"""Private SQLite snapshots, without importing or initializing the web app."""

import argparse
from contextlib import closing
import math
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import time


PROJECT_ROOT = Path(__file__).resolve().parent
REQUIRED_TABLES = frozenset((
    "users", "projects", "project_members", "tasks", "task_updates",
    "project_stages", "project_defenses", "project_assessments",
    "student_profiles", "team_invitations", "team_member_roles",
))
PUBLIC_DIRECTORIES = frozenset(("static", "public", "www", "wwwroot", "htdocs"))


class BackupError(Exception):
    """Operator-safe errors: never include records or raw SQLite diagnostics."""


def configured_database():
    # Same default and relative-path semantics as app.py, without importing it.
    return Path(os.environ.get("M_FLOW_DATABASE", str(PROJECT_ROOT / "database.db")))


def _existing_database(path):
    candidate = Path(path).resolve(strict=True)
    if not candidate.is_file():
        raise BackupError("Источник должен быть существующим файлом SQLite.")
    return candidate


def _read_only(path, timeout=5):
    # mode=ro must not be replaced with immutable=1: live WAL must be read.
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=timeout)


def _check_connection(conn):
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise BackupError("Проверка целостности не пройдена; содержимое не выводится.")
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not REQUIRED_TABLES.issubset(tables):
        raise BackupError("В файле отсутствуют обязательные таблицы M-Flow.")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise BackupError("Нарушены связи записей; автоматическое исправление запрещено.")


def verify_database(path):
    """Read-only integrity/schema/FK check; no app startup or data output."""
    with closing(_read_only(_existing_database(path))) as conn:
        _check_connection(conn)


def _private_destination(path):
    raw = Path(path).absolute()
    if os.path.lexists(raw):
        raise BackupError("Файл назначения уже существует; перезапись запрещена.")
    parent = raw.parent.resolve(strict=True)
    if not parent.is_dir():
        raise BackupError("Нужен существующий закрытый каталог назначения.")
    target = parent / raw.name
    if target == configured_database().resolve():
        raise BackupError("Восстановление в путь рабочей базы запрещено.")
    if target.is_relative_to(PROJECT_ROOT):
        raise BackupError("Пользовательские данные нельзя сохранять в проекте.")
    for directory in (parent, *parent.parents):
        if ((directory / ".git").exists()
                or directory.name.casefold() in PUBLIC_DIRECTORIES):
            raise BackupError("Копии запрещены в Git-репозиториях и публичных каталогах.")
    if os.name == "posix" and stat.S_IMODE(parent.stat().st_mode) & 0o077:
        raise BackupError("Каталог должен быть закрыт от других пользователей (chmod 700).")
    return target


def _copy_snapshot(source, destination, timeout):
    if not math.isfinite(timeout) or timeout <= 0:
        raise BackupError("Время ожидания должно быть положительным конечным числом.")
    source = _existing_database(source)
    destination = _private_destination(destination)
    if source == destination:
        raise BackupError("Источник и назначение должны различаться.")
    deadline = time.monotonic() + timeout

    def progress(status, remaining, total):
        if time.monotonic() >= deadline:
            raise BackupError("Время копирования истекло; готовая копия не опубликована.")

    # The destination appears only after a complete, checked snapshot. Hard link
    # publication is atomic and fails if another process created the name first.
    fd, temporary = tempfile.mkstemp(prefix=".mflow-snapshot-", suffix=".sqlite3",
                                     dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        with closing(_read_only(source, timeout=min(timeout, 1))) as src:
            with closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst, pages=256, progress=progress, sleep=0.05)
                _check_connection(dst)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, destination)
        return destination
    finally:
        # Only this invocation's private temporary files; never source/target.
        for suffix in ("", "-journal", "-wal", "-shm"):
            temporary.with_name(temporary.name + suffix).unlink(missing_ok=True)


def create_backup(destination, source=None, timeout=60):
    """Consistent full snapshot including all tables/indexes and committed WAL."""
    if source is None and (os.environ.get("M_FLOW_DATABASE_BACKEND") == "postgres"
                           or (os.environ.get("DATABASE_URL") and os.environ.get("M_FLOW_DATABASE_BACKEND") != "sqlite")):
        raise BackupError("Рабочая база — PostgreSQL. Для неё нужен pg_dump; SQLite-копия не заменяет её резервную копию.")
    return _copy_snapshot(configured_database() if source is None else source,
                          destination, timeout)


def restore_backup(source, destination, timeout=60):
    """Restore to a NEW private database; never replace an existing/active DB."""
    return _copy_snapshot(source, destination, timeout)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Закрытые резервные копии M-Flow (без запуска сайта).")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup", help="Создать проверенную копию")
    backup.add_argument("--source", type=Path, default=None)
    backup.add_argument("--output", type=Path, required=True)
    restore = subparsers.add_parser("restore", help="Восстановить в новый отдельный файл")
    restore.add_argument("--source", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True)
    for command in (backup, restore):
        command.add_argument("--timeout", type=float, default=60)
    verify = subparsers.add_parser("verify", help="Проверить файл без вывода данных")
    verify.add_argument("--source", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            verify_database(args.source)
        elif args.command == "backup":
            create_backup(args.output, args.source, args.timeout)
        else:
            restore_backup(args.source, args.output, args.timeout)
    except BackupError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, ValueError):
        print("Операция не выполнена. Проверьте путь, права, свободное место и формат SQLite. "
              "Подробности с пользовательскими данными не выводятся.", file=sys.stderr)
        return 1
    print("Проверка завершена." if args.command == "verify" else
          "Новый файл создан и проверен. Он содержит личные данные: храните его закрыто.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

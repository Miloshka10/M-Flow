"""Private pg_dump snapshots. Credentials are entered locally, never in argv."""

from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

import psycopg
from psycopg import sql
from database import validate_postgres_url, DatabaseUnavailable
from database_backup import BackupError, REQUIRED_TABLES, _private_destination


ROOT = Path(__file__).resolve().parent


def tool_path(name):
    bundled = ROOT / ".tools" / "postgresql-18" / "bin" / (name + ".exe")
    found = str(bundled) if bundled.is_file() else shutil.which(name)
    if not found:
        raise BackupError("Нужны клиентские утилиты PostgreSQL 18: pg_dump и pg_restore.")
    result = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=15)
    version = re.search(r"PostgreSQL\)?\s+(\d+)", result.stdout)
    if result.returncode or not version or int(version[1]) < 18:
        raise BackupError("Нужны исправные клиентские утилиты PostgreSQL версии 18 или новее.")
    return found


def connection_environment(url):
    validate_postgres_url(url)
    parsed = urlsplit(url)
    if "-pooler" in parsed.hostname:
        raise BackupError("Для копии выберите direct URL: выключите Connection pooling в Neon.")
    options = parse_qs(parsed.query)
    # Ignore inherited PG* overrides: they must not redirect to another database.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    env.update(PGHOST=parsed.hostname, PGPORT=str(parsed.port or 5432),
               PGDATABASE=unquote(parsed.path.lstrip("/")), PGUSER=unquote(parsed.username),
               PGPASSWORD=unquote(parsed.password or ""), PGSSLMODE=options.get("sslmode", ["require"])[-1],
               PGCHANNELBINDING=options.get("channel_binding", ["prefer"])[-1],
               PGCONNECT_TIMEOUT="15", PGCLIENTENCODING="UTF8")
    if not env["PGPASSWORD"]:
        raise BackupError("В direct URL отсутствует пароль; не отправляйте его в чат.")
    return env


def private_directory(base):
    base = Path(base).absolute()
    # Reject repository/public locations BEFORE creating directories there.
    resolved = base.resolve()
    if resolved.is_relative_to(ROOT):
        raise BackupError("Папка копий должна находиться вне проекта.")
    for parent in (resolved, *resolved.parents):
        if (parent / ".git").exists() or parent.name.casefold() in ("public", "static", "www", "wwwroot", "htdocs"):
            raise BackupError("Папка копий не может быть репозиторием или публичной папкой.")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = base / ("mflow-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    folder.mkdir(mode=0o700)
    if os.name == "nt":
        identity = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                   "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value"],
                                  capture_output=True, text=True, timeout=15)
        sid = identity.stdout.strip()
        if identity.returncode or not re.fullmatch(r"S-1-(?:\d+-)+\d+", sid):
            raise BackupError("Не удалось определить владельца закрытой папки.")
        protected = subprocess.run(["icacls.exe", str(folder), "/inheritance:r", "/grant:r",
                                    f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                                   capture_output=True, timeout=15)
        if protected.returncode:
            raise BackupError("Не удалось закрыть папку от других пользователей; копия не создаётся.")
    _private_destination(folder / "database.dump")
    return folder


def run_private(arguments, env=None):
    result = subprocess.run(arguments, env=env, capture_output=True, timeout=300)
    if result.returncode:
        # Do not echo raw driver stderr, archive SQL, credentials or record data.
        raise BackupError("Утилита PostgreSQL завершилась с ошибкой. Проверьте подключение, права и версию клиента. Данные и подробности ошибки не выводятся.")
    return result.stdout


def archive_check(path, restore):
    contents = run_private([restore, "--list", str(path)]).decode("utf-8", errors="replace")
    tables = {match[2] for match in re.finditer(r"\bTABLE\s+(?!DATA\b)(\S+)\s+(\S+)\s", contents)}
    if not REQUIRED_TABLES.issubset(tables):
        raise BackupError("В архиве отсутствуют обязательные таблицы M-Flow.")


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_backup(url, directory):
    env = connection_environment(url)
    dump, restore = tool_path("pg_dump"), tool_path("pg_restore")
    folder = private_directory(directory)
    destination = folder / "database.dump"
    descriptor, temporary = tempfile.mkstemp(prefix=".partial-", suffix=".dump", dir=folder)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=15,
                             application_name="M-Flow private backup") as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                available = {row[0] for row in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")}
                if not REQUIRED_TABLES.issubset(available):
                    raise BackupError("Источник не содержит всех таблиц M-Flow в public.")
                counts = {table: conn.execute(sql.SQL("SELECT COUNT(*) FROM public.{}").format(sql.Identifier(table))).fetchone()[0]
                          for table in sorted(REQUIRED_TABLES)}
                snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
                version = conn.info.server_version
                if version >= 190000:
                    raise BackupError("Для этого сервера проверьте совместимость версии pg_dump отдельно.")
                run_private([dump, "--no-password", "--format=custom", "--snapshot=" + snapshot,
                             "--file=" + str(temporary)], env)
        archive_check(temporary, restore)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        checksum = file_hash(temporary)
        os.link(temporary, destination)  # Never overwrite another file.
        manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(), server_version=version,
                        sha256=checksum, counts=counts, archive_checked=True, restore_tested=False)
        with (folder / "verification.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
        return destination
    finally:
        temporary.unlink(missing_ok=True)  # Only this call's partial file.
        env.pop("PGPASSWORD", None)


def main():
    try:
        tool_path("pg_dump")
        tool_path("pg_restore")
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise BackupError("Для скрытого ввода откройте интерактивное окно; не передавайте пароль в команду.")
        print("Neon → Connect → Connection pooling OFF. Вставьте полную direct-строку ниже.")
        print("Ввод скрыт. Строка не сохраняется в файл или историю команд.")
        url = getpass.getpass("Direct URL (ввод скрыт): ").strip()
        destination = create_backup(url, Path.home() / "MFlow-private-backups")
        print("Копия создана; состав архива проверен.")
        print("Файл: " + str(destination))
        print("Восстановление ещё НЕ проверено. Render пока не перезапускайте.")
        return 0
    except (BackupError, DatabaseUnavailable) as error:
        print(str(error), file=sys.stderr)
    except (psycopg.Error, OSError, ValueError, subprocess.SubprocessError):
        print("Копирование не завершено. Проверьте настройки подключения и доступ к файлам. Секреты и записи не выводятся.", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("Операция отменена. Render не перезапускался.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

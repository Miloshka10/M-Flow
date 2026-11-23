"""Restore only into an empty, explicitly named disposable database."""
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import re
import subprocess
import sys

import psycopg
from psycopg import sql
from database import DatabaseUnavailable
from postgres_backup import (BackupError, OPTIONAL_TABLES, REQUIRED_TABLES, ROW_HASH_FORMAT,
                             archive_check, connection_environment, file_hash, run_private,
                             table_row_hashes, tool_path)

TEST_DATABASE = "mflow_restore_check"


def test_environment(url):
    env = connection_environment(url)
    if env['PGDATABASE'] != TEST_DATABASE:
        raise BackupError("Разрешена только отдельная база mflow_restore_check. Рабочая neondb запрещена.")
    return env


def require_empty(conn):
    if conn.execute("SELECT current_database()").fetchone()[0] != TEST_DATABASE:
        raise BackupError("Сервер подключил другую базу; восстановление запрещено.")
    objects = conn.execute("""SELECT COUNT(*) FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
        AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
        AND c.relkind IN ('r','p','v','m','S','f')""").fetchone()[0]
    if objects:
        raise BackupError("Тестовая база уже содержит таблицы или другие объекты. Ничего не удалено; восстановление отменено.")


def manifest_tables(manifest):
    tables = set(manifest.get('counts', {}))
    if not REQUIRED_TABLES.issubset(tables) or not tables.issubset(REQUIRED_TABLES | OPTIONAL_TABLES):
        raise BackupError("Нет контрольных количеств для всех таблиц.")
    return tables


def manifest_row_hashes(manifest, tables):
    if 'row_sha256' not in manifest and 'row_sha256_format' not in manifest:
        return None  # Legacy archives only recorded row counts.
    hashes = manifest.get('row_sha256')
    if (manifest.get('row_sha256_format') != ROW_HASH_FORMAT or not isinstance(hashes, dict)
            or hashes.keys() != tables
            or not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                       for value in hashes.values())):
        raise BackupError("Контрольные суммы строк отсутствуют или имеют неподдерживаемый формат.")
    return hashes


def restore_check(url, archive):
    archive = Path(archive).resolve()
    allowed = (Path.home() / 'MFlow-private-backups').resolve()
    if not archive.is_relative_to(allowed) or archive.name != 'database.dump':
        raise BackupError("Выберите закрытую копию database.dump вне проекта.")
    return restore_verified_archive(url, archive)


def restore_verified_archive(url, archive):
    """Verify and restore an already selected archive to the empty test DB."""
    env = test_environment(url)
    manifest = json.loads(archive.with_name('verification.json').read_text(encoding='utf-8'))
    if manifest.get('sha256') != file_hash(archive):
        raise BackupError("Контрольная сумма копии не совпадает; восстановление отменено.")
    checked_tables = manifest_tables(manifest)
    expected_hashes = manifest_row_hashes(manifest, checked_tables)
    restore = tool_path('pg_restore')
    archive_check(archive, restore)
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=15) as conn:
            require_empty(conn)
        # No --clean or --create: never delete existing data or change database.
        run_private([restore, '--no-password', '--exit-on-error', '--single-transaction',
                     '--no-owner', '--no-privileges', '--dbname=' + TEST_DATABASE,
                     str(archive)], env)
        with psycopg.connect(url, autocommit=True, connect_timeout=15) as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                tables = {row[0] for row in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")}
                if not checked_tables.issubset(tables):
                    raise BackupError("После восстановления отсутствуют обязательные таблицы.")
                counts = {table: conn.execute(sql.SQL('SELECT COUNT(*) FROM public.{}').format(sql.Identifier(table))).fetchone()[0]
                          for table in sorted(checked_tables)}
                if counts != manifest['counts']:
                    raise BackupError("Количество записей не совпало с копией; проверка не пройдена.")
                if expected_hashes is not None and table_row_hashes(conn, checked_tables) != expected_hashes:
                    raise BackupError("Содержимое записей отличается от исходной базы; проверка не пройдена.")
                invalid = conn.execute("SELECT COUNT(*) FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated").fetchone()[0]
                if invalid:
                    raise BackupError("Найдены непроверенные ограничения; проверка не пройдена.")
        receipt = dict(verified_utc=datetime.now(timezone.utc).isoformat(), sha256=manifest['sha256'],
                       restore_tested=True, counts_match=True, constraints_valid=True,
                       row_contents_match=True if expected_hashes is not None else None,
                       application_scenarios_tested=False)
        with archive.with_name('restore_verified.json').open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, indent=2)
        return receipt
    finally:
        env.pop('PGPASSWORD', None)


def main():
    try:
        if len(sys.argv) != 2 or not sys.stdin.isatty() or not sys.stderr.isatty():
            raise BackupError("Нужны интерактивное окно и путь к закрытой копии. Пароль нельзя передавать в команду.")
        print('Вставьте direct URL тестовой ветки mflow-restore-check-20261009.')
        print('Database: mflow_restore_check. Connection pooling OFF. Ввод скрыт.')
        url = getpass.getpass('Тестовый direct URL: ').strip()
        print('Проверка и восстановление... Пожалуйста, подождите.')
        receipt = restore_check(url, sys.argv[1])
        print('Восстановление проверено: таблицы и количества записей совпали с копией.')
        if receipt['row_contents_match']:
            print('Содержимое записей совпало с исходным снимком.')
        else:
            print('В старой копии есть только контроль количества записей. Содержимое не сверялось.')
        print('Рабочая neondb и Render не изменены. Сценарии сайта на копии ещё не проверены.')
        return 0
    except (BackupError, DatabaseUnavailable) as error:
        print(str(error), file=sys.stderr)
    except (psycopg.Error, OSError, ValueError, subprocess.SubprocessError):
        print('Проверка не завершена. Не присылайте URL или пароль. Рабочую базу не перезапускайте.', file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print('Проверка отменена.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

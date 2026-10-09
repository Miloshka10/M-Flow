"""Restore only into an empty, explicitly named disposable database."""
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import subprocess
import sys

import psycopg
from psycopg import sql
from database import DatabaseUnavailable
from postgres_backup import (BackupError, REQUIRED_TABLES, archive_check,
                             connection_environment, file_hash, run_private, tool_path)

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


def restore_check(url, archive):
    env = test_environment(url)
    archive = Path(archive).resolve()
    allowed = (Path.home() / 'MFlow-private-backups').resolve()
    if not archive.is_relative_to(allowed) or archive.name != 'database.dump':
        raise BackupError("Выберите закрытую копию database.dump вне проекта.")
    manifest = json.loads(archive.with_name('verification.json').read_text(encoding='utf-8'))
    if manifest.get('sha256') != file_hash(archive):
        raise BackupError("Контрольная сумма копии не совпадает; восстановление отменено.")
    if set(manifest.get('counts', {})) != REQUIRED_TABLES:
        raise BackupError("Нет контрольных количеств для всех таблиц.")
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
                if not REQUIRED_TABLES.issubset(tables):
                    raise BackupError("После восстановления отсутствуют обязательные таблицы.")
                counts = {table: conn.execute(sql.SQL('SELECT COUNT(*) FROM public.{}').format(sql.Identifier(table))).fetchone()[0]
                          for table in sorted(REQUIRED_TABLES)}
                if counts != manifest['counts']:
                    raise BackupError("Количество записей не совпало с копией; проверка не пройдена.")
                invalid = conn.execute("SELECT COUNT(*) FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated").fetchone()[0]
                if invalid:
                    raise BackupError("Найдены непроверенные ограничения; проверка не пройдена.")
        receipt = dict(verified_utc=datetime.now(timezone.utc).isoformat(), sha256=manifest['sha256'],
                       restore_tested=True, counts_match=True, constraints_valid=True,
                       application_scenarios_tested=False)
        with archive.with_name('restore_verified.json').open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, indent=2)
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
        restore_check(url, sys.argv[1])
        print('Восстановление проверено: таблицы и количества записей совпали с копией.')
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

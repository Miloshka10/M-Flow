"""Read-only account-name audit on an in-memory copy, without importing app.py.

Only aggregate counts leave memory; no names, emails, passwords or hashes.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from account_names import name_audit


def audit_sqlite(path):
    source = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        copy = sqlite3.connect(':memory:')
        try:
            db.backup(copy)
            copy.row_factory = sqlite3.Row
            copy.execute('PRAGMA trusted_schema=OFF')
            copy.execute('PRAGMA query_only=ON')
            if not copy.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
                return dict(accounts_table=False)
            return dict(accounts_table=True, **name_audit(copy))
        finally:
            copy.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite', required=True, help='Existing local SQLite file; read-only, no application startup')
    args = parser.parse_args()
    try:
        print(json.dumps(audit_sqlite(args.sqlite)))
    except (OSError, sqlite3.Error):
        parser.exit(1, 'Аудит не выполнен; содержимое и путь базы не выводятся.\n')

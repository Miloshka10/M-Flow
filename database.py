"""Explicit SQLite/PostgreSQL selection; no fallback after a PostgreSQL failure."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit

try:
    import psycopg
except ImportError:
    psycopg = None


INTEGRITY_ERRORS = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())
SQL_TOKENS = re.compile(r"'[^']*(?:''[^']*)*'|\"[^\"]*(?:\"\"[^\"]*)*\"|--[^\n]*|/\*.*?\*/|\?|;", re.S)


class DatabaseUnavailable(RuntimeError):
    """Safe public diagnostic, without credentials or driver connection details."""


@dataclass(frozen=True)
class DatabaseSettings:
    backend: str
    path: str
    url: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls, root):
        url = os.environ.get("DATABASE_URL", "").strip()
        backend = os.environ.get("M_FLOW_DATABASE_BACKEND", "postgres" if url else "sqlite").strip().lower()
        if backend not in ("sqlite", "postgres"):
            raise DatabaseUnavailable("M_FLOW_DATABASE_BACKEND должна быть sqlite или postgres.")
        if backend == "postgres":
            if not url:
                raise DatabaseUnavailable("Для PostgreSQL задайте DATABASE_URL в закрытых настройках сервиса.")
            validate_postgres_url(url)
        return cls(backend, os.environ.get("M_FLOW_DATABASE", str(Path(root) / "database.db")), url)


def validate_postgres_url(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname
                or not parsed.username or not parsed.path.strip("/") or not (parsed.port or 5432)):
            raise ValueError()
        ssl = parse_qs(parsed.query).get("sslmode", [""])[-1]
        if parsed.hostname not in ("localhost", "127.0.0.1", "::1") and ssl not in ("require", "verify-ca", "verify-full"):
            raise ValueError()
    except ValueError:
        raise DatabaseUnavailable("Некорректная DATABASE_URL. Для внешней базы требуется TLS (sslmode=require или verify-full).") from None


class Record:
    """sqlite.Row-compatible named/positional access for existing view code."""
    def __init__(self, names, values):
        self.names, self.values = names, tuple(values)
        self.positions = {name: index for index, name in enumerate(names)}

    def keys(self):
        return self.names

    def __getitem__(self, key):
        return self.values[self.positions[key]] if isinstance(key, str) else self.values[key]

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)


def record_factory(cursor):
    names = tuple(column.name for column in cursor.description) if cursor.description else ()
    return lambda values: Record(names, values)


def postgres_parameters(sql):
    """Translate only qmark placeholders outside literals/comments; bind values separately."""
    output, previous = [], 0
    for token in SQL_TOKENS.finditer(sql):
        output.append(sql[previous:token.start()].replace("%", "%%"))
        part = token.group()
        output.append("%s" if part == "?" else part.replace("%", "%%"))
        previous = token.end()
    output.append(sql[previous:].replace("%", "%%"))
    return "".join(output)


def statements(script):
    previous = 0
    for token in SQL_TOKENS.finditer(script):
        if token.group() == ";":
            if script[previous:token.start()].strip():
                yield script[previous:token.start()]
            previous = token.end()
    if script[previous:].strip():
        yield script[previous:]


class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection
        self.trace = None

    def execute(self, sql, parameters=None):
        if self.trace:
            self.trace(sql)  # Never interpolate user values into diagnostics.
        query = postgres_parameters(sql) if parameters is not None else sql
        return self.connection.execute(query, parameters)

    def executemany(self, sql, parameters):
        cursor = self.connection.cursor()
        cursor.executemany(postgres_parameters(sql), parameters)
        return cursor

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        try:
            self.connection.rollback()  # End read or failed transactions before pool return.
        finally:
            self.connection.close()

    def set_trace_callback(self, callback):
        self.trace = callback


def connect_database(settings):
    if settings.backend == "sqlite":
        conn = sqlite3.connect(settings.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    if psycopg is None:
        raise DatabaseUnavailable("Установите зависимости проекта для работы с PostgreSQL.")
    try:
        return PostgresConnection(psycopg.connect(settings.url, row_factory=record_factory,
                                  connect_timeout=10, application_name="M-Flow", prepare_threshold=None))
    except psycopg.Error:
        raise DatabaseUnavailable("PostgreSQL недоступна. Проверьте настройки подключения в панели хостинга. SQLite не используется вместо неё.") from None


def execute_schema(conn, sql):
    if isinstance(conn, PostgresConnection):
        # Only our version-controlled DDL, not arbitrary application queries.
        sql = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", sql)
        sql = re.sub(r"\bINTEGER\b", "BIGINT", sql)
    return conn.execute(sql)


def initialization_settings(settings):
    if settings.backend != "postgres":
        return settings
    url = os.environ.get("DATABASE_URL_UNPOOLED", "").strip() or settings.url
    validate_postgres_url(url)
    if "-pooler" in urlsplit(url).hostname:
        raise DatabaseUnavailable("Для создания схемы задайте прямую DATABASE_URL_UNPOOLED без -pooler.")
    return DatabaseSettings("postgres", settings.path, url)


def execute_schema_script(conn, script):
    if isinstance(conn, PostgresConnection):
        for statement in statements(script):
            execute_schema(conn, statement)
    else:
        conn.executescript(script)


def table_columns(conn, table):
    if table not in ("projects", "tasks"):
        raise ValueError("Неподдерживаемая таблица схемы.")
    if isinstance(conn, PostgresConnection):
        rows = conn.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?", (table,))
    else:
        rows = conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in rows}


def insert_id(conn, sql, parameters):
    if isinstance(conn, PostgresConnection):
        return conn.execute(sql.rstrip().rstrip(";") + " RETURNING id", parameters).fetchone()["id"]
    return conn.execute(sql, parameters).lastrowid


def begin_project_write(conn, project_id):
    if isinstance(conn, PostgresConnection):
        # Lock the existing project even if the stage/defense row doesn't exist yet.
        conn.execute("SELECT id FROM projects WHERE id=? FOR UPDATE", (project_id,))
    else:
        conn.execute("BEGIN IMMEDIATE")


def begin_invitation_write(conn, invitation_id, user_id):
    if isinstance(conn, PostgresConnection):
        # Match project -> invitation lock order used by invite/legacy transfer.
        conn.execute("SELECT p.id FROM projects p JOIN team_invitations i ON i.project_id=p.id WHERE i.id=? AND i.invitee_id=? FOR UPDATE OF p", (invitation_id, user_id))
    else:
        conn.execute("BEGIN IMMEDIATE")


def form_database_id(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = int(value)
        if not 0 < number <= 9223372036854775807:
            raise ValueError()
        return number
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Выберите корректный номер проекта или ученика.") from None

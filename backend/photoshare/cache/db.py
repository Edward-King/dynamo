"""
SQLite connection management for cache_root/metadata.db.

Applies the pragmas specified in the SQL DDL Design document and the
idempotent schema migration (CREATE TABLE IF NOT EXISTS) on every startup.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _configure_connection(conn: sqlite3.Connection, busy_timeout_ms: int) -> None:
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)};")
    conn.row_factory = sqlite3.Row


def get_connection(db_path: str | Path, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    """Opens a new SQLite connection with pragmas applied. Caller manages lifecycle."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _configure_connection(conn, busy_timeout_ms)
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """Idempotently creates all tables/indexes if they don't already exist."""
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


class Database:
    """
    Thin wrapper owning the single SQLite connection for the service
    lifetime. Passed into repositories/services via dependency injection
    (see photoshare.api.dependencies).
    """

    def __init__(self, db_path: str | Path, busy_timeout_ms: int = 5000) -> None:
        self.db_path = Path(db_path)
        self.conn = get_connection(self.db_path, busy_timeout_ms)
        apply_schema(self.conn)

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self.conn.cursor()
        try:
            yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()

    def close(self) -> None:
        self.conn.close()

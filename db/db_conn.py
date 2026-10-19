"""Persistence for stream keys using the existing ``transmisiones`` schema."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent.parent / "statics" / "data.db"


class TransmissionRepository:
    """Read and save stream keys without keeping a database connection open."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = (
            Path(database_path) if database_path is not None else DEFAULT_DATABASE_PATH
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            create_table(connection.cursor())
            yield connection

    def load_keys(self) -> dict[str, str]:
        """Load saved keys, favoring the newest entry for legacy duplicates."""
        with self._connection() as connection:
            rows = connection.execute("SELECT plataforma, key FROM transmisiones ORDER BY id")
            return dict(rows)

    def save_keys(self, keys: Mapping[str, str]) -> None:
        """Save all supplied keys atomically; retain platforms not supplied."""
        with self._connection() as connection:
            cursor = connection.cursor()
            for platform, key in keys.items():
                insert_transmision(cursor, platform, key)


def connect_sqlite(
    database_path: str | Path | None = None,
) -> tuple[sqlite3.Connection, sqlite3.Cursor]:
    """Legacy API: the caller owns committing and closing this connection."""
    path = Path(database_path) if database_path is not None else DEFAULT_DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    return connection, connection.cursor()


def create_table(cursor: sqlite3.Cursor) -> None:
    """Create the original schema without migrating existing saved keys."""
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS transmisiones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plataforma TEXT NOT NULL,
            key TEXT NOT NULL
        )"""
    )


def get_transmisiones(cursor: sqlite3.Cursor) -> list[tuple[int, str, str]]:
    """Return rows in insertion order for callers using the legacy API."""
    cursor.execute("SELECT id, plataforma, key FROM transmisiones ORDER BY id")
    return cursor.fetchall()


def insert_transmision(cursor: sqlite3.Cursor, plataforma: str, key: str) -> None:
    """Replace a platform key inside the caller's transaction."""
    cursor.execute("DELETE FROM transmisiones WHERE plataforma = ?", (plataforma,))
    cursor.execute("INSERT INTO transmisiones (plataforma, key) VALUES (?, ?)", (plataforma, key))

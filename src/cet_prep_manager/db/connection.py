"""SQLite connection management for CET Prep Manager."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3

DEFAULT_DB_FILENAME = "cet_prep.db"


def get_default_db_path() -> Path:
    """Return the default database path.

    Can be overridden by the CETPM_DB_PATH environment variable.
    Defaults to 'data/cet_prep.db' relative to the current working directory.
    """
    env_path = os.getenv("CETPM_DB_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return (Path.cwd() / "data" / DEFAULT_DB_FILENAME).resolve()


def get_connection(
    db_path: Path | str | None = None,
    *,
    enforce_foreign_keys: bool = True,
    timeout: float = 5.0,
) -> sqlite3.Connection:
    """Open and configure a SQLite connection.

    Args:
        db_path: Path to database file or ':memory:'. Defaults to get_default_db_path().
        enforce_foreign_keys: Whether to execute 'PRAGMA foreign_keys = ON;'. Defaults to True.
        timeout: Timeout in seconds for SQLite busy handler. Defaults to 5.0.

    Returns:
        Configured sqlite3.Connection instance with row_factory set to sqlite3.Row.
    """
    if db_path is None:
        target_path = str(get_default_db_path())
    else:
        target_path = str(db_path)

    # If it's a file path, ensure the parent directory exists
    if target_path != ":memory:":
        file_path = Path(target_path).expanduser().resolve()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        target_path = str(file_path)

    conn = sqlite3.connect(target_path, timeout=timeout)
    conn.row_factory = sqlite3.Row

    if enforce_foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON;")

    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)};")
    return conn


def verify_foreign_keys(conn: sqlite3.Connection) -> bool:
    """Check if foreign key enforcement is active on the given connection."""
    cursor = conn.execute("PRAGMA foreign_keys;")
    row = cursor.fetchone()
    if row is None:
        return False
    return bool(row[0])

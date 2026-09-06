"""Database migration runner and schema initialization for CET Prep Manager."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from typing import Any

from cet_prep_manager.db.connection import get_connection

MIGRATION_FILE_PATTERN = re.compile(r"^(\d+)_(.+)\.sql$")


@dataclass(frozen=True)
class Migration:
    """Represents a discovered database migration."""

    version: int
    name: str
    path: Path


def get_migrations_dir(override: Path | str | None = None) -> Path:
    """Locate the directory containing SQL migration files."""
    if override is not None:
        p = Path(override).resolve()
        if not p.is_dir():
            raise FileNotFoundError(f"Migrations directory does not exist: {p}")
        return p

    # 1. Try repo root sql/ directory
    repo_sql = Path(__file__).resolve().parents[3] / "sql"
    if repo_sql.is_dir() and any(repo_sql.glob("*.sql")):
        return repo_sql

    # 2. Try package-internal sql/ directory
    pkg_sql = Path(__file__).resolve().parent.parent / "sql"
    if pkg_sql.is_dir() and any(pkg_sql.glob("*.sql")):
        return pkg_sql

    raise FileNotFoundError("Could not find SQL migrations directory.")


def discover_migrations(migrations_dir: Path | str | None = None) -> list[Migration]:
    """Scan and return all migration files sorted by version number."""
    m_dir = get_migrations_dir(migrations_dir)
    migrations: list[Migration] = []
    seen_versions: dict[int, Path] = {}

    for file_path in m_dir.glob("*.sql"):
        match = MIGRATION_FILE_PATTERN.match(file_path.name)
        if not match:
            continue
        version = int(match.group(1))
        name = match.group(2)
        if version in seen_versions:
            raise ValueError(
                f"Duplicate migration version {version}: {file_path.name} and {seen_versions[version].name}"
            )
        seen_versions[version] = file_path
        migrations.append(Migration(version=version, name=name, path=file_path))

    migrations.sort(key=lambda m: m.version)
    return migrations


def ensure_migration_tables(conn: sqlite3.Connection) -> None:
    """Create schema metadata and migration tracking tables if they do not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version from schema_meta table.

    Returns 0 if schema_meta does not exist or has no schema_version entry.
    """
    cursor = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='schema_meta';"
    )
    if cursor.fetchone()[0] == 0:
        return 0

    cursor = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version';"
    )
    row = cursor.fetchone()
    if row is None:
        return 0
    try:
        return int(row[0])
    except (ValueError, TypeError):
        return 0


def get_applied_migrations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return a list of applied migrations ordered by version."""
    cursor = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations';"
    )
    if cursor.fetchone()[0] == 0:
        return []

    cursor = conn.execute(
        "SELECT version, name, applied_at FROM schema_migrations ORDER BY version ASC;"
    )
    return [
        {
            "version": row["version"],
            "name": row["name"],
            "applied_at": row["applied_at"],
        }
        for row in cursor.fetchall()
    ]


def run_migrations(
    conn: sqlite3.Connection,
    migrations_dir: Path | str | None = None,
) -> list[int]:
    """Apply any pending migrations to the database.

    Args:
        conn: Open SQLite connection.
        migrations_dir: Optional path to migrations directory.

    Returns:
        List of newly applied migration versions.
    """
    ensure_migration_tables(conn)
    migrations = discover_migrations(migrations_dir)

    cursor = conn.execute("SELECT version FROM schema_migrations;")
    applied_versions = {row[0] for row in cursor.fetchall()}

    newly_applied: list[int] = []

    for migration in migrations:
        if migration.version in applied_versions:
            continue

        sql_text = migration.path.read_text(encoding="utf-8")
        conn.executescript(sql_text)

        applied_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO schema_migrations (version, name, applied_at)
            VALUES (?, ?, ?);
            """,
            (migration.version, migration.path.name, applied_at),
        )
        conn.execute(
            """
            INSERT INTO schema_meta (key, value)
            VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """,
            (str(migration.version),),
        )
        conn.commit()
        newly_applied.append(migration.version)

    return newly_applied


def init_db(
    target: Path | str | sqlite3.Connection | None = None,
    migrations_dir: Path | str | None = None,
) -> int:
    """Initialize the database and apply all pending migrations.

    Args:
        target: Database file path, ':memory:', None (for default path), or existing Connection.
        migrations_dir: Optional path to migrations directory.

    Returns:
        The current schema version after running migrations.
    """
    if isinstance(target, sqlite3.Connection):
        run_migrations(target, migrations_dir=migrations_dir)
        return get_schema_version(target)

    conn = get_connection(target)
    try:
        run_migrations(conn, migrations_dir=migrations_dir)
        return get_schema_version(conn)
    finally:
        conn.close()


def create_in_memory_db(
    migrations_dir: Path | str | None = None,
) -> sqlite3.Connection:
    """Create an open in-memory database with all migrations applied.

    Useful for tests and ephemeral sessions.
    """
    conn = get_connection(":memory:")
    run_migrations(conn, migrations_dir=migrations_dir)
    return conn

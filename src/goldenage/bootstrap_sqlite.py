"""Bootstrap utilities for a local SQLite-backed GoldenAge instance."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from goldenage.config import load_settings
from goldenage.resource_paths import SchemaPath, migration_paths, read_resource_text


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Bootstrap a SQLite database for GoldenAge.")
    parser.add_argument(
        "--path",
        default=None,
        help="SQLite database path. Defaults to GOLDENAGE_SQLITE_PATH from the environment or .env.",
    )
    parser.add_argument(
        "--schema-path",
        default=None,
        type=Path,
        help=(
            "Path to a SQLite migration directory or a single schema SQL file. "
            "Defaults to migrations bundled in the installed package."
        ),
    )
    args = parser.parse_args()

    settings = load_settings()
    raw_path = args.path or (str(settings.sqlite_path) if settings.sqlite_path else None)
    if raw_path is None:
        raise SystemExit("GOLDENAGE_SQLITE_PATH is not configured.")

    ensure_sqlite_bootstrapped(Path(raw_path), args.schema_path)


def ensure_sqlite_bootstrapped(database_path: Path, schema_path: Path | None = None) -> None:
    """Create the SQLite database and apply migrations once."""
    target = database_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)
    sql_paths = _schema_paths(schema_path)

    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migration (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for sql_path in sql_paths:
            row = connection.execute(
                "SELECT 1 FROM schema_migration WHERE name = ?",
                (sql_path.name,),
            ).fetchone()
            if row is not None:
                continue
            connection.executescript(read_resource_text(sql_path))
            connection.execute(
                "INSERT INTO schema_migration (name) VALUES (?)",
                (sql_path.name,),
            )
        connection.commit()


def _schema_paths(schema_path: Path | None = None) -> list[SchemaPath]:
    """Return SQLite migrations from an override or bundled package resources."""
    return migration_paths(schema_path, "sqlite")


if __name__ == "__main__":  # pragma: no cover
    main()

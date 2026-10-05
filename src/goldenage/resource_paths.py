"""Access paths for resources shipped with the GoldenAge package."""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import cast

SchemaPath = Path | Traversable


def bundled_migration_directory(dialect: str) -> Traversable:
    """Return the bundled migration directory for a database dialect."""
    if dialect == "postgres":
        parts = ("resources", "sql")
    elif dialect == "sqlite":
        parts = ("resources", "sql", "sqlite")
    else:
        raise ValueError(f"Unsupported database dialect: {dialect}")
    return files("goldenage").joinpath(*parts)


def migration_paths(schema_path: Path | None, dialect: str) -> list[SchemaPath]:
    """Return sorted migration paths from an override or bundled resources."""
    if schema_path is None:
        directory = bundled_migration_directory(dialect)
        return sorted(
            (path for path in directory.iterdir() if path.is_file() and path.name.endswith(".sql")),
            key=lambda path: path.name,
        )
    if schema_path.is_dir():
        return cast(list[SchemaPath], sorted(schema_path.glob("*.sql")))
    return [schema_path]


def read_resource_text(path: SchemaPath) -> str:
    """Read a filesystem or importlib resource path as UTF-8 text."""
    return path.read_text(encoding="utf-8")

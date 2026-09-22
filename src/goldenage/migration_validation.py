"""Validate the migration filename contract without changing applied migrations."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

MIGRATION_FILENAME = re.compile(r"(?P<id>[0-9]{4})_(?P<description>[a-z][a-z0-9_]*)\.sql$")


@dataclass(frozen=True, slots=True)
class MigrationNamespace:
    """The immutable historical baseline for one database dialect."""

    name: str
    directory: Path
    historical_names: frozenset[str]
    highest_historical_id: int


POSTGRES_NAMESPACE = MigrationNamespace(
    name="PostgreSQL",
    directory=Path("sql"),
    historical_names=frozenset(
        {
            "0001_initial.sql",
            "0002_artifact_mail_metadata.sql",
            "0003_apple_mail_import.sql",
            "0003_mailbox_ingestion.sql",
        }
    ),
    highest_historical_id=3,
)

SQLITE_NAMESPACE = MigrationNamespace(
    name="SQLite",
    directory=Path("sql/sqlite"),
    historical_names=frozenset(
        {
            "0001_initial.sql",
            "0002_apple_mail_import.sql",
            "0002_mailbox_ingestion.sql",
        }
    ),
    highest_historical_id=2,
)

NAMESPACES = (POSTGRES_NAMESPACE, SQLITE_NAMESPACE)


class MigrationValidationError(ValueError):
    """Raised when migration filenames do not comply with the repository contract."""


def validate_migration_directory(directory: Path, namespace: MigrationNamespace) -> None:
    """Validate one dialect's historical baseline and any subsequent migrations."""
    paths = sorted(path for path in directory.iterdir() if path.is_file())
    names = {path.name for path in paths}
    errors: list[str] = []

    missing_historical = namespace.historical_names - names
    for name in sorted(missing_historical):
        errors.append(f"{namespace.name}: missing historical migration {name}")

    new_ids: dict[int, str] = {}
    for path in paths:
        if path.name in namespace.historical_names:
            continue

        match = MIGRATION_FILENAME.fullmatch(path.name)
        if match is None:
            errors.append(
                f"{namespace.name}: invalid migration filename {path.name}; "
                "expected NNNN_lowercase_description.sql"
            )
            continue

        migration_id = int(match.group("id"))
        if migration_id <= namespace.highest_historical_id:
            errors.append(
                f"{namespace.name}: new migration {path.name} sorts before or within the "
                f"historical baseline ending at {namespace.highest_historical_id:04d}"
            )
            continue

        existing_name = new_ids.get(migration_id)
        if existing_name is not None:
            errors.append(
                f"{namespace.name}: duplicate new migration ID {migration_id:04d} in "
                f"{existing_name} and {path.name}"
            )
            continue
        new_ids[migration_id] = path.name

    if errors:
        raise MigrationValidationError("\n".join(errors))


def validate_repository_migrations(repository_root: Path) -> None:
    """Validate both database-specific migration namespaces in a repository."""
    for namespace in NAMESPACES:
        validate_migration_directory(repository_root / namespace.directory, namespace)


def main() -> None:
    """Run the migration filename quality check for the current repository."""
    parser = argparse.ArgumentParser(description="Validate GoldenAge migration filenames.")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing sql/. Defaults to the current directory.",
    )
    args = parser.parse_args()

    try:
        validate_repository_migrations(args.repository_root)
    except MigrationValidationError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":  # pragma: no cover
    main()

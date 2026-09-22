from pathlib import Path

import pytest

from goldenage.migration_validation import (
    MigrationNamespace,
    MigrationValidationError,
    validate_migration_directory,
    validate_repository_migrations,
)


def _namespace() -> MigrationNamespace:
    return MigrationNamespace(
        name="Test database",
        directory=Path("migrations"),
        historical_names=frozenset(
            {
                "0001_initial.sql",
                "0002_first_historical_change.sql",
                "0002_second_historical_change.sql",
            }
        ),
        highest_historical_id=2,
    )


def _historical_migrations(tmp_path: Path, namespace: MigrationNamespace) -> None:
    for name in namespace.historical_names:
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")


def test_validator_allows_exact_historical_names_and_a_new_ordered_migration(tmp_path) -> None:
    namespace = _namespace()
    _historical_migrations(tmp_path, namespace)
    (tmp_path / "0003_add_case_status.sql").write_text("SELECT 1;", encoding="utf-8")

    validate_migration_directory(tmp_path, namespace)


def test_validator_rejects_invalid_new_migration_filename(tmp_path) -> None:
    namespace = _namespace()
    _historical_migrations(tmp_path, namespace)
    (tmp_path / "0003-AddCaseStatus.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationValidationError, match="invalid migration filename"):
        validate_migration_directory(tmp_path, namespace)


def test_validator_rejects_duplicate_new_migration_ids(tmp_path) -> None:
    namespace = _namespace()
    _historical_migrations(tmp_path, namespace)
    (tmp_path / "0003_add_case_status.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0003_add_case_owner.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationValidationError, match="duplicate new migration ID 0003"):
        validate_migration_directory(tmp_path, namespace)


def test_validator_rejects_a_new_migration_with_a_historical_id(tmp_path) -> None:
    namespace = _namespace()
    _historical_migrations(tmp_path, namespace)
    (tmp_path / "0002_new_collision.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(
        MigrationValidationError, match="sorts before or within the historical baseline"
    ):
        validate_migration_directory(tmp_path, namespace)


def test_validator_requires_the_exact_historical_baseline(tmp_path) -> None:
    namespace = _namespace()
    (tmp_path / "0001_initial.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(
        MigrationValidationError,
        match="missing historical migration 0002_first_historical_change.sql",
    ):
        validate_migration_directory(tmp_path, namespace)


def test_repository_migrations_match_the_historical_baseline() -> None:
    repository_root = Path(__file__).parents[1]

    validate_repository_migrations(repository_root)

from pathlib import Path

import pytest

from goldenage import bootstrap_postgres


class FakeCursor:
    def __init__(self, migration_rows: list[object | None] | None = None) -> None:
        self.migration_rows = migration_rows or []
        self.executed: list[tuple[str, dict[str, object] | None]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, object] | None = None) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> object | None:
        return self.migration_rows.pop(0) if self.migration_rows else None


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.commits = 0

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1


def test_schema_paths_returns_sorted_directory_sql_and_single_file(tmp_path) -> None:
    second = tmp_path / "002_second.sql"
    first = tmp_path / "001_first.sql"
    ignored = tmp_path / "README.md"
    second.write_text("SELECT 2", encoding="utf-8")
    first.write_text("SELECT 1", encoding="utf-8")
    ignored.write_text("ignored", encoding="utf-8")

    assert bootstrap_postgres._schema_paths(tmp_path) == [first, second]
    assert bootstrap_postgres._schema_paths(first) == [first]


def test_apply_schema_skips_applied_migrations_and_records_new_ones(tmp_path, monkeypatch) -> None:
    applied = tmp_path / "001_applied.sql"
    pending = tmp_path / "002_pending.sql"
    applied.write_text("CREATE TABLE applied_test(id int)", encoding="utf-8")
    pending.write_text("CREATE TABLE pending_test(id int)", encoding="utf-8")
    cursor = FakeCursor(migration_rows=[{"one": 1}, None])
    connection = FakeConnection(cursor)
    monkeypatch.setattr(
        bootstrap_postgres.psycopg,
        "connect",
        lambda dsn: connection,
    )

    bootstrap_postgres.apply_schema("postgresql://example", tmp_path)

    executed_sql = [sql for sql, params in cursor.executed]
    inserted_names = [params["name"] for sql, params in cursor.executed if sql.startswith("INSERT")]  # ty:ignore[not-subscriptable]
    assert "CREATE TABLE applied_test(id int)" not in executed_sql
    assert "CREATE TABLE pending_test(id int)" in executed_sql
    assert inserted_names == ["002_pending.sql"]
    assert connection.commits == 1


def test_seed_demo_data_upserts_user_cases_and_activities(monkeypatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    monkeypatch.setattr(
        bootstrap_postgres.psycopg,
        "connect",
        lambda dsn: connection,
    )

    bootstrap_postgres.seed_demo_data("postgresql://example")

    executed_sql = [sql for sql, params in cursor.executed]
    assert any("INSERT INTO app_user" in sql for sql in executed_sql)
    assert any("INSERT INTO case_file" in sql for sql in executed_sql)
    assert any("INSERT INTO activity" in sql for sql in executed_sql)
    assert connection.commits == 1


def test_main_requires_database_url(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap_postgres,
        "load_settings",
        lambda: type("Settings", (), {"database_url": None})(),
    )
    monkeypatch.setattr("sys.argv", ["bootstrap_postgres"])

    with pytest.raises(SystemExit, match="DATABASE_URL is not configured"):
        bootstrap_postgres.main()


def test_main_applies_schema_and_optional_seed(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str | Path]] = []
    monkeypatch.setattr(
        bootstrap_postgres,
        "load_settings",
        lambda: type("Settings", (), {"database_url": "postgresql://from-env"})(),
    )
    monkeypatch.setattr(
        bootstrap_postgres,
        "apply_schema",
        lambda dsn, schema_path: calls.append(("schema", dsn, schema_path)),  # ty:ignore[invalid-argument-type]
    )
    monkeypatch.setattr(
        bootstrap_postgres,
        "seed_demo_data",
        lambda dsn: calls.append(("seed", dsn)),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["bootstrap_postgres", "--schema-path", str(tmp_path), "--seed-demo"],
    )

    bootstrap_postgres.main()

    assert calls == [
        ("schema", "postgresql://from-env", tmp_path),
        ("seed", "postgresql://from-env"),
    ]

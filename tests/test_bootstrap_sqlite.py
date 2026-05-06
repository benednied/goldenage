import sqlite3
from pathlib import Path

import pytest

from goldenage import bootstrap_sqlite


def test_schema_paths_returns_sorted_directory_sql_and_single_file(tmp_path) -> None:
    second = tmp_path / "002_second.sql"
    first = tmp_path / "001_first.sql"
    ignored = tmp_path / "README.md"
    second.write_text("CREATE TABLE second(id integer);", encoding="utf-8")
    first.write_text("CREATE TABLE first(id integer);", encoding="utf-8")
    ignored.write_text("ignored", encoding="utf-8")

    assert bootstrap_sqlite._schema_paths(tmp_path) == [first, second]
    assert bootstrap_sqlite._schema_paths(first) == [first]


def test_ensure_sqlite_bootstrapped_applies_each_migration_once(tmp_path) -> None:
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    first = schema_dir / "001_first.sql"
    second = schema_dir / "002_second.sql"
    first.write_text("CREATE TABLE first_table(id integer primary key);", encoding="utf-8")
    second.write_text("CREATE TABLE second_table(id integer primary key);", encoding="utf-8")
    database_path = tmp_path / "nested" / "goldenage.sqlite3"

    bootstrap_sqlite.ensure_sqlite_bootstrapped(database_path, schema_dir)
    bootstrap_sqlite.ensure_sqlite_bootstrapped(database_path, schema_dir)

    with sqlite3.connect(database_path) as connection:
        migration_names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM schema_migration ORDER BY name"
            ).fetchall()
        ]
        assert migration_names == ["001_first.sql", "002_second.sql"]
        assert connection.execute("SELECT COUNT(*) FROM first_table").fetchone() == (0,)


def test_main_requires_sqlite_path(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap_sqlite,
        "load_settings",
        lambda: type("Settings", (), {"sqlite_path": None})(),
    )
    monkeypatch.setattr("sys.argv", ["bootstrap_sqlite"])

    with pytest.raises(SystemExit, match="GOLDENAGE_SQLITE_PATH is not configured"):
        bootstrap_sqlite.main()


def test_main_uses_cli_path_before_environment_path(monkeypatch, tmp_path) -> None:
    calls: list[tuple[Path, Path]] = []
    env_path = tmp_path / "env.sqlite3"
    cli_path = tmp_path / "cli.sqlite3"
    schema_path = tmp_path / "schema.sql"
    monkeypatch.setattr(
        bootstrap_sqlite,
        "load_settings",
        lambda: type("Settings", (), {"sqlite_path": env_path})(),
    )
    monkeypatch.setattr(
        bootstrap_sqlite,
        "ensure_sqlite_bootstrapped",
        lambda database_path, schema: calls.append((database_path, schema)),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "bootstrap_sqlite",
            "--path",
            str(cli_path),
            "--schema-path",
            str(schema_path),
        ],
    )

    bootstrap_sqlite.main()

    assert calls == [(cli_path, schema_path)]

"""Bootstrap utilities for a local PostgreSQL-backed GoldenAge instance."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from goldenage.adapters.demo import build_demo_state
from goldenage.config import load_settings

try:
    import psycopg
except ModuleNotFoundError as exc:  # pragma: no cover - import guard for incomplete envs.
    raise RuntimeError("psycopg must be installed to bootstrap PostgreSQL.") from exc


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Bootstrap a PostgreSQL database for GoldenAge.")
    parser.add_argument(
        "--dsn",
        default=None,
        help="PostgreSQL DSN. Defaults to DATABASE_URL from the environment or .env.",
    )
    parser.add_argument(
        "--schema-path",
        default="sql",
        help="Path to a SQL migration directory or a single schema SQL file.",
    )
    parser.add_argument(
        "--seed-demo",
        action="store_true",
        help="Insert the seeded demo dataset used by the in-memory adapter.",
    )
    args = parser.parse_args()

    settings = load_settings()
    dsn = args.dsn or settings.database_url
    if not dsn:
        raise SystemExit("DATABASE_URL is not configured.")

    apply_schema(dsn, Path(args.schema_path))
    if args.seed_demo:
        seed_demo_data(dsn)


def apply_schema(dsn: str, schema_path: Path) -> None:
    """Apply bootstrap SQL files once."""
    sql_paths = _schema_paths(schema_path)
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migration (
                    name TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            for sql_path in sql_paths:
                cursor.execute(
                    "SELECT 1 FROM schema_migration WHERE name = %(name)s",
                    {"name": sql_path.name},
                )
                if cursor.fetchone() is not None:
                    continue
                cursor.execute(sql_path.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO schema_migration (name) VALUES (%(name)s)",
                    {"name": sql_path.name},
                )
        connection.commit()


def _schema_paths(schema_path: Path) -> list[Path]:
    if schema_path.is_dir():
        return sorted(schema_path.glob("*.sql"))
    return [schema_path]


def seed_demo_data(dsn: str) -> None:
    """Insert or update the seeded demo dataset."""
    state, user = build_demo_state()

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO app_user (id, email, display_name)
                VALUES (%(id)s, %(email)s, %(display_name)s)
                ON CONFLICT (id) DO UPDATE SET
                    email = EXCLUDED.email,
                    display_name = EXCLUDED.display_name
                """,
                {
                    "id": user.id,
                    "email": user.email,
                    "display_name": user.display_name,
                },
            )

            for case_file in state.cases.values():
                cursor.execute(
                    """
                    INSERT INTO case_file (
                        id, title, company, primary_contact, status, last_activity_at, visible_group_id
                    ) VALUES (
                        %(id)s, %(title)s, %(company)s, %(primary_contact)s, %(status)s,
                        %(last_activity_at)s, %(visible_group_id)s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        company = EXCLUDED.company,
                        primary_contact = EXCLUDED.primary_contact,
                        status = EXCLUDED.status,
                        last_activity_at = EXCLUDED.last_activity_at,
                        visible_group_id = EXCLUDED.visible_group_id
                    """,
                    asdict(case_file),
                )

            for activity in state.activities.values():
                cursor.execute(
                    """
                    INSERT INTO activity (
                        id, case_id, description, kind, due_at, created_at, created_by, completed_at
                    ) VALUES (
                        %(id)s, %(case_id)s, %(description)s, %(kind)s, %(due_at)s,
                        %(created_at)s, %(created_by)s, %(completed_at)s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        description = EXCLUDED.description,
                        kind = EXCLUDED.kind,
                        due_at = EXCLUDED.due_at,
                        created_at = EXCLUDED.created_at,
                        created_by = EXCLUDED.created_by,
                        completed_at = EXCLUDED.completed_at
                    """,
                    asdict(activity),
                )
        connection.commit()


if __name__ == "__main__":
    main()

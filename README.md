# GoldenAge

GoldenAge is a focused web application for operational case work. The current implementation slice ships the daily worklist, case-detail workflow, manual Outlook `.msg` intake, single-case assignment suggestion, and fallback search flow behind clean architecture boundaries.

## What Exists

- Daily worklist with overdue/today prioritization
- Case detail panel with enforced next-step resolution
- Outlook `.msg` intake with drag/drop, subject-based case suggestion, and bounded fallback search
- FastAPI web app with server-rendered templates and HTMX interactions
- Demo/in-memory runtime plus raw-SQL PostgreSQL adapters
- Initial schema, tests, and uv-managed Python 3.14 environment

## Stack

- Python: `3.14` pinned via `uv` and [`.python-version`](.python-version)
- Package/runtime tooling: `uv`
- Database: PostgreSQL with hand-written SQL, no ORM
- Web/UI: FastAPI + server-rendered HTML partials for HTMX-style interactions

## Local Setup

```bash
uv sync --extra dev
./.venv/bin/python -m goldenage.bootstrap_postgres --seed-demo
uv run uvicorn goldenage.web.app:create_app --factory --reload
```

Then open `http://127.0.0.1:8000/worklist`.

The repository now includes a local `.env` that points the app at the Docker-published PostgreSQL instance on `127.0.0.1:5432`. If `DATABASE_URL` is present, the app uses the PostgreSQL adapters. If not, it falls back to the seeded in-memory demo adapters.

## Local-First SQLite Mode

To enable the local-first onboarding flow instead of the fixed demo user, configure:

```bash
GOLDENAGE_LOCAL_FIRST_MODE=sqlite3
GOLDENAGE_SQLITE_PATH=var/goldenage.sqlite3
```

When that mode is active, app startup bootstraps the SQLite schema automatically, serves profile images from the local artifact directory, replaces the fixed `Alex Example` user with a first-user onboarding flow, and renders the worklist header as `welcome to the golden age`.

## Windows Standalone Setup

If you only have `uv` installed on Windows, use the built-in SQLite mode instead of PostgreSQL.

1. Install the project dependencies:

```powershell
uv sync --extra dev
```

2. Create a local `.env` file in the repository root with the standalone settings:

```dotenv
GOLDENAGE_LOCAL_FIRST_MODE=sqlite3
GOLDENAGE_SQLITE_PATH=var/goldenage.sqlite3
GOLDENAGE_ARTIFACT_DIR=var/artifacts
GOLDENAGE_LOCAL_TIMEZONE=Europe/Berlin
```

3. Create the SQLite database file and apply the schema:

```powershell
uv run python -m goldenage.bootstrap_sqlite
```

This creates `var\goldenage.sqlite3` for you. You do not need a separate `sqlite3.exe` installation because the bootstrap command uses Python's built-in `sqlite3` module.

4. Start the app:

```powershell
uv run uvicorn goldenage.web.app:create_app --factory --reload
```

Then open `http://127.0.0.1:8000/`. On first launch in standalone mode, GoldenAge redirects to onboarding so the first local user can create an account.

## PostgreSQL Bootstrap

```bash
./.venv/bin/python -m goldenage.bootstrap_postgres --seed-demo
```

That command:

- applies SQL migrations in [`sql/`](sql)
- seeds the fixed demo user
- seeds the baseline cases and activities used by the first slice

## Verification

```bash
./.venv/bin/python -m compileall src tests
./.venv/bin/pytest -q
```

## Repository Guide

- [`docs/README.md`](docs/README.md): documentation index
- [`docs/70-decisions/`](docs/70-decisions): canonical technical decisions and ADRs
- [`docs/learnings/`](docs/learnings): dated implementation notes and discovered constraints
- [`plans/implementation-plan.md`](plans/implementation-plan.md): current implementation plan
- [`sql/`](sql): bootstrap SQL migrations
- [`src/goldenage/`](src/goldenage): application code by clean-architecture layer
- [`tests/`](tests): unit, service, and web-flow tests

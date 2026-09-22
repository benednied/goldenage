# GoldenAge

GoldenAge is a focused web application for operational case work. The current implementation slice ships the daily worklist, case-detail workflow, manual Outlook `.msg` intake, optional Windows classic Outlook mailbox intake, single-case assignment suggestion, and fallback search flow behind clean architecture boundaries.

## What Exists

- Daily worklist with overdue/today prioritization
- Case detail panel with enforced next-step resolution
- Outlook `.msg` intake with drag/drop, subject-based case suggestion, and bounded fallback search
- Optional Windows classic Outlook mailbox polling for Inbox and Sent mail
- FastAPI web app with server-rendered templates and HTMX interactions
- Demo/in-memory runtime plus raw-SQL PostgreSQL adapters
- Initial schema, tests, and uv-managed Python 3.14 environment

## Stack

- Python: `3.14` pinned via `uv` and [`.python-version`](.python-version)
- Package/runtime tooling: `uv`
- Linting and formatting: `ruff`
- Type checking: `ty`
- Database: PostgreSQL with hand-written SQL, no ORM
- Web/UI: FastAPI + server-rendered HTML partials for HTMX-style interactions

## Development Tooling

This repository prefers Astral's `ruff` for linting and formatting and `ty` for type checking. They are included in the `dev` extra, so `uv sync --frozen --extra dev` installs them into `.venv/`.

## Local Setup

Requirements: Python 3.14, [uv](https://docs.astral.sh/uv/), and, for the PostgreSQL
path only, Docker with the Compose plugin. Start in the repository root.

```bash
uv sync --frozen --extra dev
```

### Configuration loading and mode selection

Copy the tracked template before configuring a persistent mode:

```bash
cp .env.example .env
```

`.env` is intentionally ignored by Git and contains only local values. GoldenAge—not
`uv`, FastAPI, or the shell—reads a simple `.env` file from the current working
directory when it builds settings. The app and both bootstrap CLIs use the same
loader. Existing shell environment variables win over `.env`; set
`GOLDENAGE_DISABLE_DOTENV=1` to skip the file entirely. The loader accepts plain
`KEY=VALUE` lines (with optional single or double quotes), not `export KEY=VALUE`.

Choose exactly one runtime mode:

| Mode | Required configuration | Data lifetime |
| --- | --- | --- |
| Demo | Leave `DATABASE_URL` and `GOLDENAGE_LOCAL_FIRST_MODE` unset. | In memory; recreated at every server start. |
| SQLite | `GOLDENAGE_LOCAL_FIRST_MODE=sqlite3` and `GOLDENAGE_SQLITE_PATH=var/goldenage.sqlite3` | The SQLite file and `var/artifacts/` persist locally. |
| PostgreSQL | `DATABASE_URL=postgresql://goldenage:goldenage-local-password@127.0.0.1:5432/goldenage`; leave `GOLDENAGE_LOCAL_FIRST_MODE` unset. | The Compose volume persists locally. |

`GOLDENAGE_LOCAL_FIRST_MODE=sqlite` or `sqlite3` takes precedence over
`DATABASE_URL`. If neither is set, GoldenAge starts the demo adapters. The full list
of optional mail, artifact, timezone, and authentication settings, including legacy
Apple Mail and local-first fallbacks, is commented in [`.env.example`](.env.example).

### Demo mode

Do not create `.env`, or keep the mode variables commented out in it. Then start:

```bash
uv run uvicorn goldenage.web.app:create_app --factory --reload
```

Open <http://127.0.0.1:8000/worklist>. Stop the server with `Ctrl+C`. The demo
dataset has no persistent state; restarting the server resets it.

## Local-First SQLite Mode

To enable the local-first onboarding flow instead of the fixed demo user, uncomment
these values in `.env` (or export them in the shell):

```bash
GOLDENAGE_LOCAL_FIRST_MODE=sqlite3 \
GOLDENAGE_SQLITE_PATH=var/goldenage.sqlite3 \
./.venv/bin/python -m goldenage.bootstrap_sqlite
```

The bootstrap creates `var/` and applies `sql/sqlite/` migrations. App startup also
applies outstanding SQLite migrations, so the bootstrap command is safe to repeat.
Start the server with the same command as demo mode and open
<http://127.0.0.1:8000/worklist>. It serves profile images from the local artifact
directory, replaces the fixed `Alex Example` user with a first-user onboarding flow,
and renders the worklist header as `welcome to the golden age`. Stop with `Ctrl+C`.

`var/goldenage.sqlite3` and `var/artifacts/` are persistent local data. To reset the
SQLite example data, stop the server and delete those two paths, then rerun the
bootstrap command. This is destructive and cannot be undone.

## Local PostgreSQL Mode

The provided [compose.yaml](compose.yaml) supports PostgreSQL 17 and binds its port
only to `127.0.0.1:5432`; it is not exposed to the network. Its password is a
synthetic development-only value. Configure the PostgreSQL `DATABASE_URL` shown in
`.env.example`, ensuring `GOLDENAGE_LOCAL_FIRST_MODE` remains unset, then start and
wait for the health check:

```bash
docker compose up -d
docker compose ps
```

Continue only when the `postgres` service reports `healthy`. Then apply migrations,
optionally seed the deterministic demo data, and start the app:

```bash
./.venv/bin/python -m goldenage.bootstrap_postgres --seed-demo
uv run uvicorn goldenage.web.app:create_app --factory --reload
```

Open <http://127.0.0.1:8000/worklist> and stop Uvicorn with `Ctrl+C`. Stop the
database while retaining its data with `docker compose down`. The named
`goldenage-postgres-data` volume persists migrations and application data. To reset
all PostgreSQL example data, stop the server and run the destructive command below;
then repeat the start and bootstrap commands:

```bash
docker compose down --volumes
```

### Local setup troubleshooting

- `DATABASE_URL is not configured`: set it in `.env` or the shell before running the PostgreSQL bootstrap; run the command from the repository root so `.env` can be found.
- `GOLDENAGE_SQLITE_PATH is not configured`: configure it whenever local-first SQLite mode is enabled.
- A PostgreSQL connection failure usually means the Compose health check is not yet `healthy`; use `docker compose ps` and `docker compose logs postgres` before retrying.
- If port `5432` is already in use, stop the conflicting local service or change both the left-hand side of the `ports` mapping in `compose.yaml` and the port in `DATABASE_URL`.

Mailbox-backed intake uses the same persisted selector defaults on macOS and Windows:

```bash
GOLDENAGE_MAIL_CLIENT_MODE=auto
GOLDENAGE_OUTLOOK_SCAN_PER_FOLDER_LIMIT=250
```

In `auto` mode, macOS uses Apple Mail automation when available and Windows uses classic Outlook desktop through COM. Set the mail address in Settings; leaving the folder field blank scans all mail folders under that configured address. The legacy `GOLDENAGE_APPLE_MAIL_*` env vars still work as fallbacks.

## Artifact Storage

`GOLDENAGE_ARTIFACT_DIR` defaults to `var/artifacts`. Use an application-owned
local directory with trusted parents. New artifacts use exclusive server-owned
`<uuid>.bin` names; the original filename remains metadata. Existing storage keys
remain valid without migration. Root symlinks and Windows reparse points are
rejected. See [storage security and platform validation](docs/learnings/2026-09-15-secure-artifact-storage.md)
for permissions, failure behavior, and supported filesystem assumptions.

## Windows Outlook Mailbox Intake

The mailbox integration only works on Windows with classic desktop Outlook installed and signed in. It uses Outlook COM/MAPI through `pywin32`, so it does not run on Linux, macOS, Outlook Web, or the new WebView-based Outlook app.

To enable automatic mailbox intake:

1. Use the Windows standalone setup above and create the first local user in the app.

2. Confirm the Outlook store display name. In classic Outlook, this is usually the mailbox/account name shown in the left folder pane, for example `Mailbox - bened@example.com` or `bened@example.com`.

3. Add the Outlook settings to the repository-root `.env` file:

```dotenv
GOLDENAGE_OUTLOOK_SYNC_ENABLED=1
GOLDENAGE_OUTLOOK_ACCOUNT=Mailbox - bened@example.com
GOLDENAGE_OUTLOOK_POLL_SECONDS=15
```

4. Start the app from the same Windows user session where Outlook is available:

```powershell
uv run uvicorn goldenage.web.app:create_app --factory --reload
```

5. Open `http://127.0.0.1:8000/`. Keep the server running. GoldenAge polls the configured Outlook store, reads recent Inbox and Sent messages, normalizes the mail envelope, and ingests each new message through the same intake/conversation flow used by manual `.msg` uploads.

Operational notes:

- `GOLDENAGE_OUTLOOK_SYNC_ENABLED=1` turns the worker on.
- `GOLDENAGE_OUTLOOK_ACCOUNT` must exactly match the Outlook store display name, ignoring case.
- `GOLDENAGE_OUTLOOK_POLL_SECONDS` defaults to `15`; values below `5` are treated as `5`.
- `pywin32` is installed automatically by `uv sync` on Windows because it is declared as a Windows-only dependency.
- The worker currently polls the 25 most recent messages in Inbox and Sent during each interval and deduplicates messages for the current app process by Outlook `EntryID`; persisted duplicate protection also uses the existing mail source metadata.
- If the app starts before local-first onboarding has created a user, mailbox messages are skipped until a user exists. Create the local user first, then restart the server with Outlook sync enabled.

Troubleshooting:

- `Classic Outlook intake is only available on Windows.` means the integration is running on a non-Windows platform.
- `pywin32 is required for classic Outlook mailbox intake.` means dependencies were not installed in the active environment; rerun `uv sync --extra dev` on Windows.
- `Outlook account not found` means `GOLDENAGE_OUTLOOK_ACCOUNT` does not match any classic Outlook store display name.
- If no mail appears, verify classic Outlook can open normally in the same Windows session, check the account name, then restart `uvicorn`.

## PostgreSQL Bootstrap

```bash
./.venv/bin/python -m goldenage.bootstrap_postgres --seed-demo
```

In PostgreSQL mode, that command:

- applies SQL migrations in [`sql/`](sql)
- seeds the fixed demo user
- seeds the baseline cases and activities used by the first slice

## Creating Migrations

PostgreSQL migrations live in [`sql/`](sql); SQLite migrations live in
[`sql/sqlite/`](sql/sqlite). They are separate namespaces, but each uses the format
`NNNN_lowercase_description.sql` and a unique next ID. Check the target branch directly
before creating or merging a migration, then run:

```bash
./.venv/bin/python -m goldenage.migration_validation
```

The check preserves the shipped historical prefix collisions as explicit exceptions and
rejects malformed, duplicate, or backward-sorting new IDs. Never rename or edit an
applied migration: deliver a correction as a new forward migration. See
[`ADR-0004`](docs/70-decisions/adr-0004-migration-identifiers.md) for the baseline and
concurrent-PR collision procedure. Add this command to the existing quality job when
that job is available.

## Verification

```bash
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev ty check
./.venv/bin/python -m goldenage.migration_validation
./.venv/bin/python -m compileall src tests
./.venv/bin/pytest -q
```

## Continuous Integration

Pull requests and pushes to the default branch run the locked Python 3.14
quality job: Ruff lint, Ruff format verification, `ty`, and `pytest`. Run the
same checks locally before opening a pull request; the exact commands and
failure-handling guidance are in [`docs/ci.md`](docs/ci.md). Branch-protection
configuration requires GitHub repository administration and is documented
there as well.

To auto-fix style issues locally, use:

```bash
uv run --extra dev ruff check . --fix
uv run --extra dev ruff format .
```

## Repository Guide

- [`CONTRIBUTING.md`](CONTRIBUTING.md): local setup, checks, and the contribution workflow
- [Bug report form](.github/ISSUE_TEMPLATE/bug_report.yml) and [feature request form](.github/ISSUE_TEMPLATE/feature_request.yml): start a reproducible report or a product request
- [Pull-request template](.github/pull_request_template.md): review information for proposed changes
- [`docs/README.md`](docs/README.md): documentation index
- [`docs/70-decisions/`](docs/70-decisions): canonical technical decisions and ADRs
- [`docs/learnings/`](docs/learnings): dated implementation notes and discovered constraints
- [`plans/implementation-plan.md`](plans/implementation-plan.md): current implementation plan
- [`sql/`](sql): bootstrap SQL migrations
- [`src/goldenage/`](src/goldenage): application code by clean-architecture layer
- [`tests/`](tests): unit, service, and web-flow tests

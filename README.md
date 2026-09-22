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

This repository prefers Astral's `ruff` for linting and formatting and `ty` for type checking. They are included in the `dev` extra, so `uv sync --extra dev` installs them into `.venv/`.

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

That command:

- applies SQL migrations in [`sql/`](sql)
- seeds the fixed demo user
- seeds the baseline cases and activities used by the first slice

## Verification

```bash
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev ty check
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

- [`docs/README.md`](docs/README.md): documentation index
- [`docs/70-decisions/`](docs/70-decisions): canonical technical decisions and ADRs
- [`docs/learnings/`](docs/learnings): dated implementation notes and discovered constraints
- [`plans/implementation-plan.md`](plans/implementation-plan.md): current implementation plan
- [`sql/`](sql): bootstrap SQL migrations
- [`src/goldenage/`](src/goldenage): application code by clean-architecture layer
- [`tests/`](tests): unit, service, and web-flow tests

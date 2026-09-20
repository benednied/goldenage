# Contributing to GoldenAge

Thank you for helping improve GoldenAge. This guide covers the smallest useful
contributor workflow; the [repository guide](README.md#repository-guide) links
to the detailed technical decisions.

## Before You Start

- Use Python 3.14 (pinned in [`.python-version`](.python-version)) and install
  [`uv`](https://docs.astral.sh/uv/).
- Do not include credentials, access tokens, production data, or personally
  identifiable example data in issues, pull requests, test fixtures, logs, or
  screenshots. Replace them with clearly fictional values.
- Do not report a suspected vulnerability in a public issue. Use the private
  security reporting route documented by the project maintainers. The repository
  administrator must enable and publish that route before external reports can
  be accepted.

## Set Up a Local Environment

```bash
uv sync --extra dev
```

The default runtime uses seeded in-memory adapters when `DATABASE_URL` is not
set. To use PostgreSQL, configure `DATABASE_URL`, then apply the migrations and
seed the demo data:

```bash
./.venv/bin/python -m goldenage.bootstrap_postgres --seed-demo
```

For local-first SQLite onboarding, set these variables before starting the app:

```bash
GOLDENAGE_LOCAL_FIRST_MODE=sqlite3
GOLDENAGE_SQLITE_PATH=var/goldenage.sqlite3
```

Start the application with:

```bash
uv run uvicorn goldenage.web.app:create_app --factory --reload
```

Then open <http://127.0.0.1:8000/worklist>.

## Make a Change

Keep changes within the architecture boundaries:

- `domain/` contains pure business rules and models.
- `application/` orchestrates use cases and owns workflow decisions.
- `adapters/` contains storage and external integrations.
- `web/` delivers HTTP and UI concerns only; it must not hold business rules.

See the [style guide](docs/70-decisions/styleguide.md) for the full conventions
and the [decision records](docs/70-decisions/) for architectural context. Add or
update focused tests for changes to rules, workflows, web handlers, or
SQL-backed behavior. Test fixtures must be synthetic and free of credentials or
personal data.

## Check Your Work

Run these checks before opening a pull request:

```bash
./.venv/bin/python -m ruff check .
./.venv/bin/python -m ruff format --check .
./.venv/bin/ty check
./.venv/bin/python -m compileall src tests
./.venv/bin/python -m pytest -q
```

Ruff can fix many style issues locally:

```bash
./.venv/bin/python -m ruff check . --fix
./.venv/bin/python -m ruff format .
```

## Open a Small Pull Request

1. Start from the current default branch and keep the change narrowly scoped.
2. Explain the problem and the resulting behavior in the pull request.
3. List the validation you ran and any checks you did not run, with a reason.
4. Call out migration, schema, environment-variable, or operational changes.
5. Include before-and-after screenshots for relevant UI changes.
6. Link the related issue, ADR, or implementation plan when one exists.

The [pull-request template](.github/pull_request_template.md) provides the
review checklist. Use the [bug report](.github/ISSUE_TEMPLATE/bug_report.yml)
or [feature request](.github/ISSUE_TEMPLATE/feature_request.yml) form for new
issues.

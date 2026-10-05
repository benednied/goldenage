# Re-enable type checking for central modules (TYPE-01)

The initial `tool.ty.src.exclude` list contained these six paths:

- `src/goldenage/adapters/demo.py`
- `src/goldenage/adapters/postgres.py`
- `src/goldenage/adapters/sqlite.py`
- `src/goldenage/bootstrap_postgres.py`
- `src/goldenage/web/app.py`
- `tests/test_web.py`

The baseline `ty check` passed only because these files were excluded. A direct
check with `--no-force-exclude` found 92 diagnostics: PostgreSQL's optional
imports and untyped database rows accounted for the errors, the bootstrap
module had one SQL-string overload error, and `web/app.py` emitted two
deprecated FastAPI lifecycle warnings. The demo adapter, SQLite adapter, and
web tests had no diagnostics in that direct check.

## Resolution

All six exclusions were removed. Psycopg is a required project dependency, so
the PostgreSQL adapter now imports it directly and exposes a typed connection
boundary for dictionary rows. Database rows are converted through explicit
runtime validators before entering domain models. The validators preserve the
existing numeric and Boolean conversions while rejecting malformed IDs,
timestamps, literals, strings, and JSON participant records.

The bootstrap module marks migration files as trusted `LiteralString` input for
Psycopg's SQL API. The web app uses a typed lifespan context manager instead of
the deprecated `on_event` hooks; worker startup and shutdown behavior is
unchanged.

## Verification

The following local checks pass with the repository's existing `.venv`:

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/ty check
.venv/bin/pytest -q tests/test_postgres_adapter.py tests/test_bootstrap_postgres.py
```

The targeted adapter/bootstrap run reports 16 passed tests. The full pytest
run could not complete in the managed worker sandbox: its temporary paths are
under a parent mounted with execute-only permissions, so the existing secure
artifact store raises `PermissionError` while opening that parent. This is an
environment limitation, not a test assertion or type-check failure.

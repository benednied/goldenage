# Repository Guidelines

## Project Structure & Module Organization
`src/goldenage/` contains the application code, split by clean-architecture layer: `domain/` for pure models and rules, `application/` for use cases and ports, `adapters/` for storage and integrations, and `web/` for the FastAPI app, `templates/`, and `static/`. `tests/` covers unit, service, and web flows. Hand-written migrations live in `sql/` and `sql/sqlite/`. ADRs are in `docs/70-decisions/`; dated implementation notes are in `docs/learnings/`.

## Build, Test, and Development Commands
- `uv sync --extra dev`: install Python 3.14 dependencies and test tooling.
- `./.venv/bin/python -m goldenage.bootstrap_postgres --seed-demo`: apply PostgreSQL migrations and seed demo data.
- `uv run uvicorn goldenage.web.app:create_app --factory --reload`: start the local dev server at `http://127.0.0.1:8000/worklist`.
- `uv run --extra dev ruff check .`: run lint checks.
- `uv run --extra dev ruff format --check .`: verify formatting.
- `uv run --extra dev ty check`: run type checks across `src/` and `tests/`.
- `./.venv/bin/python -m compileall src tests`: run a fast syntax check.
- `./.venv/bin/pytest -q`: run the full test suite.

## Coding Style & Naming Conventions
Use 4-space indentation, explicit type hints, and small modules with clear data flow. Follow `docs/70-decisions/styleguide.md`: business rules stay in `domain/`, workflow orchestration in `application/`, integrations in `adapters/`, and delivery code only in `web/`. Prefer `@dataclass(frozen=True, slots=True)` for core models and `Protocol` for ports. Use `snake_case` for modules and functions, `PascalCase` for classes, and keep raw SQL in adapter modules or `sql/` files. This repo prefers `ruff` and `ty`; run them before opening a PR.

## Testing Guidelines
Pytest is configured in `pyproject.toml` with `src` on `pythonpath` and `tests/` as the test root. Name test files `test_*.py` and keep test names behavior-focused, for example `test_upload_subject_match_proposes_case`. Add or update tests whenever you change domain rules, application flows, web handlers, or SQL-backed behavior. Before review, run `ruff`, `ty`, and `pytest`.

## Commit & Pull Request Guidelines
The current history uses short, imperative commit subjects such as `Initial commit`. Keep commit titles concise, present tense, and scoped to one logical change. Pull requests should summarize the user-visible impact, list the verification commands you ran, call out schema or environment-variable changes, and include screenshots when editing `web/templates/` or `web/static/`. Link an issue, ADR, or plan document when relevant.

## Configuration Tips
The app uses PostgreSQL when `DATABASE_URL` is set; otherwise it falls back to the seeded demo adapters. For local-first SQLite mode, set `GOLDENAGE_LOCAL_FIRST_MODE=sqlite3` and `GOLDENAGE_SQLITE_PATH=var/goldenage.sqlite3`.

# Tech Stack

## Defaults

- Runtime target: Python 3.14
- Repository interpreter: uv-managed Python 3.14 via `.python-version` and `.venv`
- Package and environment tooling: `uv`
- Type-checking target: `ty`
- Test runner: `pytest`
- Database: PostgreSQL
- Data access: raw SQL only, no ORM
- Frontend interaction model: server-rendered HTML with HTMX
- Architecture: clean architecture with explicit ports and adapters

## Service Boundaries

- `goldenage` starts as a single repo and process for fast iteration.
- The assignment agent (`gisela`) and fallback search system (`elizabethan`) are isolated behind adapter contracts from day one.
- Once their interfaces stabilize, they can move to separate repositories and communicate over HTTP without changing domain/application code.

## Notes

- The repository no longer depends on the system Python install.
- The current app can run in demo mode without PostgreSQL, but PostgreSQL remains the intended system of record for non-demo usage.
- No ORM shortcuts are allowed in interim development code.

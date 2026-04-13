# 2026-04-12 Bootstrap Slice

## What Worked

- The daily-worklist and enforced next-step rule fit cleanly into a domain/application split.
- HTMX plus server-rendered partials is sufficient for the current two-pane workflow without adding a frontend build system.
- A demo adapter layer makes the product runnable before PostgreSQL wiring is fully exercised.
- uv-managed Python removed dependency on the system interpreter and made the runtime target explicit.

## Friction Found

- FastAPI and Starlette template helpers are version-sensitive; passing `request`, `name`, and `context` explicitly is safer than relying on positional call conventions.
- A naive "always suggest one case" heuristic works against the desired fallback-search flow; the system needs an explicit "no safe single match" state.
- Demo mode is useful, but it can mask divergence from the PostgreSQL adapter path if left untested.

## Follow-Up Gaps

- Add PostgreSQL-backed integration tests for repositories and permission filtering.
- Replace heuristic assignment/search adapters with real `gisela` and `elizabethan` integrations behind the existing ports.
- Introduce auth/provider wiring instead of the current fixed demo user.
- Expand artifact extraction beyond plain text decoding into OCR and richer document parsing.

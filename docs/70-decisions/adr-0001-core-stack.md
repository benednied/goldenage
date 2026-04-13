# ADR-0001: Core Application Stack

## Status

Accepted

## Context

GoldenAge needs a low-friction implementation path for a workflow-heavy internal tool: server-rendered UI, strong control over request handling, explicit data access, and a simple local setup that does not depend on the machine's system Python.

## Decision

- Use Python 3.14 as the supported runtime.
- Manage Python and dependencies with `uv`.
- Build the web app on FastAPI with server-rendered Jinja templates and HTMX interactions.
- Use PostgreSQL as the intended primary datastore.
- Write raw SQL directly instead of using an ORM.

## Consequences

- The project has a fast bootstrap path and explicit control over query behavior.
- Business workflows can stay close to plain Python rather than framework conventions.
- Data access remains auditable and predictable, but query and mapping code is more verbose.
- UI interactions stay simple and inspectable, but richer client behavior will require deliberate partial/template design.

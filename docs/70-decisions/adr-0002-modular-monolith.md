# ADR-0002: Start as a Modular Monolith

## Status

Accepted

## Context

GoldenAge will likely split the assignment agent (`gisela`) and the fallback search system (`elizabethan`) into separate repositories later. Doing that immediately would slow down iteration while the domain model and interaction shape are still changing.

## Decision

- Start as a single repository and single deployable application.
- Keep clean architecture boundaries between `domain`, `application`, `adapters`, and `web`.
- Hide agent and search integrations behind explicit ports so they can move to HTTP services later.

## Consequences

- Early implementation remains fast and easy to run locally.
- Service extraction later should be mostly adapter work rather than domain rewrites.
- The codebase must stay disciplined about boundary ownership; shortcuts in `web/` or adapter leakage into domain logic will make the later split expensive.

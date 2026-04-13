# ADR-0003: Support Demo Mode Alongside PostgreSQL Adapters

## Status

Accepted

## Context

The product needs a runnable slice before database provisioning, auth integration, and external services are in place. At the same time, the implementation should not drift away from the eventual PostgreSQL-backed design.

## Decision

- Provide in-memory demo adapters with seeded data for local development and flow validation.
- Implement raw-SQL PostgreSQL repositories in parallel as the production-oriented persistence path.
- Keep both behind the same application ports and let runtime configuration choose the backing adapter.

## Consequences

- The team can validate UX and workflow behavior immediately.
- Tests can exercise core flows without requiring infrastructure.
- There is a risk that demo behavior diverges from the PostgreSQL path, so integration coverage against PostgreSQL remains necessary.

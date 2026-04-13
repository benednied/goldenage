# Style Guide

## Code Style

- Follow the Google Python Style Guide unless a local convention is stricter.
- Prefer small modules with explicit data flow over framework-heavy indirection.
- Use dataclasses and typed protocols for domain and port boundaries.
- Keep raw SQL in dedicated adapter modules or SQL files; never hide it behind an ORM.

## Architecture Rules

- `domain/` contains core business rules and pure models.
- `application/` orchestrates use cases and owns workflow decisions.
- `adapters/` integrate storage, AI/search clients, and auth providers.
- `web/` is delivery-only and should not contain business rules.

## UI Rules

- Keep the default workspace calm and task-focused.
- Prefer inline/detail partial updates over full-page navigation where possible.
- Surface status with both color and text labels.

# ADR-0004: Use Monotonic Migration IDs per Dialect

## Status

Accepted

## Context

The PostgreSQL and SQLite bootstraps sort complete SQL filenames lexicographically and
record that filename verbatim in `schema_migration.name`. The initial migration history
contains prefix collisions that must remain deployable because their exact names may
already be recorded in existing databases.

## Decision

Migration namespaces are separate for PostgreSQL (`sql/`) and SQLite (`sql/sqlite/`).
Every future migration must use the filename form `NNNN_lowercase_description.sql`,
where `NNNN` is a four-digit, zero-padded integer. A new ID must be greater than the
historical baseline and unique within its dialect. Names consequently sort after all
historical migrations.

Before opening or merging a migration PR, authors must rebase or otherwise compare with
the target branch, then run:

```bash
./.venv/bin/python -m goldenage.migration_validation
```

For concurrent PRs, the PR that merges first keeps its selected next ID. Every other
PR must choose a new, unused ID and rerun the check before merge. Do not resolve a
collision by renaming a migration that may have been applied anywhere.

The validator permits these historical exceptions and no others:

| Dialect | Historical filenames | Baseline for new IDs |
| --- | --- | --- |
| PostgreSQL | `0001_initial.sql`, `0002_artifact_mail_metadata.sql`, `0003_apple_mail_import.sql`, `0003_mailbox_ingestion.sql` | `0003` |
| SQLite | `0001_initial.sql`, `0002_apple_mail_import.sql`, `0002_mailbox_ingestion.sql` | `0002` |

The existing bootstraps continue to use full filenames as migration identities. Applied
migration files and `schema_migration` rows are immutable: corrections require a new
forward migration. A resource relocation must preserve the exact filename identity.

## Consequences

- New migrations are unambiguous and order after the shipped history.
- Existing PostgreSQL `0003_*` and SQLite `0002_*` files retain their deterministic
  lexicographic order and stored identities.
- The repository quality check detects missing historical names, malformed names, new
  IDs within the historical range, and duplicate future IDs.

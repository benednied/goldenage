# Changelog

All notable changes to GoldenAge are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and release versions
use the Python-compatible subset of [PEP 440](https://peps.python.org/pep-0440/).

## [Unreleased]

Changes that are not part of a tagged release go here. Move them into the
versioned section as part of the release preparation pull request.

## [0.1.0] - Unreleased

This is the initial package baseline. It is not a published release until a
`v0.1.0` tag and a corresponding draft release have passed the process in
[`docs/releasing.md`](docs/releasing.md).

### Added

- Daily worklist and case-detail workflows with enforced next-step resolution.
- Outlook `.msg` intake, case suggestions, bounded fallback search, and the
  optional Windows classic Outlook mailbox integration.
- Demo/in-memory, local-first SQLite, and PostgreSQL runtime paths.
- A locked Python 3.14 development toolchain with Ruff, ty, migration checks,
  and pytest quality gates.

### Configuration

- Runtime mode, artifact storage, mailbox, timezone, and authentication
  settings are documented in [`.env.example`](.env.example).

### Schema and upgrade notes

- PostgreSQL and SQLite migrations are hand-written and applied forward-only.
- A deployment using a persistent database must apply the migrations in the
  release before starting the new application version. See
  [Creating Migrations](README.md#creating-migrations).

[Unreleased]: https://github.com/benednied/goldenage/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/benednied/goldenage/releases/tag/v0.1.0

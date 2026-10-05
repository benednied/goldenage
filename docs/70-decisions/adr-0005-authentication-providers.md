# ADR-0005: Explicit Authentication Providers

## Status

Accepted

## Decision

Demo mode, local-first SQLite, and PostgreSQL authenticated mode are separate
runtime paths:

- no `DATABASE_URL` selects the seeded in-memory demo for local development;
- `GOLDENAGE_LOCAL_FIRST_MODE=sqlite3` selects the existing single-account
  onboarding flow; and
- `DATABASE_URL` requires `GOLDENAGE_AUTH_PROVIDER=postgres_local`.

PostgreSQL mode uses the `app_user` password hash as its initial provider. The
provider returns a verified `UserContext` and resolves its group memberships
from `user_group_membership`. The existing signed account-ID cookie remains the
session boundary; this provider does not introduce a second session system.

The PostgreSQL authentication migration adds nullable `password_hash` and
`profile_image_path` columns. Operators must provision accounts with a
PBKDF2-SHA256 hash before enabling the provider. Accounts without a password
hash cannot authenticate, and a missing or unknown provider fails application
startup rather than selecting the demo identity.

Group membership is resolved on every authenticated request. Membership changes,
account deletion, and password-hash removal therefore take effect on the next
request using that session; no separate group cache refresh or revocation job is
required for this provider.

LDAP and Entra ID are not supported providers yet and are not shown as login
options.

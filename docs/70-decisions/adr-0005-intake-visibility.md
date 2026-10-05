# ADR-0005: Intake and Conversation Visibility

## Status

Accepted for the current single-group case visibility model.

## Policy

- An unassigned artifact uploaded by a user is private to that uploader.
- An unassigned artifact with no uploader is explicitly shared intake and is
  visible to every user.
- Once assigned, an artifact follows its case: public cases are visible to
  everyone and group cases are visible only to members of that group.
- A conversation with an assigned case follows that case. Otherwise its
  visibility follows its latest artifact, including the artifact's uploader
  ownership when it is still unassigned.
- Conversation expansion and source-message deduplication apply the same
  artifact policy to every message; a missing case join is not authorization.
- A case created from intake inherits the creator's lexicographically first
  visible group ID. A user with no visible groups creates a shared/public case
  because the current schema has no private-case owner column.

These rules are enforced in repository queries and in the demo adapter. The
application service passes only user-visible cases to suggestion and fallback
search clients, and mutations first resolve the artifact and target case
through their user-scoped repository lookups.

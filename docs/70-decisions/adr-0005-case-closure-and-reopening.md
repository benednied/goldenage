# ADR-0005: Explicit case closure and reopening

## Status

Accepted

## Decision

GoldenAge uses blocking closure as its conservative policy. A resolve request
may close a case only when the selected activity is the case's last unfinished
activity. The request fails before any activity is marked complete when other
activities remain; the system does not silently complete or cancel them.

Resolving an activity never reopens a closed case. A user must issue the
explicit reopen command, which checks case visibility, changes the case to
`open`, and writes a `case_reopened` audit event in the same persistence
transaction. Activity resolution and its case update are likewise one
transaction, with the persisted case and activity state rechecked to reject
stale requests.

Closed cases are excluded from ordinary due-work queries, while their activity
history remains visible in case detail so a closed case with stale data cannot
be mistaken for successfully completed work.

## Consequences

- Users must resolve or schedule remaining activities before closing a case.
- A closed case with an unfinished activity is visible as a state problem in
  detail, not as unexplained ordinary work; explicit reopening is required.
- SQLite and PostgreSQL keep the activity, case, and workflow audit event
  atomic. The in-memory adapter follows the same transition contract.

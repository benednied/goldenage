# Command idempotency and concurrency policy

Workflow mutations use a command identity scoped to the acting user and
operation. The web forms may provide a UUID; when they do not, the service
derives a stable UUID from the normalized operation and intent. The database
stores the request fingerprint and committed result in `command_execution`.

An identical retry returns the stored case/activity result and does not write
another activity or audit event. Reusing a command UUID for a different intent
is a conflict. Assignment of an already assigned artifact is also a conflict;
intentional reassignment must call the explicit `reassign=True` operation with
a new command identity, which creates one audited follow-up transition.

Resolution keeps the domain's sequential already-completed guard. Persistent
commands additionally use one transaction: SQLite serializes the command with
`BEGIN IMMEDIATE`, while PostgreSQL locks the target rows and uses a
`completed_at IS NULL` conditional update. A concurrent loser receives a
deterministic command conflict and can retry with its original command UUID.

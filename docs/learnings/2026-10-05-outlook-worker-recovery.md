# Outlook worker recovery

The classic Outlook mailbox worker now reads the existing
`mailbox_sync_checkpoint` rows, fetches bounded pages until the durable position,
and delivers the backlog oldest-first. A checkpoint advances only after the
callback and checkpoint write both succeed. Callback failures receive bounded
interruptible retries; after the retry budget the message is quarantined in
worker health, later messages may be attempted, and the durable position stays
before the failed message. This intentionally permits idempotent replay after a
restart so ingestion dedupe remains the final guard against duplicate artifacts.

PostgreSQL, SQLite, and the in-memory demo repository use the existing account
and checkpoint contracts. SQLite now exposes the same adapter methods as
PostgreSQL; no parallel checkpoint table was added.

Validation in this environment uses deterministic Outlook collection fixtures.
Native Windows checks involving classic Outlook COM/MAPI, pywin32, Outlook
sharing/ACL behavior, and Windows mailbox locking were not performed because
the validation host is Linux.

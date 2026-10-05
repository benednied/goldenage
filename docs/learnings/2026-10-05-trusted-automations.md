# Trusted local automations

M6 automations are owned by an application user and persist their Python
source, SHA-256 code version, enabled state, effective user, configured
filesystem paths, trigger configuration, and bounded run logs. Manual,
timezone-aware cron, mailbox, and artifact triggers all create a durable run
with an idempotency key. Mail listeners use exact normalized sender and
recipient addresses and case-insensitive subject containment.

Ingestion and assignment events are published only after the corresponding
application service has returned successfully. Repeated delivery returns the
existing run for the same event/trigger key. On process restart, queued or
running rows are marked `interrupted`; they are visible to the owner and need
an explicit retry, so restart recovery cannot silently repeat a completed
mutation. The default overlap policy records a skipped run while another run
is active. The default missed-schedule policy skips slots that were not
observed. A `run_once` schedule catches the latest matching minute in the
previous 24 hours, while the default `skip` policy does not catch up.

The runtime is trusted local Python, not a sandbox. Python code can access the
host process and filesystem, and the configured paths are disclosure and
provenance metadata rather than an isolation boundary. Deployments requiring
multi-user isolation must use separate operating-system identities or
instances. App-owned wrappers revalidate the current effective user's normal
repository permissions before reads and mutations and attribute audit writes
to that user.

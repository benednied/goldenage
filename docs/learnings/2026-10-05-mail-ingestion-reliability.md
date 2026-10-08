# Mail ingestion reliability

Mail identity is scoped to the authenticated user.  A source message identity
(mailbox account/folder/message id, RFC Message-ID, or the normalized content
fingerprint used by manual uploads) is claimed by a database uniqueness
constraint, so a pre-check is only an optimization and not the correctness
boundary.

Extraction runs before the original is written.  The original is then written
to server-owned storage and finalized with the artifact, mail metadata,
conversation update, and source-message row in one database transaction.  If a
concurrent delivery wins the identity claim, or finalization fails, the newly
written file is removed.  A successful finalization is not rolled back when
suggestion analysis fails: the artifact stays in the user’s intake queue, an
`artifact_analysis_failed` audit event is attempted, and
`retry_artifact_analysis()` retries analysis without importing the message a
second time.

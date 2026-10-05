ALTER TABLE mail_conversation ADD COLUMN user_id TEXT REFERENCES app_user (id) ON DELETE CASCADE;

UPDATE mail_conversation
SET user_id = (
    SELECT uploaded_by
    FROM artifact
    WHERE artifact.id = mail_conversation.latest_artifact_id
)
WHERE user_id IS NULL;

ALTER TABLE mail_message RENAME TO mail_message_legacy;

CREATE TABLE mail_message (
    artifact_id TEXT PRIMARY KEY REFERENCES artifact (id) ON DELETE CASCADE,
    user_id TEXT REFERENCES app_user (id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES mail_conversation (id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_account_id TEXT,
    source_folder_id TEXT,
    source_message_id TEXT,
    source_conversation_id TEXT,
    internet_message_id TEXT,
    dedupe_fingerprint TEXT NOT NULL,
    direction TEXT,
    received_at TEXT,
    created_at TEXT NOT NULL
);

INSERT INTO mail_message (
    artifact_id, conversation_id, source_kind, source_account_id, source_folder_id,
    source_message_id, source_conversation_id, internet_message_id, dedupe_fingerprint,
    direction, received_at, created_at, user_id
)
SELECT mm.artifact_id, mm.conversation_id, mm.source_kind, mm.source_account_id,
       mm.source_folder_id, mm.source_message_id, mm.source_conversation_id,
       mm.internet_message_id, mm.dedupe_fingerprint, mm.direction, mm.received_at,
       mm.created_at, a.uploaded_by
FROM mail_message_legacy mm
JOIN artifact a ON a.id = mm.artifact_id;

DROP TABLE mail_message_legacy;

CREATE UNIQUE INDEX mail_message_user_dedupe_idx
    ON mail_message (user_id, dedupe_fingerprint)
    WHERE user_id IS NOT NULL;

CREATE UNIQUE INDEX mail_message_user_source_idx
    ON mail_message (user_id, source_kind, source_account_id, source_folder_id, source_message_id)
    WHERE user_id IS NOT NULL
      AND source_account_id IS NOT NULL
      AND source_folder_id IS NOT NULL
      AND source_message_id IS NOT NULL;

CREATE UNIQUE INDEX mail_message_user_internet_idx
    ON mail_message (user_id, source_kind, internet_message_id)
    WHERE user_id IS NOT NULL
      AND internet_message_id IS NOT NULL;

CREATE UNIQUE INDEX mail_conversation_user_external_idx
    ON mail_conversation (user_id, source_kind, external_conversation_id)
    WHERE user_id IS NOT NULL
      AND external_conversation_id IS NOT NULL;

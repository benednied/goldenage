ALTER TABLE mail_message RENAME TO mail_message_legacy;

DROP INDEX mail_message_conversation_idx;
DROP INDEX mail_message_source_idx;
DROP INDEX mail_message_internet_id_idx;

CREATE TABLE mail_message (
    artifact_id TEXT PRIMARY KEY REFERENCES artifact (id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES mail_conversation (id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_account_id TEXT,
    source_folder_id TEXT,
    source_message_id TEXT,
    source_conversation_id TEXT,
    internet_message_id TEXT,
    dedupe_fingerprint TEXT NOT NULL,
    dedupe_scope_user_id TEXT REFERENCES app_user (id),
    direction TEXT,
    received_at TEXT,
    created_at TEXT NOT NULL
);

INSERT INTO mail_message (
    artifact_id, conversation_id, source_kind, source_account_id, source_folder_id,
    source_message_id, source_conversation_id, internet_message_id, dedupe_fingerprint,
    dedupe_scope_user_id, direction, received_at, created_at
)
SELECT artifact_id, conversation_id, source_kind, source_account_id, source_folder_id,
       source_message_id, source_conversation_id, internet_message_id, dedupe_fingerprint,
       (SELECT uploaded_by FROM artifact WHERE artifact.id = mail_message_legacy.artifact_id),
       direction, received_at, created_at
FROM mail_message_legacy;

DROP TABLE mail_message_legacy;

CREATE UNIQUE INDEX mail_message_dedupe_scope_idx
    ON mail_message (
        dedupe_fingerprint,
        COALESCE(dedupe_scope_user_id, '00000000-0000-0000-0000-000000000000')
    );

CREATE INDEX mail_message_conversation_idx ON mail_message (conversation_id);
CREATE INDEX mail_message_source_idx
    ON mail_message (source_kind, source_account_id, source_folder_id, source_message_id);
CREATE INDEX mail_message_internet_id_idx ON mail_message (internet_message_id);

CREATE TABLE mail_conversation (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    external_conversation_id TEXT,
    normalized_subject TEXT,
    latest_subject TEXT,
    latest_message_at TEXT NOT NULL,
    participants_json TEXT NOT NULL DEFAULT '[]',
    message_count INTEGER NOT NULL DEFAULT 1,
    latest_artifact_id TEXT NOT NULL REFERENCES artifact (id) ON DELETE CASCADE,
    assigned_case_id TEXT REFERENCES case_file (id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX mail_conversation_latest_message_idx
    ON mail_conversation (latest_message_at DESC);

CREATE TABLE mail_message (
    artifact_id TEXT PRIMARY KEY REFERENCES artifact (id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES mail_conversation (id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_account_id TEXT,
    source_folder_id TEXT,
    source_message_id TEXT,
    source_conversation_id TEXT,
    internet_message_id TEXT,
    dedupe_fingerprint TEXT NOT NULL UNIQUE,
    direction TEXT,
    received_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX mail_message_conversation_idx ON mail_message (conversation_id);
CREATE INDEX mail_message_source_idx
    ON mail_message (source_kind, source_account_id, source_folder_id, source_message_id);
CREATE INDEX mail_message_internet_id_idx ON mail_message (internet_message_id);

CREATE TABLE mailbox_account_config (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES app_user (id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    account_key TEXT NOT NULL,
    outlook_store_name TEXT NOT NULL,
    inbox_folder_key TEXT,
    sent_folder_key TEXT,
    polling_interval_seconds INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE mailbox_sync_checkpoint (
    account_config_id TEXT NOT NULL REFERENCES mailbox_account_config (id) ON DELETE CASCADE,
    folder_key TEXT NOT NULL,
    last_message_key TEXT,
    last_message_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_config_id, folder_key)
);

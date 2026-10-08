CREATE TABLE mailbox_import_recovery (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    source_system TEXT NOT NULL,
    external_message_id TEXT,
    rfc_message_id TEXT,
    account_name TEXT,
    mailbox_name TEXT,
    file_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content BYTEA NOT NULL,
    extracted_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT NOT NULL,
    status TEXT NOT NULL,
    failed_at TIMESTAMPTZ NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    recovered_artifact_id UUID REFERENCES artifact (id),
    recovered_at TIMESTAMPTZ
);

CREATE INDEX mailbox_import_recovery_user_status_idx
    ON mailbox_import_recovery (user_id, status, failed_at DESC);

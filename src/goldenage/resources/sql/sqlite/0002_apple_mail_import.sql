ALTER TABLE artifact_mail_metadata ADD COLUMN source_system TEXT NOT NULL DEFAULT 'outlook_upload';
ALTER TABLE artifact_mail_metadata ADD COLUMN message_format TEXT NOT NULL DEFAULT 'outlook_msg';
ALTER TABLE artifact_mail_metadata ADD COLUMN external_message_id TEXT;
ALTER TABLE artifact_mail_metadata ADD COLUMN rfc_message_id TEXT;
ALTER TABLE artifact_mail_metadata ADD COLUMN source_account TEXT;
ALTER TABLE artifact_mail_metadata ADD COLUMN source_mailbox TEXT;

CREATE TABLE mail_import_source (
    user_id TEXT NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    source_system TEXT NOT NULL,
    enabled_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, source_system)
);

CREATE TABLE mail_import_selector (
    user_id TEXT NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    source_system TEXT NOT NULL,
    account_name TEXT,
    mailbox_name TEXT,
    unread_only INTEGER NOT NULL DEFAULT 1,
    sender_filter TEXT NOT NULL DEFAULT '',
    subject_filter TEXT NOT NULL DEFAULT '',
    sent_after TEXT,
    result_limit INTEGER NOT NULL DEFAULT 25,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, source_system)
);

CREATE TABLE mail_import_review_candidate (
    user_id TEXT NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    source_system TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    account_name TEXT,
    mailbox_name TEXT,
    subject TEXT,
    sender_name TEXT,
    sender_email TEXT,
    sent_at TEXT,
    preview_text TEXT NOT NULL DEFAULT '',
    unread INTEGER NOT NULL DEFAULT 0,
    rfc_message_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, source_system, candidate_id)
);

CREATE TABLE imported_mail_message (
    user_id TEXT NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    source_system TEXT NOT NULL,
    external_message_id TEXT NOT NULL,
    rfc_message_id TEXT,
    artifact_id TEXT NOT NULL REFERENCES artifact (id) ON DELETE CASCADE,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (user_id, source_system, external_message_id)
);

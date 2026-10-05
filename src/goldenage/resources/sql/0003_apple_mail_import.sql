ALTER TABLE artifact_mail_metadata
    RENAME COLUMN source_kind TO source_system;

ALTER TABLE artifact_mail_metadata
    ADD COLUMN message_format TEXT NOT NULL DEFAULT 'outlook_msg';

ALTER TABLE artifact_mail_metadata
    ADD COLUMN external_message_id TEXT;

ALTER TABLE artifact_mail_metadata
    ADD COLUMN rfc_message_id TEXT;

ALTER TABLE artifact_mail_metadata
    ADD COLUMN source_account TEXT;

ALTER TABLE artifact_mail_metadata
    ADD COLUMN source_mailbox TEXT;

CREATE TABLE mail_import_source (
    user_id UUID NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    source_system TEXT NOT NULL,
    enabled_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, source_system)
);

CREATE TABLE mail_import_selector (
    user_id UUID NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    source_system TEXT NOT NULL,
    account_name TEXT,
    mailbox_name TEXT,
    unread_only BOOLEAN NOT NULL DEFAULT TRUE,
    sender_filter TEXT NOT NULL DEFAULT '',
    subject_filter TEXT NOT NULL DEFAULT '',
    sent_after TIMESTAMPTZ,
    result_limit INTEGER NOT NULL DEFAULT 25,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, source_system)
);

CREATE TABLE mail_import_review_candidate (
    user_id UUID NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    source_system TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    account_name TEXT,
    mailbox_name TEXT,
    subject TEXT,
    sender_name TEXT,
    sender_email TEXT,
    sent_at TIMESTAMPTZ,
    preview_text TEXT NOT NULL DEFAULT '',
    unread BOOLEAN NOT NULL DEFAULT FALSE,
    rfc_message_id TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, source_system, candidate_id)
);

CREATE TABLE imported_mail_message (
    user_id UUID NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    source_system TEXT NOT NULL,
    external_message_id TEXT NOT NULL,
    rfc_message_id TEXT,
    artifact_id UUID NOT NULL REFERENCES artifact (id) ON DELETE CASCADE,
    imported_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, source_system, external_message_id)
);

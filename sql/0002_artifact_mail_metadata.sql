CREATE TABLE artifact_mail_metadata (
    artifact_id UUID PRIMARY KEY REFERENCES artifact (id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    subject TEXT,
    sender_name TEXT,
    sender_email TEXT,
    sender_domain TEXT,
    recipients_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);

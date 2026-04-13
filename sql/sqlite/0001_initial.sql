CREATE TABLE app_user (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    profile_image_path TEXT
);

CREATE TABLE app_group (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE user_group_membership (
    user_id TEXT NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    group_id TEXT NOT NULL REFERENCES app_group (id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);

CREATE TABLE case_file (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT,
    primary_contact TEXT,
    status TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    visible_group_id TEXT REFERENCES app_group (id)
);

CREATE TABLE activity (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES case_file (id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    kind TEXT NOT NULL,
    due_at TEXT NOT NULL,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT REFERENCES app_user (id)
);

CREATE INDEX activity_due_idx ON activity (due_at) WHERE completed_at IS NULL;

CREATE TABLE artifact (
    id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    content_text TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    uploaded_by TEXT REFERENCES app_user (id),
    assigned_case_id TEXT REFERENCES case_file (id)
);

CREATE TABLE assignment_suggestion (
    artifact_id TEXT PRIMARY KEY REFERENCES artifact (id) ON DELETE CASCADE,
    suggested_case_id TEXT REFERENCES case_file (id),
    summary_reason TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE audit_event (
    id TEXT PRIMARY KEY,
    actor_user_id TEXT REFERENCES app_user (id),
    event_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE artifact_mail_metadata (
    artifact_id TEXT PRIMARY KEY REFERENCES artifact (id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    subject TEXT,
    sender_name TEXT,
    sender_email TEXT,
    sender_domain TEXT,
    recipients_json TEXT NOT NULL DEFAULT '[]',
    sent_at TEXT,
    created_at TEXT NOT NULL
);

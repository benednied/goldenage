CREATE TABLE app_user (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL
);

CREATE TABLE app_group (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE user_group_membership (
    user_id UUID NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    group_id UUID NOT NULL REFERENCES app_group (id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);

CREATE TABLE case_file (
    id UUID PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT,
    primary_contact TEXT,
    status TEXT NOT NULL,
    last_activity_at TIMESTAMPTZ NOT NULL,
    visible_group_id UUID REFERENCES app_group (id)
);

CREATE TABLE activity (
    id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES case_file (id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    kind TEXT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    created_by UUID REFERENCES app_user (id)
);

CREATE INDEX activity_due_idx ON activity (due_at) WHERE completed_at IS NULL;

CREATE TABLE artifact (
    id UUID PRIMARY KEY,
    file_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    content_text TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL,
    uploaded_by UUID REFERENCES app_user (id),
    assigned_case_id UUID REFERENCES case_file (id)
);

CREATE TABLE assignment_suggestion (
    artifact_id UUID PRIMARY KEY REFERENCES artifact (id) ON DELETE CASCADE,
    suggested_case_id UUID REFERENCES case_file (id),
    summary_reason TEXT NOT NULL,
    confidence NUMERIC(4, 3) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE audit_event (
    id UUID PRIMARY KEY,
    actor_user_id UUID REFERENCES app_user (id),
    event_type TEXT NOT NULL,
    subject_id UUID NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

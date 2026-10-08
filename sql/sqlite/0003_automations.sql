CREATE TABLE automation (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    code_version TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    effective_user_id TEXT NOT NULL REFERENCES app_user (id),
    filesystem_paths_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX automation_owner_idx ON automation (owner_user_id, updated_at DESC);

CREATE TABLE automation_trigger (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL REFERENCES automation (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    schedule_expression TEXT,
    schedule_timezone TEXT,
    missed_run_policy TEXT NOT NULL DEFAULT 'skip',
    overlap_policy TEXT NOT NULL DEFAULT 'skip',
    event_type TEXT,
    sender_filter TEXT NOT NULL DEFAULT '',
    recipient_filter TEXT NOT NULL DEFAULT '',
    subject_filter TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX automation_trigger_automation_idx ON automation_trigger (automation_id);

CREATE TABLE automation_run (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL REFERENCES automation (id) ON DELETE CASCADE,
    trigger_id TEXT REFERENCES automation_trigger (id) ON DELETE SET NULL,
    owner_user_id TEXT NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    effective_user_id TEXT NOT NULL REFERENCES app_user (id),
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    code_version TEXT NOT NULL,
    configured_paths_json TEXT NOT NULL DEFAULT '[]',
    trigger_label TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    warning_message TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE (automation_id, idempotency_key)
);

CREATE INDEX automation_run_recent_idx ON automation_run (automation_id, created_at DESC);

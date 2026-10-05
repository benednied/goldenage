CREATE TABLE agent_conversation (
    id UUID PRIMARY KEY,
    acting_user_id UUID NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX agent_conversation_user_updated_idx
    ON agent_conversation (acting_user_id, updated_at DESC);

CREATE TABLE agent_run (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES agent_conversation (id) ON DELETE CASCADE,
    acting_user_id UUID NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    actor_kind TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    error TEXT,
    request_id TEXT
);

CREATE INDEX agent_run_conversation_idx ON agent_run (conversation_id, started_at);

CREATE TABLE agent_message (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES agent_conversation (id) ON DELETE CASCADE,
    run_id UUID REFERENCES agent_run (id) ON DELETE SET NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX agent_message_conversation_idx ON agent_message (conversation_id, created_at);

CREATE TABLE agent_tool_call (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES agent_conversation (id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES agent_run (id) ON DELETE CASCADE,
    acting_user_id UUID NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    actor_kind TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    input_json JSONB NOT NULL,
    status TEXT NOT NULL,
    output_json JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    UNIQUE (conversation_id, idempotency_key)
);

CREATE INDEX agent_tool_call_run_idx ON agent_tool_call (run_id, created_at);

ALTER TABLE audit_event
    ADD COLUMN actor_kind TEXT NOT NULL DEFAULT 'user',
    ADD COLUMN acting_user_id UUID REFERENCES app_user (id),
    ADD COLUMN agent_name TEXT,
    ADD COLUMN conversation_id UUID REFERENCES agent_conversation (id),
    ADD COLUMN agent_run_id UUID REFERENCES agent_run (id);

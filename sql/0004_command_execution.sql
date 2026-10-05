CREATE TABLE command_execution (
    actor_user_id UUID NOT NULL,
    operation TEXT NOT NULL,
    command_id UUID NOT NULL,
    request_fingerprint TEXT NOT NULL,
    result_case_id UUID REFERENCES case_file (id),
    result_activity_id UUID REFERENCES activity (id),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (actor_user_id, operation, command_id)
);

CREATE INDEX command_execution_result_idx
    ON command_execution (result_case_id);

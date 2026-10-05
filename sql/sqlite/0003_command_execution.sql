CREATE TABLE command_execution (
    actor_user_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    command_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    result_case_id TEXT REFERENCES case_file (id),
    result_activity_id TEXT REFERENCES activity (id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (actor_user_id, operation, command_id)
);

CREATE INDEX command_execution_result_idx
    ON command_execution (result_case_id);

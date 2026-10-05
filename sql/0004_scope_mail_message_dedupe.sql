ALTER TABLE mail_message
    ADD COLUMN dedupe_scope_user_id UUID REFERENCES app_user (id);

UPDATE mail_message AS mm
SET dedupe_scope_user_id = a.uploaded_by
FROM artifact AS a
WHERE a.id = mm.artifact_id;

DROP INDEX mail_message_dedupe_idx;

CREATE UNIQUE INDEX mail_message_dedupe_scope_idx
    ON mail_message (
        dedupe_fingerprint,
        COALESCE(dedupe_scope_user_id, '00000000-0000-0000-0000-000000000000'::uuid)
    );

ALTER TABLE mail_conversation
    ADD COLUMN user_id UUID REFERENCES app_user (id) ON DELETE CASCADE;

UPDATE mail_conversation mc
SET user_id = a.uploaded_by
FROM artifact a
WHERE a.id = mc.latest_artifact_id
  AND mc.user_id IS NULL;

ALTER TABLE mail_message
    ADD COLUMN user_id UUID REFERENCES app_user (id) ON DELETE CASCADE;

UPDATE mail_message mm
SET user_id = a.uploaded_by
FROM artifact a
WHERE a.id = mm.artifact_id
  AND mm.user_id IS NULL;

DROP INDEX mail_message_dedupe_idx;

CREATE UNIQUE INDEX mail_message_user_dedupe_idx
    ON mail_message (user_id, dedupe_fingerprint)
    WHERE user_id IS NOT NULL;

CREATE UNIQUE INDEX mail_message_user_source_idx
    ON mail_message (user_id, source_kind, source_account_id, source_folder_id, source_message_id)
    WHERE user_id IS NOT NULL
      AND source_account_id IS NOT NULL
      AND source_folder_id IS NOT NULL
      AND source_message_id IS NOT NULL;

CREATE UNIQUE INDEX mail_message_user_internet_idx
    ON mail_message (user_id, source_kind, internet_message_id)
    WHERE user_id IS NOT NULL
      AND internet_message_id IS NOT NULL;

CREATE UNIQUE INDEX mail_conversation_user_external_idx
    ON mail_conversation (user_id, source_kind, external_conversation_id)
    WHERE user_id IS NOT NULL
      AND external_conversation_id IS NOT NULL;

CREATE INDEX mail_message_conversation_identity_idx
    ON mail_message (source_kind, source_account_id, source_conversation_id, conversation_id);

CREATE INDEX mail_conversation_subject_idx
    ON mail_conversation (source_kind, normalized_subject);

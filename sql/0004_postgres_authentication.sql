ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS password_hash TEXT;

ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS profile_image_path TEXT;

CREATE INDEX IF NOT EXISTS app_user_email_lower_idx
    ON app_user (lower(email));

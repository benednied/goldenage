-- Accepted master data is deliberately separate from mail-derived hints.
-- Existing case strings remain untouched; legacy_case_party_hint makes the
-- upgrade queue explicit for a later user-confirmed link.
CREATE TABLE company (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    created_by UUID REFERENCES app_user (id),
    visible_group_id UUID REFERENCES app_group (id)
);

CREATE UNIQUE INDEX company_public_name_idx
    ON company (normalized_name) WHERE visible_group_id IS NULL;
CREATE UNIQUE INDEX company_group_name_idx
    ON company (visible_group_id, normalized_name) WHERE visible_group_id IS NOT NULL;

CREATE TABLE contact (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES company (id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    normalized_email TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    created_by UUID REFERENCES app_user (id),
    visible_group_id UUID REFERENCES app_group (id)
);

CREATE UNIQUE INDEX contact_company_email_idx
    ON contact (company_id, normalized_email) WHERE normalized_email IS NOT NULL;
CREATE INDEX contact_company_idx ON contact (company_id);

CREATE TABLE contact_note (
    id UUID PRIMARY KEY,
    contact_id UUID NOT NULL REFERENCES contact (id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    created_by UUID NOT NULL REFERENCES app_user (id),
    visible_group_id UUID REFERENCES app_group (id)
);

CREATE INDEX contact_note_contact_idx ON contact_note (contact_id, created_at DESC);

ALTER TABLE case_file
    ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES company (id) ON DELETE SET NULL;
ALTER TABLE case_file
    ADD COLUMN IF NOT EXISTS primary_contact_id UUID REFERENCES contact (id) ON DELETE SET NULL;
CREATE INDEX case_file_company_idx ON case_file (company_id);
CREATE INDEX case_file_primary_contact_idx ON case_file (primary_contact_id);

CREATE TABLE legacy_case_party_hint (
    id UUID PRIMARY KEY REFERENCES case_file (id) ON DELETE CASCADE,
    case_id UUID NOT NULL UNIQUE REFERENCES case_file (id) ON DELETE CASCADE,
    company_text TEXT,
    contact_text TEXT,
    status TEXT NOT NULL DEFAULT 'unreviewed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ,
    reviewed_by UUID REFERENCES app_user (id)
);

INSERT INTO legacy_case_party_hint (id, case_id, company_text, contact_text)
SELECT id, id, company, primary_contact
FROM case_file
WHERE company IS NOT NULL OR primary_contact IS NOT NULL
ON CONFLICT (id) DO NOTHING;

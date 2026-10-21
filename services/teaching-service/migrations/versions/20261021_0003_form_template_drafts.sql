-- Phase 1 additive draft definitions only. Explicit deployment review required.
-- Never executed by application startup. No historical or owning-service tables change.
CREATE TABLE teaching.form_template_drafts (
    id uuid PRIMARY KEY,
    institution_id uuid NOT NULL CHECK (institution_id <> '00000000-0000-0000-0000-000000000000'),
    revision integer NOT NULL CHECK (revision >= 1),
    document jsonb NOT NULL CHECK (COALESCE((
        jsonb_typeof(document) = 'object'
        AND document ?& ARRAY['id', 'institution_id', 'revision', 'status', 'schema_version', 'questions']
        AND document->>'status' = 'DRAFT'
        AND document->>'schema_version' = '1'
        AND (document->>'id')::uuid = id
        AND (document->>'institution_id')::uuid = institution_id
        AND (document->>'revision')::integer = revision
        AND jsonb_typeof(document->'questions') = 'array'
    ), false)),
    UNIQUE (institution_id, id)
);
CREATE INDEX ix_form_template_drafts_institution_id ON teaching.form_template_drafts(institution_id, id);
ALTER TABLE teaching.form_template_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE teaching.form_template_drafts FORCE ROW LEVEL SECURITY;
CREATE POLICY form_template_drafts_tenant ON teaching.form_template_drafts
    USING (institution_id = NULLIF(current_setting('schedence.institution_id', true), '')::uuid)
    WITH CHECK (institution_id = NULLIF(current_setting('schedence.institution_id', true), '')::uuid);
-- Grant SELECT, INSERT, UPDATE to the reviewed restricted Teaching runtime role separately.
-- No DELETE permission is required. Do not grant to PUBLIC.

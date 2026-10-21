-- Additive Teaching-owned sending records only; never run at application startup.
CREATE TABLE teaching.form_distributions (
    id uuid PRIMARY KEY,
    institution_id uuid NOT NULL CHECK (institution_id <> '00000000-0000-0000-0000-000000000000'),
    template_id uuid NOT NULL,
    template_revision integer NOT NULL CHECK (template_revision >= 1),
    created_by uuid NOT NULL CHECK (created_by <> '00000000-0000-0000-0000-000000000000'),
    idempotency_key varchar(128) NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9._:-]{1,128}$'),
    request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL CHECK (COALESCE((
        jsonb_typeof(document) = 'object'
        AND document ?& ARRAY['id','institution_id','created_by','channel','deadline','created_at','frozen_template','template_sha256','recipient_count']
        AND (document->>'id')::uuid = id
        AND (document->>'institution_id')::uuid = institution_id
        AND (document->>'created_by')::uuid = created_by
        AND document->>'schema_version' = '1'
        AND document->>'channel' IN ('DIRECT','SHARE_LINK')
        AND (document->>'deadline')::timestamptz > (document->>'created_at')::timestamptz
        AND (document->>'recipient_count')::integer > 0
        AND document->>'template_sha256' ~ '^[0-9a-f]{64}$'
        AND (document->'frozen_template'->>'id')::uuid = template_id
        AND (document->'frozen_template'->>'institution_id')::uuid = institution_id
        AND (document->'frozen_template'->>'revision')::integer = template_revision
    ),false)),
    UNIQUE (institution_id,id),
    UNIQUE (institution_id,created_by,idempotency_key),
    FOREIGN KEY (institution_id,template_id) REFERENCES teaching.form_template_drafts(institution_id,id) ON DELETE RESTRICT
);
CREATE TABLE teaching.form_distribution_recipients (
    institution_id uuid NOT NULL,
    distribution_id uuid NOT NULL,
    staff_id uuid NOT NULL CHECK (staff_id <> '00000000-0000-0000-0000-000000000000'),
    identity_user_id uuid,
    document jsonb NOT NULL CHECK (COALESCE((
        jsonb_typeof(document) = 'object'
        AND document ?& ARRAY['staff_id','identity_user_id','link_status','staff_number','full_name','department_ids']
        AND (document->>'staff_id')::uuid = staff_id
        AND (document->>'identity_user_id')::uuid IS NOT DISTINCT FROM identity_user_id
        AND document->>'link_status' IN ('READY','NO_LINK','AMBIGUOUS_LINK')
        AND ((document->>'link_status' = 'NO_LINK') = (identity_user_id IS NULL))
        AND (document->>'link_status' <> 'READY' OR identity_user_id <> '00000000-0000-0000-0000-000000000000')
    ),false)),
    first_opened_at timestamptz,
    PRIMARY KEY (institution_id,distribution_id,staff_id),
    FOREIGN KEY (institution_id,distribution_id) REFERENCES teaching.form_distributions(institution_id,id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX ux_form_recipient_ready_identity
    ON teaching.form_distribution_recipients(institution_id,distribution_id,identity_user_id)
    WHERE document->>'link_status' = 'READY';
CREATE INDEX ix_form_recipient_identity ON teaching.form_distribution_recipients(institution_id,identity_user_id,distribution_id);
ALTER TABLE teaching.form_distributions ENABLE ROW LEVEL SECURITY;
ALTER TABLE teaching.form_distributions FORCE ROW LEVEL SECURITY;
ALTER TABLE teaching.form_distribution_recipients ENABLE ROW LEVEL SECURITY;
ALTER TABLE teaching.form_distribution_recipients FORCE ROW LEVEL SECURITY;
CREATE POLICY form_distributions_tenant ON teaching.form_distributions
    USING (institution_id = NULLIF(current_setting('schedence.institution_id',true),'')::uuid)
    WITH CHECK (institution_id = NULLIF(current_setting('schedence.institution_id',true),'')::uuid);
CREATE POLICY form_recipients_tenant ON teaching.form_distribution_recipients
    USING (institution_id = NULLIF(current_setting('schedence.institution_id',true),'')::uuid)
    WITH CHECK (institution_id = NULLIF(current_setting('schedence.institution_id',true),'')::uuid);
CREATE FUNCTION teaching.protect_form_distribution() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Sent form definitions are immutable';
END;
$$;
CREATE TRIGGER immutable_form_distribution BEFORE UPDATE OR DELETE ON teaching.form_distributions
    FOR EACH ROW EXECUTE FUNCTION teaching.protect_form_distribution();
CREATE FUNCTION teaching.protect_form_recipient() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Sent form recipients are immutable';
    END IF;
    IF (to_jsonb(NEW) - 'first_opened_at') IS DISTINCT FROM (to_jsonb(OLD) - 'first_opened_at')
        OR (OLD.first_opened_at IS NOT NULL AND NEW.first_opened_at IS DISTINCT FROM OLD.first_opened_at)
        OR NEW.first_opened_at IS NULL OR NEW.document->>'link_status' <> 'READY' THEN
        RAISE EXCEPTION 'Only the first open of an authorized recipient may be recorded';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM teaching.form_distributions d WHERE d.institution_id=NEW.institution_id
        AND d.id=NEW.distribution_id AND (d.document->>'deadline')::timestamptz > clock_timestamp()) THEN
        RAISE EXCEPTION 'Form deadline has passed';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER immutable_form_recipient BEFORE UPDATE OR DELETE ON teaching.form_distribution_recipients
    FOR EACH ROW EXECUTE FUNCTION teaching.protect_form_recipient();
-- Explicit reviewed runtime grants: SELECT,INSERT on both tables; UPDATE(first_opened_at) on recipients only.
-- No PUBLIC grants, UPDATE on sent definitions, or DELETE privileges.

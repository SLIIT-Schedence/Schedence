"""Reconcile the approved selective Forms deployment with the Teaching Alembic chain.

Default: restore verified backup locally, compare existing Forms objects against
frozen revision SQL, run missing DDL/grants/marker in a rolled-back transaction.
--apply requires that exact dry-run. No academic/history DML or solver calls.
"""
import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy.engine import make_url

from backup_existing_neon import LOCAL_CONFIG, fingerprint_query, fingerprints, pg_env, run, table_names
from dev_seed import ROOT, canonical, connect, database_identity, read_url
from plan_existing_neon import CURRENT, NEON, OLD

MIGRATIONS = ROOT / 'services/teaching-service/migrations/versions'
PRIVATE = ROOT / 'scripts/neon-review'
OUT = PRIVATE / 'teaching-schema-reconciliation-20261021'
FORMS = ('form_template_drafts','form_distributions','form_distribution_recipients')
QUEUES = ('generation_jobs','generation_job_attempts')
ROLE = 'schedence_teaching_runtime'


def q(value):
    return "'" + value.replace("'", "''") + "'"


def shape(schema, table):
    relation = q(schema+'.'+table)+'::regclass'
    def normalize(expression):
        return f"replace(({expression}),{q(schema+'.')},'teaching.')"
    return f"""jsonb_build_object(
      'columns',(SELECT jsonb_agg(jsonb_build_array(a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
          a.attidentity,a.attgenerated,coalesce(pg_get_expr(d.adbin,d.adrelid),'')) ORDER BY a.attnum)
          FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
          WHERE a.attrelid={relation} AND a.attnum>0 AND NOT a.attisdropped),
      'constraints',(SELECT jsonb_agg(jsonb_build_array(conname,contype,convalidated,
          {normalize('pg_get_constraintdef(oid)')}) ORDER BY conname) FROM pg_constraint WHERE conrelid={relation}),
      'indexes',(SELECT jsonb_agg({normalize('pg_get_indexdef(indexrelid)')} ORDER BY indexrelid::regclass::text)
          FROM pg_index WHERE indrelid={relation}),
      'policies',(SELECT jsonb_agg(jsonb_build_array(polname,polcmd,polpermissive,
          pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid),
          ARRAY(SELECT CASE WHEN x=0 THEN 'public' ELSE x::regrole::text END FROM unnest(polroles) x)) ORDER BY polname)
          FROM pg_policy WHERE polrelid={relation}),
      'triggers',(SELECT jsonb_agg({normalize('pg_get_triggerdef(oid)')} ORDER BY tgname)
          FROM pg_trigger WHERE tgrelid={relation} AND NOT tgisinternal),
      'rls',(SELECT jsonb_build_array(relrowsecurity,relforcerowsecurity) FROM pg_class WHERE oid={relation}))"""


def shapes_query(schema='teaching'):
    pairs = [q(table)+','+shape(schema, table) for table in FORMS]
    for function in ('protect_form_distribution','protect_form_recipient'):
        # PostgreSQL preserves function-body CRLF. Ignore platform line endings,
        # while comparing the full function, trigger, constraint and policy code.
        pairs.append(q(function)+",replace(replace(pg_get_functiondef("+q(schema+'.'+function+'()')+
            "::regprocedure),chr(13),''),"+q(schema+'.')+",'teaching.')")
    return 'SELECT jsonb_build_object('+','.join(pairs)+')'


def migration_sql(expected):
    ddl = (MIGRATIONS/'20261016_0002_generation_jobs.sql').read_text()
    commands = []
    for table in QUEUES:
        commands.append(f'GRANT SELECT,INSERT,UPDATE ON teaching.{table} TO {ROLE}')
        commands.append(f'REVOKE ALL ON teaching.{table} FROM PUBLIC')
    commands += [f'GRANT USAGE ON SCHEMA teaching TO {ROLE}',
        f'GRANT SELECT,INSERT,UPDATE ON teaching.form_template_drafts TO {ROLE}',
        f'GRANT SELECT,INSERT ON teaching.form_distributions,teaching.form_distribution_recipients TO {ROLE}',
        f'GRANT UPDATE(first_opened_at) ON teaching.form_distribution_recipients TO {ROLE}']
    commands.append("UPDATE teaching.alembic_version SET version_num='20261021_0004' WHERE version_num='20261016_0001'")
    return f"""DO $migration$ DECLARE revision text; actual jsonb; BEGIN
      IF NOT coalesce((current_database()='neondb' AND current_setting('neon.project_id',true)='super-leaf-21551515'
          AND current_setting('neon.branch_id',true)='br-dry-darkness-b549ci92') OR
          (current_database()='postgres' AND inet_server_addr()='127.0.0.1'::inet
           AND current_setting('schedence.transactional_restore',true)='on'),false) THEN
          RAISE EXCEPTION 'Wrong database target'; END IF;
      SELECT version_num INTO STRICT revision FROM teaching.alembic_version;
      IF revision NOT IN ('20261016_0001','20261021_0004') THEN RAISE EXCEPTION 'Unexpected migration revision'; END IF;
      {shapes_query()} INTO actual;
      IF actual IS DISTINCT FROM {q(canonical(expected))}::jsonb THEN
          RAISE EXCEPTION 'Existing Forms schema differs from frozen migrations; no adoption permitted'; END IF;
      IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{ROLE}' AND NOT rolsuper AND NOT rolbypassrls
          AND NOT rolcreaterole AND NOT rolcreatedb) THEN RAISE EXCEPTION 'Runtime role is not restricted'; END IF;
      IF revision='20261021_0004' THEN RETURN; END IF;
      IF to_regclass('teaching.generation_jobs') IS NOT NULL OR to_regclass('teaching.generation_job_attempts') IS NOT NULL
          THEN RAISE EXCEPTION 'Unrecognized queue schema'; END IF;
      EXECUTE {q(ddl)};
      {''.join('EXECUTE '+q(command)+';' for command in commands)}
    END $migration$;\n"""


def verification(baseline):
    protected = {k:v for k,v in baseline.items() if k!='teaching.alembic_version'}
    query = fingerprint_query([tuple(k.split('.')) for k in protected]).as_string()
    result = f"""DO $verify$ DECLARE actual jsonb; BEGIN
      SELECT jsonb_object_agg(table_name,jsonb_build_object('rows',rows,'sha256',sha256)) INTO actual FROM ({query}) data;
      IF actual IS DISTINCT FROM {q(canonical(protected))}::jsonb THEN RAISE EXCEPTION 'Existing data changed'; END IF;
      IF (SELECT version_num FROM teaching.alembic_version)<>'20261021_0004' THEN RAISE EXCEPTION 'Wrong revision'; END IF;
      IF EXISTS(SELECT 1 FROM pg_constraint WHERE connamespace='teaching'::regnamespace AND NOT convalidated)
          THEN RAISE EXCEPTION 'Unvalidated Teaching constraint'; END IF;
      IF (SELECT count(*) FROM teaching.generation_jobs)<>0 OR (SELECT count(*) FROM teaching.generation_job_attempts)<>0
          THEN RAISE EXCEPTION 'Unexpected generation jobs'; END IF;
    END $verify$;
    SET LOCAL ROLE {ROLE};
    """
    for tenant, forms in ((CURRENT,4),(OLD,0),('',0)):
        result += f"SELECT set_config('schedence.institution_id',{q(tenant)},true);\n"
        result += f"DO $rls$ BEGIN IF (SELECT count(*) FROM teaching.form_template_drafts)<>{forms} THEN RAISE EXCEPTION 'Forms tenant isolation failed'; END IF; END $rls$;\n"
        for table in QUEUES:
            result += f"SELECT count(*) FROM teaching.{table};\n"
    return result+'RESET ROLE;\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup',required=True,type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    backup_dir=args.backup.resolve()
    if backup_dir.parent!=PRIVATE.resolve(): raise ValueError('Private verified backup path required')
    backup=json.loads((backup_dir/'manifest.json').read_text())
    if (backup['status']!='PASS' or backup['target']!=NEON or
        hashlib.sha256((backup_dir/'neondb.dump').read_bytes()).hexdigest()!=backup['sha256']):
        raise ValueError('Verified backup gate failed')
    baseline=backup['tables']
    target=read_url(ROOT/'.env','DIRECT_DATABASE_URL')
    with connect(target) as conn:
        if database_identity(conn)!=NEON or fingerprints(conn,[tuple(k.split('.')) for k in baseline])!=baseline:
            raise ValueError('Target or existing data changed since backup')
    OUT.mkdir(exist_ok=True)
    if args.apply:
        dry=json.loads((OUT/'dry-run.json').read_text())
        if dry['status']!='PASS' or dry['backup_sha256']!=backup['sha256']:
            raise ValueError('Matching dry-run is required')
        body=migration_sql(dry['expected_forms'])
        if hashlib.sha256(body.encode()).hexdigest()!=dry['migration_sha256']:
            raise ValueError('Migration changed since dry-run')
        with connect(target,readonly=False) as conn:
            conn.execute("SET LOCAL lock_timeout='3s'")
            conn.execute("SET LOCAL statement_timeout='45s'")
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('schedence-teaching-chain-reconciliation'))")
            if fingerprints(conn,[tuple(k.split('.')) for k in baseline])!=baseline:
                raise ValueError('Concurrent data change at write gate')
            conn.execute(body,prepare=False)
            conn.execute(verification(baseline),prepare=False)
        with connect(target) as conn:
            protected={k:v for k,v in baseline.items() if k!='teaching.alembic_version'}
            if fingerprints(conn,[tuple(k.split('.')) for k in protected])!=protected:
                raise ValueError('Post-commit data check failed')
            version=conn.execute('SELECT version_num FROM teaching.alembic_version').fetchone()[0]
        result=dict(status='PASS',revision=version,new_queue_tables=2,existing_forms_adopted=3,
            existing_data_rows_changed=0,alembic_metadata_rows_updated=1,protected_tables_unchanged=len(protected),
            jobs_created=0,backup_sha256=backup['sha256'])
        (OUT/'applied.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2)); return
    local=make_url(json.loads(LOCAL_CONFIG.read_text())['admin_url']).set(database='postgres')
    with connect(local) as conn:
        if table_names(conn): raise ValueError('Local maintenance database is not empty')
    prefix="\\set ON_ERROR_STOP on\nBEGIN;\n"+(backup_dir/'restore.sql').read_text(encoding='utf-8')
    reference="\nSET LOCAL row_security=on;\nSET LOCAL schedence.transactional_restore='on';\nCREATE SCHEMA teaching_expected;\n"
    for name in ('20261021_0003_form_template_drafts.sql','20261021_0004_form_sending.sql'):
        reference+=(MIGRATIONS/name).read_text().replace('teaching.','teaching_expected.')+'\n'
    probe=OUT/'expected.sql'
    probe.write_text(prefix+reference+"SELECT 'FORMS_EXPECTED:'||("+shapes_query('teaching_expected')+")::text;\nROLLBACK;\n",encoding='utf-8',newline='\n')
    output=run(['psql.exe','-X','--quiet','--tuples-only','--no-align','--file='+str(probe)],pg_env(local),OUT,'expected')
    line=next(line for line in output.stdout.decode().splitlines() if line.startswith('FORMS_EXPECTED:'))
    expected=json.loads(line.removeprefix('FORMS_EXPECTED:'))
    body=migration_sql(expected)
    wrapper=OUT/'dry-run.sql'
    wrapper.write_text(prefix+"\nSET LOCAL row_security=on;\nSET LOCAL schedence.transactional_restore='on';\n"+
        body+verification(baseline)+body+verification(baseline)+"ROLLBACK;\nSELECT 'RECONCILIATION_VERIFIED_AND_ROLLED_BACK';\n",encoding='utf-8',newline='\n')
    result=run(['psql.exe','-X','--quiet','--file='+str(wrapper)],pg_env(local),OUT,'dry-run')
    if b'RECONCILIATION_VERIFIED_AND_ROLLED_BACK' not in result.stdout: raise ValueError('Dry-run failed')
    with connect(local) as conn:
        if table_names(conn): raise ValueError('Local restore did not roll back')
    report=dict(status='PASS',backup_sha256=backup['sha256'],expected_forms=expected,
        migration_sha256=hashlib.sha256(body.encode()).hexdigest(),repeat_idempotent=True,existing_data_unchanged=True)
    (OUT/'dry-run.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='expected_forms'},indent=2))


if __name__=='__main__': main()

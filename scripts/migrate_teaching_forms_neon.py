"""Narrow approved additive migration. Default prepares and rollback-tests; --apply requires PASS.

No existing-table DML, Alembic marker updates, solver, database creation or replacement views.
Private payloads and full restore SQL remain in ignored scripts/neon-review/.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from psycopg import sql
from sqlalchemy.engine import make_url

from backup_existing_neon import BIN, LOCAL_CONFIG, fingerprint_query, fingerprints, pg_env, run, table_names
from compare_academic_records import snapshot_rows
from dev_seed import canonical, connect, database_identity, read_url
from plan_existing_neon import CURRENT, NEON, OLD, ROOT
from teaching_migration_eligibility import review
from teaching_shared_plan import CORE, OWNER, READER, quote, simple

PRIVATE = ROOT / "scripts/neon-review"
OUT = PRIVATE / "approved-additive-migration-20261021"
BACKUP = PRIVATE / "backup-20261021T060734Z"
RUNTIME = "schedence_teaching_runtime"
NEW_TABLES = ("form_template_drafts", "form_distributions", "form_distribution_recipients")
MIGRATIONS = ROOT / "services/teaching-service/migrations/versions"


def hash_value(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def reviewed_mappings(neon, local):
    """Existing IDs only: exact source identities plus the four previously reviewed exceptions."""
    manifest = json.loads((ROOT / "database/teaching_shared/reviewed_module_mappings.v1.json").read_text())
    evidence = json.loads((PRIVATE / "approved-migration-mapping-evidence-20261021-v1.json").read_text())
    previous = json.loads((PRIVATE / "module-mapping-evidence-20261021-v2.json").read_text())
    for current in evidence["academic"]:
        old = next(row for row in previous["academic"] if row["id"] == current["id"])
        for key in ("id", "institution_id", "module_code", "module_name", "academic_year", "semester",
                    "degree_type", "source_id", "source_row", "source_sha256", "safe_source_fields", "programs"):
            if current[key] != old[key]:
                raise ValueError("Reviewed module evidence changed: " + key)
    if evidence["examination"] != previous["examination"] or evidence["exam_dataset_owner"] != CURRENT:
        raise ValueError("Reviewed Examination identities changed")
    source = snapshot_rows(local, "teaching_input,module_offerings", "e->>'module_code' AS module_code,"
        "e->>'module_name' AS module_name,(e->>'academic_year')::int AS academic_year,"
        "(e->>'semester')::int AS semester,e->>'degree_type' AS degree_type")
    masters = neon.execute("SELECT to_jsonb(m) FROM academic.modules m WHERE institution_id=%s", (CURRENT,)).fetchall()
    exceptions = {m["academic_code"]: m for m in manifest["mappings"]}
    result = []
    for entry in source:
        matches = [row[0] for row in masters if all(row[0][k] == v for k, v in entry.items())]
        if len(matches) != 1:
            raise ValueError("Ambiguous/missing existing Academic module")
        row = matches[0]
        exam = neon.execute("SELECT module_code,module_name,academic_year,semester FROM examinations.seed_modules "
                            "WHERE module_code=%s", (row["module_code"],)).fetchall()
        exception = exceptions.get(row["module_code"])
        if exception and exception["academic_id"] != row["id"]:
            raise ValueError("Reviewed UUID mismatch")
        if exception:
            exam_code, decision = exception["exam_code"], exception["decision"]
        else:
            if len(exam) != 1 or exam[0] != (row["module_code"], row["module_name"], row["academic_year"], row["semester"]):
                raise ValueError("Unreviewed Examination mapping difference")
            exam_code, decision = row["module_code"], "EXACT_EXISTING_IDENTITY"
        result.append(dict(academic_id=row["id"], module_code=row["module_code"], exam_code=exam_code, decision=decision))
    if len(result) != 91:
        raise ValueError("Reviewed Teaching scope is no longer 91 modules")
    # Statistics is separately reviewed; it is semester 2, outside the 91 semester-1 inputs.
    stats = exceptions["IT2100"]
    if stats["academic_id"] not in {r["academic_id"] for r in result}:
        result.append(dict(academic_id=stats["academic_id"], module_code="IT2100", exam_code="IT2100",
                           decision=stats["decision"]))
    if len({r["module_code"] for r in result}) != len(result):
        raise ValueError("Duplicate code in verified shared contract")
    return sorted(result, key=lambda row: row["module_code"])


def view_contracts(mappings):
    views = copy.deepcopy(CORE)
    del views["module_source_records"]
    allowed = ",".join(quote(row["academic_id"]) + "::uuid" for row in mappings)
    views["modules"] = simple("academic.modules", "id institution_id module_code module_name academic_year semester "
        "degree_type lecture_minutes tutorial_minutes workshop_minutes lab_minutes source_id", where=f"t.id IN ({allowed})")
    views["module_specializations"]["query"] += f" AND t.module_id IN ({allowed})"
    views["teaching_assignments"]["query"] += f" AND a.module_id IN ({allowed})"
    values = ",".join("(" + ",".join(quote(row[k]) if row[k] is not None else "NULL::text"
                       for k in ("academic_id", "module_code", "exam_code", "decision")) + ")" for row in mappings)
    views["reviewed_module_crosswalk"] = dict(
        query=f"SELECT t.institution_id,t.id AS academic_module_id,t.module_code,v.exam_code,v.decision "
              f"FROM academic.modules t JOIN (VALUES {values}) v(id,code,exam_code,decision) "
              f"ON t.id=v.id::uuid AND t.module_code=v.code WHERE "
              f"t.institution_id='{CURRENT}'::uuid AND "
              f"NULLIF(current_setting('schedence.institution_id',true),'')::uuid='{CURRENT}'::uuid",
        sources={"academic.modules": ["id", "institution_id", "module_code"]})
    return views


def compile_migration(views, forms):
    """New objects only; existing source ACLs get narrow new-owner SELECT grants."""
    payload = dict(views=views, forms=forms, new_tables=NEW_TABLES)
    signature = hash_value(payload)
    commands = [
        f"CREATE ROLE {OWNER} NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION",
        f"CREATE ROLE {READER} NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION",
        f"GRANT {OWNER} TO CURRENT_USER",
        f"CREATE SCHEMA teaching_shared AUTHORIZATION {OWNER}",
        "REVOKE ALL ON SCHEMA teaching_shared FROM PUBLIC",
        "CREATE TABLE teaching_shared.integration_migrations(version integer PRIMARY KEY,artifact_sha256 text NOT NULL,"
        "view_fingerprints jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp())",
    ]
    for filename in ("20261021_0003_form_template_drafts.sql", "20261021_0004_form_sending.sql"):
        commands.append((MIGRATIONS / filename).read_text())
    for table in NEW_TABLES:
        commands += [f"REVOKE ALL ON teaching.{table} FROM PUBLIC", f"GRANT SELECT,INSERT ON teaching.{table} TO {RUNTIME}"]
    commands += [f"GRANT USAGE ON SCHEMA teaching TO {RUNTIME}",
                 f"GRANT UPDATE ON teaching.form_template_drafts TO {RUNTIME}",
                 f"GRANT UPDATE(first_opened_at) ON teaching.form_distribution_recipients TO {RUNTIME}"]
    for row in forms:
        commands.append("INSERT INTO teaching.form_template_drafts SELECT * FROM jsonb_populate_record("
                        "NULL::teaching.form_template_drafts," + quote(canonical(row)) + "::jsonb)")
    sources = {}
    for spec in views.values():
        for table, fields in spec["sources"].items():
            sources.setdefault(table, set()).update(fields)
    for schema in sorted({t.split(".")[0] for t in sources}):
        commands.append(f"GRANT USAGE ON SCHEMA {schema} TO {OWNER}")
    for table, fields in sorted(sources.items()):
        commands.append(f"GRANT SELECT ({','.join(sorted(fields))}) ON {table} TO {OWNER}")
    commands.append(f"SET LOCAL ROLE {OWNER}")
    for view, spec in views.items():
        commands += [f"CREATE VIEW teaching_shared.{view} WITH (security_barrier=true,security_invoker=false) AS {spec['query']}",
                     f"REVOKE ALL ON teaching_shared.{view} FROM PUBLIC", f"GRANT SELECT ON teaching_shared.{view} TO {READER}"]
    commands += [f"GRANT USAGE ON SCHEMA teaching_shared TO {READER}", "RESET ROLE",
                 f"REVOKE {OWNER} FROM CURRENT_USER", f"GRANT {READER} TO {RUNTIME}"]
    names = ",".join(quote(name) for name in views)
    commands.append(f"INSERT INTO teaching_shared.integration_migrations(version,artifact_sha256,view_fingerprints) "
        f"SELECT 1,{quote(signature)},jsonb_object_agg(c.relname,md5(pg_get_viewdef(c.oid,true))) "
        f"FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='teaching_shared' AND c.relname IN ({names})")
    ddl = "\n".join("EXECUTE " + quote(command) + ";" for command in commands)
    guards = " OR ".join("to_regclass('teaching." + name + "') IS NOT NULL" for name in NEW_TABLES)
    body = f"""DO $migration$ DECLARE existing text; item record; BEGIN
IF NOT coalesce(((current_database()='neondb' AND current_setting('neon.project_id',true)='super-leaf-21551515'
    AND current_setting('neon.branch_id',true)='br-dry-darkness-b549ci92') OR
    (current_database()='postgres' AND inet_server_addr()='127.0.0.1'::inet
     AND current_setting('schedence.transactional_restore',true)='on')),false) THEN
    RAISE EXCEPTION 'Wrong migration target'; END IF;
IF NOT EXISTS(SELECT 1 FROM identity.institutions WHERE id='{CURRENT}'::uuid AND code='SLIIT-MALABE' AND status='ACTIVE')
    THEN RAISE EXCEPTION 'Canonical active institution missing'; END IF;
IF to_regclass('teaching_shared.integration_migrations') IS NOT NULL THEN
    SELECT artifact_sha256 INTO existing FROM teaching_shared.integration_migrations WHERE version=1;
    IF existing IS DISTINCT FROM '{signature}' THEN RAISE EXCEPTION 'Migration artifact mismatch'; END IF;
    FOR item IN SELECT key,value FROM teaching_shared.integration_migrations m,
        jsonb_each_text(m.view_fingerprints) WHERE version=1 LOOP
        IF to_regclass('teaching_shared.'||quote_ident(item.key)) IS NULL OR
           md5(pg_get_viewdef(to_regclass('teaching_shared.'||quote_ident(item.key)),true))<>item.value THEN
           RAISE EXCEPTION 'Installed view drift'; END IF;
    END LOOP;
    RETURN;
END IF;
IF to_regnamespace('teaching_shared') IS NOT NULL OR {guards} OR
    EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ('{OWNER}','{READER}')) THEN
    RAISE EXCEPTION 'Unrecognized existing objects; no replacement permitted'; END IF;
{ddl}
END $migration$;
"""
    return body, signature


def permission_preflight(conn, views):
    """Check live grant authority before attempting any additive DDL."""
    role = conn.execute("SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user").fetchone()
    if not role or not role[0] or not conn.execute(
            "SELECT has_schema_privilege(current_user,'teaching','CREATE')").fetchone()[0]:
        raise ValueError("Migration role lacks required role/schema authority")
    runtime = conn.execute("SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname=%s", (RUNTIME,)).fetchone()
    if not runtime or any(runtime):
        raise ValueError("Teaching runtime role missing or elevated")
    sources = {}
    for spec in views.values():
        for table, fields in spec["sources"].items():
            sources.setdefault(table, set()).update(fields)
    checks = []
    for schema in sorted({name.split('.')[0] for name in sources}):
        checks.append(sql.SQL("SELECT has_schema_privilege(current_user,{},'USAGE WITH GRANT OPTION')").format(sql.Literal(schema)))
    for table, fields in sources.items():
        for field in fields:
            checks.append(sql.SQL("SELECT has_column_privilege(current_user,{}, {},'SELECT WITH GRANT OPTION')").format(
                sql.Literal(table), sql.Literal(field)))
    if not all(row[0] for row in conn.execute(sql.SQL(" UNION ALL ").join(checks)).fetchall()):
        raise ValueError("Ambiguous source permission: no authority for required column-only reader grants")


def verification_sql(views, forms, baseline):
    """Fail inside the transaction on any existing-data change or isolation/permission defect."""
    pairs = [tuple(name.split(".")) for name in baseline]
    query = fingerprint_query(pairs).as_string()
    sql_text = "DO $check$ DECLARE actual jsonb; BEGIN\n"
    sql_text += "SELECT jsonb_object_agg(table_name,jsonb_build_object('rows',rows,'sha256',sha256)) INTO actual FROM (" + query + ") f;\n"
    sql_text += "IF actual IS DISTINCT FROM " + quote(canonical(baseline)) + "::jsonb THEN RAISE EXCEPTION 'Existing data changed'; END IF;\n"
    sql_text += "IF (SELECT count(*) FROM teaching.form_template_drafts)<>4 OR EXISTS(SELECT 1 FROM teaching.form_template_drafts WHERE institution_id<>'" + CURRENT + "'::uuid) THEN RAISE EXCEPTION 'Wrong imported form count or tenant'; END IF;\n"
    for row in forms:
        sql_text += "IF NOT EXISTS(SELECT 1 FROM teaching.form_template_drafts t WHERE to_jsonb(t)=" + quote(canonical(row)) + "::jsonb) THEN RAISE EXCEPTION 'Imported form changed'; END IF;\n"
    sql_text += "IF EXISTS(SELECT 1 FROM pg_constraint WHERE connamespace='teaching'::regnamespace AND NOT convalidated) THEN RAISE EXCEPTION 'Unvalidated FK/check'; END IF;\n"
    sql_text += f"IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ('{OWNER}','{READER}') AND (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole OR rolcreatedb)) THEN RAISE EXCEPTION 'Elevated shared role'; END IF;\n"
    for table in NEW_TABLES:
        sql_text += f"IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid='teaching.{table}'::regclass AND relrowsecurity AND relforcerowsecurity) THEN RAISE EXCEPTION 'Forms RLS not forced'; END IF;\n"
        sql_text += f"IF has_table_privilege('{READER}','teaching.{table}','SELECT') THEN RAISE EXCEPTION 'Private Forms exposed'; END IF;\n"
    sql_text += "END $check$;\n"
    sql_text += f"SET LOCAL ROLE {READER};\n"
    for tenant, expected in ((CURRENT, "positive"), (OLD, "zero"), ("", "zero")):
        sql_text += "SELECT set_config('schedence.institution_id'," + quote(tenant) + ",true);\n"
        for view in views:
            sql_text += f"DO $isolation$ BEGIN IF "
            if expected == "zero":
                sql_text += f"(SELECT count(*) FROM teaching_shared.{view})<>0"
            elif view in {"modules", "academic_staff", "venues", "reviewed_module_crosswalk"}:
                count = {"modules": 92, "academic_staff": 110, "venues": 62, "reviewed_module_crosswalk": 92}[view]
                sql_text += f"(SELECT count(*) FROM teaching_shared.{view})<>{count}"
            else:
                # Force query execution and privilege checking for legitimately empty contracts.
                sql_text += f"(SELECT count(*) FROM teaching_shared.{view})<0"
            sql_text += " THEN RAISE EXCEPTION 'Shared view count/isolation failed'; END IF; END $isolation$;\n"
    sql_text += "RESET ROLE;\n"
    sql_text += f"SET LOCAL ROLE {RUNTIME};\n"
    for tenant, count in ((CURRENT, 4), (OLD, 0), ("", 0)):
        sql_text += "SELECT set_config('schedence.institution_id'," + quote(tenant) + ",true);\n"
        sql_text += f"DO $forms$ BEGIN IF (SELECT count(*) FROM teaching.form_template_drafts)<>{count} THEN RAISE EXCEPTION 'Forms tenant isolation failed'; END IF; END $forms$;\n"
    sql_text += "RESET ROLE;\n"
    return sql_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    backup = json.loads((BACKUP / "verified-manifest.json").read_text())
    if (backup["status"] != "PASS" or backup["target"] != NEON or
            hashlib.sha256((BACKUP / "neondb.dump").read_bytes()).hexdigest() != backup["sha256"]):
        raise ValueError("Fresh verified backup gate failed")
    eligibility = review()
    if (eligibility["approved_candidates"], eligibility["eligible"], eligibility["skipped_experimental"]) != (335, 4, 331):
        raise ValueError("Eligibility scope changed")
    local_url = make_url(json.loads(LOCAL_CONFIG.read_text())["admin_url"])
    neon_url = read_url(ROOT / ".env", "DIRECT_DATABASE_URL")
    with connect(neon_url) as neon, connect(local_url) as local:
        if database_identity(neon) != NEON:
            raise ValueError("Wrong target")
        baseline = fingerprints(neon, [tuple(name.split(".")) for name in backup["tables"]])
        if baseline != backup["tables"]:
            raise ValueError("Existing data changed since verified backup")
        mappings = reviewed_mappings(neon, local)
        wanted = {r["id"]: r for r in eligibility["forms"]}
        forms = [r[0] for r in local.execute("SELECT to_jsonb(t) FROM teaching.form_template_drafts t WHERE institution_id=%s ORDER BY id", (CURRENT,))
                 if r[0]["id"] in wanted]
        for row in forms:
            digest = local.execute("SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') "
                "FROM teaching.form_template_drafts t WHERE id=%s", (row["id"],)).fetchone()[0]
            if digest != wanted[row["id"]]["row_sha256"]:
                raise ValueError("Source form changed during preparation")
            for actor in {row["document"]["created_by"], row["document"]["updated_by"]}:
                if not neon.execute("SELECT EXISTS(SELECT 1 FROM identity.users WHERE id=%s)", (actor,)).fetchone()[0]:
                    raise ValueError("Form actor identity missing")
        if len(forms) != 4:
            raise ValueError("Missing eligible Forms payload")
    views = view_contracts(mappings)
    with connect(neon_url) as conn:
        permission_preflight(conn, views)
    body, signature = compile_migration(views, forms)
    checks = verification_sql(views, forms, baseline)
    OUT.mkdir(exist_ok=True)
    report_path = OUT / "dry-run.json"
    if args.apply:
        dry_run = json.loads(report_path.read_text())
        if dry_run["status"] != "PASS" or dry_run["artifact_sha256"] != signature:
            raise ValueError("Exact artifact has not passed transactional restore dry-run")
        with connect(neon_url, readonly=False) as conn:
            conn.execute("SET LOCAL lock_timeout='3s'")
            conn.execute("SET LOCAL statement_timeout='45s'")
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('schedence-approved-teaching-additive'))")
            if fingerprints(conn, [tuple(name.split(".")) for name in baseline]) != baseline:
                raise ValueError("Source changed at apply gate")
            conn.execute(body, prepare=False)
            conn.execute(checks, prepare=False)
        with connect(neon_url) as conn:
            if fingerprints(conn, [tuple(name.split(".")) for name in baseline]) != baseline:
                raise ValueError("Post-commit data verification failed")
            result = dict(status="PASS", inserted_forms=4, inserted_ledger=1, tables_created=3,
                shared_views=len(views), reused_teaching_modules=91, separately_verified_statistics=1,
                skipped_experimental=331, skipped_unreviewed=1, artifact_sha256=signature,
                existing_tables_unchanged=len(baseline), existing_rows_unchanged=sum(r["rows"] for r in baseline.values()),
                target=NEON, completed_utc=datetime.now(timezone.utc).isoformat())
        (OUT / "applied.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
        return
    local_maintenance = local_url.set(database="postgres")
    with connect(local_maintenance) as conn:
        if table_names(conn):
            raise ValueError("Local maintenance database not empty")
    wrapper = OUT / "dry-run.sql"
    with wrapper.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\\set ON_ERROR_STOP on\nBEGIN;\nSET LOCAL timezone='UTC';\n")
        handle.write((BACKUP / "restore.sql").read_text(encoding="utf-8"))
        # pg_restore sets row_security=off for the superuser data load. Restore
        # normal policy enforcement before exercising the restricted roles.
        handle.write("\nSET LOCAL row_security=on;\nSET LOCAL schedence.transactional_restore='on';\n")
        handle.write(body + checks + body + checks)  # Exact repeat must be a no-op.
        handle.write("\nROLLBACK;\nSELECT 'ADDITIVE_DRY_RUN_VERIFIED_AND_ROLLED_BACK';\n")
    result = run(["psql.exe", "-X", "--quiet", "--file=" + str(wrapper)], pg_env(local_maintenance), OUT, "dry-run", 900)
    if b"ADDITIVE_DRY_RUN_VERIFIED_AND_ROLLED_BACK" not in result.stdout:
        raise ValueError("Dry-run marker missing")
    with connect(local_maintenance) as conn:
        if table_names(conn) or conn.execute("SELECT count(*) FROM pg_roles WHERE rolname IN (%s,%s)", (OWNER, READER)).fetchone()[0]:
            raise ValueError("Local transactional dry-run did not fully roll back")
    report = dict(status="PASS", artifact_sha256=signature, backup_sha256=backup["sha256"],
                  all_existing_row_hashes_unchanged=True, forced_rls=True, tenant_isolation=True,
                  private_forms_not_shared=True, repeat_idempotent=True, rolled_back=True,
                  forms=4, excluded_experimental=331, extra_unreviewed=1,
                  module_crosswalk=mappings, views=views,
                  prepared_utc=datetime.now(timezone.utc).isoformat())
    report_path.write_text(json.dumps(report, indent=2))
    (OUT / "apply.sql").write_text("BEGIN;\n" + body + checks + "COMMIT;\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"views", "module_crosswalk"}}, indent=2))


if __name__ == "__main__":
    main()

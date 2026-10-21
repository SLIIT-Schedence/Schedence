"""READ ONLY audit/insert dry-run for the existing Neon database. No apply mode.

Only hashes, counts and schema metadata leave PostgreSQL. This tool never
copies rows, reassigns institutions, executes migrations or disables RLS.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from psycopg import sql
from sqlalchemy.engine import make_url

from dev_seed import connect, database_identity, read_url, canonical

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "scripts/neon-review"
CURRENT = "84651d6d-0c8a-470a-a798-5220666fd367"
OLD = "44dc54c1-35df-4e53-8fc0-95a7d911368b"
NEON = {"database": "neondb", "neon_project_id": "super-leaf-21551515",
        "neon_branch_id": "br-dry-darkness-b549ci92"}
LOCAL = {"database": "schedence_step12b4_test", "neon_project_id": None, "neon_branch_id": None}


def name(table):
    return sql.Identifier(*table.split("."))


def sha(expression):
    return sql.SQL("encode(sha256(convert_to(({})::text,'UTF8')),'hex')").format(expression)


def tenant(conn, value):
    conn.execute("SELECT set_config('schedence.institution_id',%s,true)", (value,))


def catalog(conn):
    result = {}
    tables = conn.execute(
        "SELECT n.nspname,c.relname,c.relrowsecurity,c.relforcerowsecurity "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%' "
        "AND n.nspname<>'information_schema' ORDER BY 1,2").fetchall()
    for schema, table, rls, force in tables:
        qualified = schema + "." + table
        cols = conn.execute(
            "SELECT attname,format_type(atttypid,atttypmod) FROM pg_attribute "
            "WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum",
            (qualified,)).fetchall()
        constraints = conn.execute(
            "SELECT conname,contype,pg_get_constraintdef(oid), "
            "ARRAY(SELECT a.attname FROM unnest(conkey) WITH ORDINALITY k(attnum,ord) "
            "JOIN pg_attribute a ON a.attrelid=conrelid AND a.attnum=k.attnum ORDER BY k.ord),"
            "CASE WHEN confrelid<>0 THEN confrelid::regclass::text END "
            "FROM pg_constraint WHERE conrelid=%s::regclass ORDER BY conname", (qualified,)).fetchall()
        indexes = conn.execute(
            "SELECT ci.relname,pg_get_expr(i.indpred,i.indrelid),"
            "ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,ord) "
            "LEFT JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum "
            "WHERE k.ord<=i.indnkeyatts ORDER BY k.ord),"
            "coalesce((to_jsonb(i)->>'indnullsnotdistinct')::boolean,false),pg_get_indexdef(i.indexrelid) "
            "FROM pg_index i JOIN pg_class ci ON ci.oid=i.indexrelid "
            "WHERE i.indrelid=%s::regclass AND i.indisunique ORDER BY ci.relname", (qualified,)).fetchall()
        result[qualified] = {
            "columns": [dict(name=k, type=v) for k,v in cols], "rls": rls, "force_rls": force,
            "constraints": [dict(name=n, kind=k, definition=d, columns=c, parent=p) for n,k,d,c,p in constraints],
            "unique_indexes": [dict(name=n, predicate=p, columns=c, nulls_not_distinct=nd, definition=d)
                               for n,p,c,nd,d in indexes],
            "triggers": [r[0] for r in conn.execute(
                "SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid=%s::regclass "
                "AND NOT tgisinternal ORDER BY tgname", (qualified,)).fetchall()],
            "policies": conn.execute("SELECT policyname,roles,cmd,qual,with_check FROM pg_policies "
                                     "WHERE schemaname=%s AND tablename=%s", (schema,table)).fetchall(),
        }
    return result


def column_names(shape):
    return {c["name"] for c in shape["columns"]}


def primary(shape):
    return next((c["columns"] for c in shape["constraints"] if c["kind"] == "p"), [])


def fingerprints(conn, table, shape, *, scoped=True, keys=None, predicate=None, nulls_not_distinct=True):
    keys = primary(shape) if keys is None else keys
    if not keys or any(k is None for k in keys):
        raise ValueError("Expression index or missing primary key requires manual review: " + table)
    key = sql.SQL("jsonb_build_array({})").format(sql.SQL(",").join(sql.Identifier("t", k) for k in keys))
    query = sql.SQL("SELECT {},{} FROM {} t WHERE true").format(sha(key), sha(sql.SQL("to_jsonb(t)")), name(table))
    params = []
    if scoped:
        query += sql.SQL(" AND institution_id=%s")
        params.append(CURRENT)
    if predicate:
        # Trusted catalog expression; READ ONLY remains database-enforced.
        query += sql.SQL(" AND ({})").format(sql.SQL(predicate))
    if not nulls_not_distinct:
        query += sql.SQL(" AND ") + sql.SQL(" AND ").join(sql.SQL("{} IS NOT NULL").format(sql.Identifier("t", k)) for k in keys)
    rows = conn.execute(query, params).fetchall()
    return dict(rows)


def count_rows(conn, table, *, field=None, value=None):
    query = sql.SQL("SELECT count(*) FROM {}").format(name(table))
    if field:
        query += sql.SQL(" WHERE {}::text=%s").format(sql.Identifier(field))
    return conn.execute(query, (value,) if field else ()).fetchone()[0]


def inventory(conn, shapes):
    result = {}
    for table, shape in shapes.items():
        entry = {}
        for label, value in (("current", CURRENT), ("old", OLD)):
            tenant(conn, value)
            if "institution_id" in column_names(shape):
                entry[label] = count_rows(conn, table, field="institution_id", value=value)
            else:
                entry[label] = None
            entry[label + "_uuid_mentions"] = conn.execute(sql.SQL(
                "SELECT count(*) FROM {} t WHERE to_jsonb(t)::text ILIKE %s").format(name(table)),
                ("%" + value + "%",)).fetchone()[0]
        tenant(conn, CURRENT)
        entry["visible_total"] = count_rows(conn, table)
        if table.endswith(".alembic_version"):
            entry["revisions"] = [r[0] for r in conn.execute(sql.SQL("SELECT version_num FROM {}").format(name(table))).fetchall()]
        result[table] = entry
    return result


def comparison(source, destination):
    same = {key for key in source.keys() & destination.keys() if source[key] == destination[key]}
    conflict = (source.keys() & destination.keys()) - same
    missing = source.keys() - destination.keys()
    return {"source_rows": len(source), "missing": len(missing), "identical_skip": len(same),
            "conflicts": len(conflict), "missing_key_sha256": sorted(missing), "conflicting_key_sha256": sorted(conflict)}


def hashed_values(conn, table, expression, *, scoped=True):
    query = sql.SQL("SELECT DISTINCT {} FROM {} t WHERE ({}) IS NOT NULL").format(
        sha(sql.SQL(expression)), name(table), sql.SQL(expression))
    if scoped:
        query += sql.SQL(" AND institution_id=%s")
    return {r[0] for r in conn.execute(query, (CURRENT,) if scoped else ()).fetchall()}


def soft_references(local, neon, local_shapes, neon_shapes):
    targets = {
        "staff": hashed_values(neon, "academic.staff", "id", scoped=True),
        "staff_number": hashed_values(neon, "academic.staff", "staff_number", scoped=True),
        "departments": hashed_values(neon, "academic.departments", "id", scoped=True),
        "users": hashed_values(neon, "identity.users", "id", scoped=False),
    }
    checks = [
        ("teaching.form_template_drafts", "document->>'created_by'", "users"),
        ("teaching.form_template_drafts", "document->>'updated_by'", "users"),
        ("teaching.form_distributions", "created_by", "users"),
        ("teaching.form_distribution_recipients", "staff_id", "staff"),
        ("teaching.form_distribution_recipients", "identity_user_id", "users"),
        ("teaching.generation_jobs", "requested_by_identity_user_id", "users"),
        ("teaching.timetable_versions", "created_by_identity_user_id", "users"),
        ("teaching.deliveries", "staff_reference", "staff_number"),
    ]
    results = []
    for table, expression, target in checks:
        if table not in local_shapes:
            continue
        values = hashed_values(local, table, expression)
        unresolved = values - targets[target]
        results.append(dict(table=table, field=expression, target=target,
                            distinct_references=len(values), unresolved=len(unresolved),
                            unresolved_sha256=sorted(unresolved)))
    return results


def audit(neon_url, local_url):
    with connect(neon_url) as neon, connect(local_url) as local:
        if database_identity(neon) != NEON or database_identity(local) != LOCAL:
            raise ValueError("Unexpected database identity; audit refused")
        report = {"created_utc": datetime.now(timezone.utc).isoformat(), "mode": "READ_ONLY_NO_APPLY",
                  "current_institution": CURRENT, "old_institution_preserve": OLD,
                  "neon": {"identity": NEON}, "isolated": {"identity": LOCAL}}
        for label, conn in (("neon",neon), ("isolated",local)):
            report[label]["transaction_read_only"] = conn.execute("SHOW transaction_read_only").fetchone()[0]
            report[label]["role"] = conn.execute("SELECT current_user,rolsuper,rolbypassrls "
                                                 "FROM pg_roles WHERE rolname=current_user").fetchone()
            report[label]["schema"] = catalog(conn)
            report[label]["inventory"] = inventory(conn, report[label]["schema"])
        ls, ns = report["isolated"]["schema"], report["neon"]["schema"]
        tenant(neon,CURRENT); tenant(local,CURRENT)
        report["insert_dry_run"] = {}
        for table, shape in ls.items():
            if not table.startswith("teaching.") or "institution_id" not in column_names(shape):
                continue
            src = fingerprints(local,table,shape)
            dst = fingerprints(neon,table,ns[table],scoped=False) if table in ns else {}
            plan = comparison(src,dst)
            plan["schema_missing"] = table not in ns
            plan["schema_columns_match"] = table in ns and shape["columns"] == ns[table]["columns"]
            plan["unique_collisions"] = {}
            if table in ns:
                for index in shape["unique_indexes"]:
                    if any(k is None for k in index["columns"]):
                        plan["unique_collisions"][index["name"]] = "EXPRESSION_REVIEW_REQUIRED"
                        continue
                    kwargs = dict(keys=index["columns"],predicate=index["predicate"],nulls_not_distinct=index["nulls_not_distinct"])
                    si = fingerprints(local,table,shape,**kwargs)
                    di = fingerprints(neon,table,ns[table],scoped=False,**kwargs)
                    plan["unique_collisions"][index["name"]] = comparison(si,di)["conflicts"]
            report["insert_dry_run"][table] = plan
        report["soft_references"] = soft_references(local,neon,ls,ns)
        if "teaching.generation_jobs" in ls:
            report["isolated"]["job_states"] = local.execute(
                "SELECT state,count(*) FROM teaching.generation_jobs WHERE institution_id=%s GROUP BY state",(CURRENT,)).fetchall()
        report["note"] = "Counts/hashes only. No backup, source rows, DDL, DML, schema upgrade or authenticated API verification performed by this command."
        return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neon-env",default=str(ROOT/".env"))
    parser.add_argument("--local-config",default=str(ROOT/"services/teaching-service/local-data/persistence-preview/step12b4-reviewed/isolated-database.json"))
    parser.add_argument("--output",required=True)
    args=parser.parse_args(argv)
    output=Path(args.output).resolve()
    if not output.is_relative_to(PRIVATE.resolve()) or output.exists():
        parser.error("Use a new private scripts/neon-review/ output file")
    nu=read_url(args.neon_env,"DIRECT_DATABASE_URL")
    lu=make_url(json.loads(Path(args.local_config).read_text())["admin_url"])
    if nu.database!="neondb" or not nu.host.endswith(".neon.tech") or (lu.host,lu.port,lu.database)!=("127.0.0.1",52870,LOCAL["database"]):
        parser.error("Explicit existing Neon/isolated PostgreSQL targets required")
    report=audit(nu,lu)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("x",encoding="utf-8") as handle:
        json.dump(report,handle,indent=2,default=str)
    print(json.dumps({"read_only": True,"tables_neon":len(report["neon"]["schema"]),
                      "tables_isolated":len(report["isolated"]["schema"]),
                      "inserts_by_table":{t:p["missing"] for t,p in report["insert_dry_run"].items()},
                      "conflicts_by_table":{t:p["conflicts"] for t,p in report["insert_dry_run"].items()},
                      "soft_reference_failures":sum(x["unresolved"] for x in report["soft_references"])},indent=2))


if __name__=="__main__":
    main()

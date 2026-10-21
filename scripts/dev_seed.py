"""Private, versioned owner-table snapshots. No migrations, grants or solver calls.

Export uses READ ONLY transactions; load defaults to a read-only plan. See
docs/development/shared-data.md before using --apply on a new development DB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid5

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.types.json import Jsonb
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "scripts" / "dev-seed"
FORMAT = "schedence-development-seed/v1"
# Dependency order, using only schemas/tables already owned by these services.
TABLES = {
    "academic": tuple("academic." + name for name in (
        "staff", "source_datasets", "departments", "specializations", "modules",
        "staff_teaching_profiles", "module_specializations", "module_teaching_teams",
        "lecturer_preferences", "cohort_totals", "pp1_specialization_allocations",
        "pp1_student_groups", "module_classification_reviews")),
    "resource": ("resource.source_datasets", "resource.venues"),
    "examinations": tuple("examinations." + name for name in (
        "exam_dataset_owner", "seed_modules", "seed_module_specializations",
        "seed_students", "seed_registrations", "seed_venues", "seed_venue_blocks",
        "seed_exam_periods", "seed_exam_period_exclusions", "seed_exam_period_modules")),
}
ALL_TABLES = tuple(table for tables in TABLES.values() for table in tables)
# Existing branch is protected regardless of the caller's target file.
PROTECTED_BRANCHES = {"br-dry-darkness-b549ci92"}


class SeedError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def identifier(table):
    if table not in ALL_TABLES:
        raise SeedError("Table is outside the seed allowlist")
    return sql.Identifier(*table.split("."))


def private_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(PRIVATE.resolve()) or path.suffix != ".json":
        raise SeedError("Bundles must be JSON files inside ignored scripts/dev-seed/")
    if path.name == "target.example.json":
        raise SeedError("The tracked example cannot contain snapshot data")
    return path


def read_url(env_file, key):
    # Explicit file/key only: no nearest-.env discovery or process-env merging.
    value = dotenv_values(env_file, interpolate=False).get(key)
    if not value:
        raise SeedError("Required database URL is missing from the explicit env file")
    url = make_url(value)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise SeedError("PostgreSQL psycopg URL required")
    if not url.host or not url.database or not url.username:
        raise SeedError("Explicit database host, name and role required")
    # libpq options could override our transaction settings; never accept them.
    if set(url.query) - {"sslmode", "channel_binding"}:
        raise SeedError("Unsupported connection URL options")
    return url


@contextmanager
def connect(url, *, readonly=True):
    options = "-c statement_timeout=15000 -c lock_timeout=3000 -c timezone=UTC -c datestyle=ISO,YMD"
    options += " -c default_transaction_read_only=" + ("on" if readonly else "off")
    with psycopg.connect(
        url.set(drivername="postgresql").render_as_string(hide_password=False),
        options=options, connect_timeout=12,
    ) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL " + ("REPEATABLE READ" if readonly else "SERIALIZABLE"))
        if readonly:
            conn.execute("SET TRANSACTION READ ONLY")
        yield conn


def database_identity(conn):
    row = conn.execute("SELECT current_database(), current_setting('neon.project_id',true), "
                       "current_setting('neon.branch_id',true)").fetchone()
    return dict(zip(("database", "neon_project_id", "neon_branch_id"), row))


def set_tenant(conn, tenant):
    conn.execute("SELECT set_config('schedence.institution_id',%s,true)", (tenant,))


def columns(conn, table):
    # SQL type names are metadata, never interpolated into generated SQL.
    return [dict(zip(("name", "type", "identity", "generated"), row)) for row in conn.execute(
        "SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attidentity,a.attgenerated "
        "FROM pg_attribute a WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 "
        "AND NOT a.attisdropped ORDER BY a.attnum", (table,)).fetchall()]


def rows(conn, table, tenant, *, global_dataset=False):
    query = sql.SQL("SELECT to_jsonb(t) FROM {} t").format(identifier(table))
    if not global_dataset:
        query += sql.SQL(" WHERE institution_id=%s")
    result = conn.execute(query, () if global_dataset else (tenant,)).fetchall()
    return sorted((row[0] for row in result), key=canonical)


def published_exams(conn, tenant):
    # Read-only projection, never imported as historical/generated records.
    sessions = conn.execute(
        "SELECT to_jsonb(s) || jsonb_build_object('institution_id',t.institution_id,"
        "'period_code',t.period_code,'revision',t.revision,'status',t.status) "
        "FROM examinations.exam_timetable_sessions s JOIN examinations.exam_timetables t "
        "USING(timetable_id) WHERE t.institution_id=%s AND t.status='PUBLISHED' "
        "AND t.validation_passed", (tenant,)).fetchall()
    rooms = conn.execute(
        "SELECT to_jsonb(r) FROM examinations.exam_timetable_rooms r "
        "JOIN examinations.exam_timetables t USING(timetable_id) "
        "WHERE t.institution_id=%s AND t.status='PUBLISHED' AND t.validation_passed",
        (tenant,)).fetchall()
    return {"sessions": sorted((r[0] for r in sessions), key=canonical),
            "rooms": sorted((r[0] for r in rooms), key=canonical),
            "time_basis": "Source local datetime; institution timezone must be supplied by Exam owner"}


def development_key(tenant, value, prefix="DEV-", length=16):
    return prefix + hashlib.sha256((tenant + ":" + value).encode()).hexdigest()[:length]


def sanitize_rows(table, data, tenant):
    """Sanitize in memory, before any local artifact is written. Never export raw responses."""
    if table == "academic.lecturer_preferences":
        return []
    safe = []
    for original in data:
        row = dict(original)
        if "staff_number" in row:
            row["staff_number"] = development_key(tenant, row["staff_number"])
        if table == "academic.staff":
            # Stable dev UUID also prevents a live staff UUID being mistaken for a dev identity.
            row["id"] = str(uuid5(UUID(tenant), str(row["id"])))
            row["full_name"] = "Development Lecturer " + row["staff_number"][4:]
            row["email"] = None
            row["identity_user_id"] = None
        if "student_id" in row:
            row["student_id"] = development_key(tenant, row["student_id"], "DEV", 13)
        if "source_values" in row:
            row["source_values"] = {}
        if "record_key" in row:
            row["record_key"] = development_key(tenant, row["record_key"], "development:", 32)
        if "provenance" in row:
            row["provenance"] = {"development_seed": True, "raw_metadata_removed": True}
            row["filename"] = table.split(".")[0] + "-development-input.xlsx"
        if "worksheet" in row:
            row["worksheet"] = "Development"
        if table == "academic.staff_teaching_profiles":
            row["source_position"] = "Development teaching role"
        if table == "examinations.exam_dataset_owner":
            row["reason"] = "Pseudonymized development dataset; original tenant retained"
        if table == "examinations.seed_venue_blocks":
            row["reason"] = "Development venue block"
            if row["source_ref"]:
                row["source_ref"] = development_key(tenant, row["source_ref"], "development:", 32)
        safe.append(row)
    return sorted(safe, key=canonical)


def revision_metadata(url):
    """Optional separately authorized metadata-only connection; never reads source records."""
    with connect(url) as conn:
        result = {"identity": database_identity(conn), "revisions": {}}
        for owner in TABLES:
            result["revisions"][owner] = sorted(row[0] for row in conn.execute(
                sql.SQL("SELECT version_num FROM {}.alembic_version").format(sql.Identifier(owner))).fetchall())
        return result


def export_bundle(sources, tenant, metadata=None):
    tenant = str(UUID(tenant))
    if set(sources) != set(TABLES):
        raise SeedError("All three explicit owner sources are required")
    bundle = {"format": FORMAT, "institution_id": tenant,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "classification": "pseudonymized development data; no live contact/account links or raw source payloads",
              "redactions": ["lecturer names/numbers/UUIDs", "emails", "identity account links",
                             "student identifiers", "raw source payloads/provenance", "personal preference responses"],
              "sources": {}, "tables": {}, "references": {}}
    for owner, url in sources.items():
        try:
            export_owner(bundle, owner, url, tenant, metadata)
        except psycopg.Error as exc:
            raise SeedError(f"{owner} export failed (SQLSTATE {exc.sqlstate}); no snapshot written") from None
    bundle["sha256"] = digest(bundle)
    validate_bundle(bundle)
    return bundle


def export_owner(bundle, owner, url, tenant, metadata=None):
    with connect(url) as conn:
        set_tenant(conn, tenant)
        identity = database_identity(conn)
        if metadata is not None:
            if identity != metadata["identity"]:
                raise SeedError("Metadata connection does not match the source database identity")
            revisions = metadata["revisions"][owner]
        else:
            revisions = sorted(row[0] for row in conn.execute(
                sql.SQL("SELECT version_num FROM {}.alembic_version").format(sql.Identifier(owner))).fetchall())
        bundle["sources"][owner] = {**identity, "revisions": revisions}
        if owner == "examinations":
            owners = conn.execute("SELECT institution_id FROM examinations.exam_dataset_owner").fetchall()
            if owners != [(UUID(tenant),)]:
                raise SeedError("Exam singleton dataset belongs to another institution or has no owner")
            bundle["references"]["published_exams"] = published_exams(conn, tenant)
        for table in TABLES[owner]:
            shape = columns(conn, table)
            if not shape:
                raise SeedError("Source schema is missing a required owner table")
            raw = [] if table == "academic.lecturer_preferences" else rows(
                conn, table, tenant, global_dataset=owner == "examinations")
            data = sanitize_rows(table, raw, tenant)
            bundle["tables"][table] = {"columns": shape, "rows": data, "sha256": digest(data)}


def validate_bundle(bundle):
    if bundle.get("format") != FORMAT:
        raise SeedError("Unsupported seed format")
    tenant = str(UUID(bundle["institution_id"]))
    unsigned = {key: value for key, value in bundle.items() if key != "sha256"}
    if bundle.get("sha256") != digest(unsigned):
        raise SeedError("Bundle checksum mismatch")
    if set(bundle["tables"]) != set(ALL_TABLES) or set(bundle["sources"]) != set(TABLES):
        raise SeedError("Incomplete or unapproved owner table set")
    for table, entry in bundle["tables"].items():
        names = {column["name"] for column in entry["columns"]}
        if not names or len(names) != len(entry["columns"]):
            raise SeedError("Invalid column metadata")
        if entry["sha256"] != digest(entry["rows"]):
            raise SeedError("Table checksum mismatch")
        for row in entry["rows"]:
            if set(row) != names:
                raise SeedError("Row columns do not match the owner schema")
            if table.startswith(("academic.", "resource.")) or table.endswith("exam_dataset_owner"):
                if row.get("institution_id") != tenant:
                    raise SeedError("Cross-institution row rejected")
            if row.get("source_values") or ("staff_number" in row and not re.fullmatch(r"DEV-[0-9a-f]{16}", row["staff_number"])):
                raise SeedError("Unsanitized source/staff values rejected")
            if "student_id" in row and not re.fullmatch(r"DEV[0-9a-f]{13}", row["student_id"]):
                raise SeedError("Unsanitized student identifier rejected")
            if table == "academic.staff" and (row.get("email") or row.get("identity_user_id")
                    or row.get("full_name") != "Development Lecturer " + row["staff_number"][4:]):
                raise SeedError("Live staff contact/account identifiers rejected")
    if bundle["tables"]["academic.lecturer_preferences"]["rows"]:
        raise SeedError("Personal preference responses must not be exported")
    owners = bundle["tables"]["examinations.exam_dataset_owner"]["rows"]
    if len(owners) != 1 or owners[0].get("singleton") is not True:
        raise SeedError("Exactly one Exam dataset owner required")
    validate_references(bundle)


def validate_references(bundle):
    data = {table: entry["rows"] for table, entry in bundle["tables"].items()}
    links = [
        ("academic.staff_teaching_profiles", "staff_number", "academic.staff", "staff_number"),
        ("academic.staff_teaching_profiles", "department_id", "academic.departments", "id"),
        ("academic.module_teaching_teams", "staff_number", "academic.staff", "staff_number"),
        ("academic.module_teaching_teams", "module_id", "academic.modules", "id"),
        ("examinations.seed_registrations", "module_code", "examinations.seed_modules", "module_code"),
        ("examinations.seed_registrations", "student_id", "examinations.seed_students", "student_id"),
        ("examinations.seed_venue_blocks", "venue_id", "examinations.seed_venues", "venue_id"),
        ("examinations.seed_exam_period_modules", "module_code", "examinations.seed_modules", "module_code"),
        ("examinations.seed_exam_period_modules", "period_code", "examinations.seed_exam_periods", "period_code"),
        ("academic.module_specializations", "module_id", "academic.modules", "id"),
        ("academic.module_specializations", "specialization_id", "academic.specializations", "id"),
        ("academic.lecturer_preferences", "staff_number", "academic.staff", "staff_number"),
        ("academic.pp1_specialization_allocations", "cohort_total_id", "academic.cohort_totals", "id"),
        ("academic.pp1_specialization_allocations", "specialization_id", "academic.specializations", "id"),
        ("academic.pp1_student_groups", "allocation_id", "academic.pp1_specialization_allocations", "id"),
        ("academic.pp1_student_groups", "parent_id", "academic.pp1_student_groups", "id"),
        ("academic.module_classification_reviews", "module_id", "academic.modules", "id"),
        ("examinations.seed_module_specializations", "module_code", "examinations.seed_modules", "module_code"),
        ("examinations.seed_exam_period_exclusions", "period_code", "examinations.seed_exam_periods", "period_code"),
    ]
    for owner in ("academic", "resource"):
        for table in TABLES[owner]:
            if any(c["name"] == "source_id" for c in bundle["tables"][table]["columns"]):
                links.append((table, "source_id", owner + ".source_datasets", "id"))
    for table, field, parent, key in links:
        keys = {row[key] for row in data[parent]}
        if any(row[field] is not None and row[field] not in keys for row in data[table]):
            raise SeedError("Broken owner reference: " + table + "." + field)
    exams = bundle["references"]["published_exams"]
    keys = set()
    for row in exams["sessions"]:
        if row["institution_id"] != bundle["institution_id"] or row["status"] != "PUBLISHED":
            raise SeedError("Invalid published exam reference")
        keys.add((row["timetable_id"], row["session_id"]))
    if any((row["timetable_id"], row["session_id"]) not in keys for row in exams["rooms"]):
        raise SeedError("Exam room references an unavailable published session")


def guard_target(url, target, actual, bundle):
    if target.get("environment") != "development" or any(
        target.get(key) != actual[key] for key in actual
    ):
        raise SeedError("Target identity does not match the explicit development manifest")
    if url.database != actual["database"]:
        raise SeedError("Connection database mismatch")
    if actual["neon_branch_id"]:
        source_branches = {s["neon_branch_id"] for s in bundle["sources"].values()}
        if (actual["database"] != "schedence_dev" or not actual["neon_project_id"]
                or actual["neon_branch_id"] in source_branches | PROTECTED_BRANCHES):
            raise SeedError("Existing/source Neon branches and non-development databases are protected")
    elif (url.host not in {"127.0.0.1", "::1"}
          or not actual["database"].startswith("schedence_seed_")
          or actual["neon_project_id"]):
        raise SeedError("Local targets must use loopback and a fresh schedence_seed_* database")


def normalized_rows(entry, data):
    # ALWAYS-generated IDs have no incoming seed FKs (only seed_venue_blocks).
    omitted = {c["name"] for c in entry["columns"] if c["identity"] or c["generated"]}
    return sorted(({k: v for k, v in row.items() if k not in omitted} for row in data), key=canonical)


def plan_tables(conn, bundle, *, lock=False):
    tenant = bundle["institution_id"]
    set_tenant(conn, tenant)
    elevated = conn.execute(
        "SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication "
        "OR EXISTS (SELECT 1 FROM pg_roles p WHERE (p.rolsuper OR p.rolbypassrls) "
        "AND pg_has_role(current_user,p.oid,'MEMBER')) "
        "FROM pg_roles WHERE rolname=current_user").fetchone()[0]
    if elevated:
        raise SeedError("Use a restricted development seed role, never an owner/superuser role")
    owns_tables = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_class c WHERE c.oid=ANY(%s::regclass[]) "
        "AND pg_has_role(current_user,c.relowner,'MEMBER'))", (list(ALL_TABLES),)).fetchone()[0]
    if owns_tables:
        raise SeedError("Seed role must not own or inherit ownership of destination tables")
    exists = conn.execute("SELECT id FROM identity.institutions WHERE id=%s AND status='ACTIVE'", (tenant,)).fetchone()
    if not exists:
        raise SeedError("Identity owner must provision the canonical development institution first")
    if lock:
        # Serialize seed loaders without granting UPDATE/DELETE just to lock tables.
        # SERIALIZABLE + real constraints handle concurrent service writes safely.
        conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (FORMAT,))
    plan = {}
    for owner, source in bundle["sources"].items():
        revisions = conn.execute(sql.SQL("SELECT version_num FROM {}.alembic_version")
                                 .format(sql.Identifier(owner))).fetchall()
        if sorted(r[0] for r in revisions) != source["revisions"]:
            raise SeedError("Owner migration revisions differ: " + owner)
    for table in ALL_TABLES:
        entry = bundle["tables"][table]
        if columns(conn, table) != entry["columns"]:
            raise SeedError("Target owner schema differs: " + table)
        # Tenant RLS stays enabled. Non-RLS tables are compared in full, never overwritten.
        current = rows(conn, table, tenant, global_dataset=True)
        wanted = normalized_rows(entry, entry["rows"])
        if normalized_rows(entry, current) == wanted:
            plan[table] = "unchanged"
        elif not current:
            plan[table] = "insert"
        else:
            raise SeedError("Existing data differs; no updates/deletes permitted: " + table)
    return plan


def load_bundle(url, target, bundle, *, apply=False):
    validate_bundle(bundle)
    with connect(url, readonly=not apply) as conn:
        guard_target(url, target, database_identity(conn), bundle)
        plan = plan_tables(conn, bundle, lock=apply)
        if apply:
            for table, action in plan.items():
                if action != "insert":
                    continue
                entry = bundle["tables"][table]
                names = [c["name"] for c in entry["columns"] if not c["identity"] and not c["generated"]]
                fields = sql.SQL(",").join(sql.Identifier(name) for name in names)
                conn.execute(sql.SQL("INSERT INTO {} ({}) SELECT {} FROM "
                                     "jsonb_populate_recordset(NULL::{},%s)")
                             .format(identifier(table), fields, fields, identifier(table)),
                             (Jsonb(entry["rows"]),))
            # Real FK/CHECK/RLS constraints remain enabled; all inserts commit together.
        return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Read-only snapshot to an ignored private JSON file")
    export.add_argument("--institution-id", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--metadata-env-file", help="Optional separately authorized migration-metadata reader")
    export.add_argument("--metadata-url-key")
    for owner in TABLES:
        export.add_argument(f"--{owner}-env-file", required=True)
        export.add_argument(f"--{owner}-url-key", required=True)
    inspect = commands.add_parser("inspect", help="Offline checksum, tenant and reference validation")
    inspect.add_argument("--bundle", required=True)
    load = commands.add_parser("load", help="Read-only plan unless --apply is explicitly supplied")
    load.add_argument("--bundle", required=True)
    load.add_argument("--target", required=True)
    load.add_argument("--env-file", required=True)
    load.add_argument("--url-key", default="DIRECT_DATABASE_URL")
    load.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            output = private_path(args.output)
            if output.exists():
                raise SeedError("Snapshot exists; export to a new versioned filename")
            sources = {owner: read_url(getattr(args, owner + "_env_file"), getattr(args, owner + "_url_key"))
                       for owner in TABLES}
            if bool(args.metadata_env_file) != bool(args.metadata_url_key):
                raise SeedError("Both metadata env file and key are required together")
            metadata = revision_metadata(read_url(args.metadata_env_file, args.metadata_url_key)) if args.metadata_env_file else None
            bundle = export_bundle(sources, args.institution_id, metadata)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as handle:
                json.dump(bundle, handle, ensure_ascii=False, indent=2)
        else:
            bundle = json.loads(private_path(args.bundle).read_text(encoding="utf-8"))
            validate_bundle(bundle)
            if args.command == "load":
                target = json.loads(Path(args.target).read_text(encoding="utf-8"))
                plan = load_bundle(read_url(args.env_file, args.url_key), target, bundle, apply=args.apply)
                print(json.dumps({"applied": args.apply, "plan": plan}, indent=2))
                return 0
        print(json.dumps({"institution_id": bundle["institution_id"], "sha256": bundle["sha256"],
                          "counts": {table: len(entry["rows"]) for table, entry in bundle["tables"].items()},
                          "published_exam_sessions": len(bundle["references"]["published_exams"]["sessions"])}, indent=2))
        return 0
    except (SeedError, OSError, KeyError, TypeError, ValueError, psycopg.Error) as exc:
        # Driver errors and connection strings may contain credentials/row values.
        message = str(exc) if isinstance(exc, SeedError) else type(exc).__name__
        print("Seed operation stopped: " + message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

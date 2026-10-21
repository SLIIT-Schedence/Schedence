"""Fresh read-only Neon archive; verify restoration in existing local postgres, then ROLLBACK.

Never creates a database/branch, never restores into Neon or the Teaching database.
Archive contains private data: keep the output inside ignored scripts/neon-review/.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import argparse
from datetime import datetime, timezone
from pathlib import Path

from psycopg import sql
from sqlalchemy.engine import make_url

from dev_seed import connect, database_identity, read_url
from plan_existing_neon import NEON, ROOT

BIN = ROOT / "services/teaching-service/local-data/persistence-preview/step12b4-reviewed/postgres-runtime/pgsql/bin"
LOCAL_CONFIG = ROOT / "services/teaching-service/local-data/persistence-preview/step12b4-reviewed/isolated-database.json"


def pg_env(url, *, readonly=False):
    result = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    result.update(PGHOST=url.host, PGPORT=str(url.port or 5432), PGUSER=url.username,
                  PGPASSWORD=url.password or "", PGDATABASE=url.database,
                  PGCONNECT_TIMEOUT="30", PGSSLMODE=url.query.get("sslmode", "prefer"),
                  PGAPPNAME="schedence-authorized-backup-verification")
    result["PGOPTIONS"] = "-c timezone=UTC -c datestyle=ISO,YMD"
    if readonly:
        result["PGOPTIONS"] += " -c default_transaction_read_only=on"
    if "channel_binding" in url.query:
        result["PGCHANNELBINDING"] = url.query["channel_binding"]
    return result


def table_names(conn):
    return conn.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%' "
                        "AND n.nspname<>'information_schema' ORDER BY 1,2").fetchall()


def fingerprint_query(tables):
    return sql.SQL(" UNION ALL ").join(sql.SQL(
        "SELECT {}::text AS table_name,count(*) AS rows,"
        "encode(sha256(convert_to(coalesce(string_agg(md5(to_jsonb(t)::text),'' "
        "ORDER BY md5(to_jsonb(t)::text)),''),'UTF8')),'hex') AS sha256 FROM {} t"
    ).format(sql.Literal(s + "." + t), sql.Identifier(s, t)) for s, t in tables)


def fingerprints(conn, tables):
    return {table: {"rows": count, "sha256": digest}
            for table, count, digest in conn.execute(fingerprint_query(tables)).fetchall()}


def run(command, env, out, label, timeout=600):
    result = subprocess.run([str(BIN / command[0]), *command[1:]], env=env,
                            capture_output=True, timeout=timeout)
    (out / (label + ".stdout")).write_bytes(result.stdout)
    (out / (label + ".stderr")).write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(label + " failed; private diagnostic log retained; no Neon writes")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-completed-archive", type=Path)
    args = parser.parse_args()
    target = read_url(ROOT / ".env", "DIRECT_DATABASE_URL")
    local = make_url(json.loads(LOCAL_CONFIG.read_text())["admin_url"]).set(database="postgres")
    if local.host != "127.0.0.1" or local.database != "postgres" or local.port != 52870:
        raise ValueError("Unapproved restore destination")
    with connect(local) as conn:
        if table_names(conn) or conn.execute("SELECT count(*) FROM pg_stat_activity WHERE "
                "datname=current_database() AND pid<>pg_backend_pid()").fetchone()[0]:
            raise ValueError("Local maintenance database is not empty and idle")
    if args.verify_completed_archive:
        out = args.verify_completed_archive.resolve()
        if (out.parent != (ROOT / "scripts/neon-review").resolve() or not out.name.startswith("backup-")
                or not (out / "neondb.dump").is_file() or not (out / "dump.stderr").is_file()
                or (out / "dump.stderr").read_bytes() or (out / "verified-manifest.json").exists()):
            raise ValueError("Only a completed private archive with empty dump error log may be reverified")
    else:
        out = ROOT / "scripts/neon-review" / ("backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        out.mkdir(exist_ok=False)
    manifest = {"status": "STARTED", "directory": str(out), "restore_database": "existing local postgres",
                "restore_transaction": "BEGIN / ROLLBACK", "neon_writes": 0, "created_databases": 0}
    try:
        with connect(target) as conn:
            if database_identity(conn) != NEON:
                raise ValueError("Unexpected Neon target")
            conn.execute("SET LOCAL idle_in_transaction_session_timeout=0")
            tables = table_names(conn)
            before = fingerprints(conn, tables)
            archive = out / "neondb.dump"
            manifest.update(tables=before, target=NEON)
            if not args.verify_completed_archive:
                snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
                run(["pg_dump.exe", "--format=custom", "--compress=6", "--snapshot=" + snapshot,
                     "--file=" + str(archive)], pg_env(target, readonly=True), out, "dump", 1800)
        listing = run(["pg_restore.exe", "--list", str(archive)], pg_env(local), out, "toc")
        if listing.stdout.count(b" TABLE DATA ") != len(tables):
            raise ValueError("Archive table-data count mismatch")
        restore_sql = out / "restore.sql"
        run(["pg_restore.exe", "--no-owner", "--no-privileges", "--file=" + str(restore_sql),
             str(archive)], pg_env(local), out, "decode")
        query = fingerprint_query(tables).as_string()
        expected = json.dumps(before).replace("'", "''")
        wrapper = out / "verify-restore.sql"
        with wrapper.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("\\set ON_ERROR_STOP on\nBEGIN;\n")
            handle.write(restore_sql.read_text(encoding="utf-8"))
            handle.write("\nDO $verify$ DECLARE actual jsonb; BEGIN\n")
            handle.write("SELECT jsonb_object_agg(table_name,jsonb_build_object('rows',rows,'sha256',sha256)) "
                         "INTO actual FROM (" + query + ") fingerprints;\n")
            handle.write("IF actual IS DISTINCT FROM '" + expected + "'::jsonb THEN "
                         "RAISE EXCEPTION 'Restored data differs from backup snapshot'; END IF; END $verify$;\n")
            handle.write("ROLLBACK;\nSELECT 'RESTORE_VERIFIED_AND_ROLLED_BACK';\n")
        restored = run(["psql.exe", "-X", "--quiet", "--file=" + str(wrapper)], pg_env(local), out, "restore", 900)
        if b"RESTORE_VERIFIED_AND_ROLLED_BACK" not in restored.stdout:
            raise ValueError("Restore verification marker missing")
        with connect(local) as conn:
            if table_names(conn):
                raise ValueError("Local restore transaction did not roll back")
        with connect(target) as conn:
            after = fingerprints(conn, tables)
            if before != after or table_names(conn) != tables:
                raise ValueError("Neon data changed concurrently; refresh backup before migration")
        manifest.update(status="PASS", target=NEON, tables=before, table_count=len(tables),
                        rows=sum(v["rows"] for v in before.values()), size_bytes=archive.stat().st_size,
                        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                        restored_all_row_hashes_match=True, local_restore_rolled_back=True,
                        neon_unchanged=True)
    except Exception as exc:
        manifest.update(status="FAILED", error_type=type(exc).__name__, error=str(exc)
                        if not hasattr(exc, "sqlstate") else "PostgreSQL operation failed; details withheld")
        raise
    finally:
        manifest_name = "verified-manifest.json" if args.verify_completed_archive else "manifest.json"
        (out / manifest_name).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in manifest.items() if k != "tables"}, indent=2))


if __name__ == "__main__":
    main()

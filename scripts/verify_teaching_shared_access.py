"""Read-only verification through existing Teaching owner adapters and Forms store.

Does not start services, prepare generation work, accept a job or call a solver.
"""
import asyncio
import json
from uuid import UUID

import psycopg

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dev_seed import ROOT, connect, database_identity, read_url
from plan_existing_neon import CURRENT, NEON, OLD
from schedence_db.runtime import run
from schedence_teaching.forms.store import SqlFormTemplateStore
from schedence_teaching.generation.readiness import load_owner_rows
from schedence_teaching.persistence.database_read import DatabaseTarget

ENV = ROOT / "services/teaching-service/local-data/persistence-preview/step12b2/resumed-production/restricted-runtime.env"
OUT = ROOT / "scripts/neon-review/approved-additive-migration-20261021"


async def main():
    engines = {schema: create_async_engine(read_url(ENV, prefix + "_DIRECT_DATABASE_URL"),
        poolclass=NullPool, hide_parameters=True,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=15000"})
        for schema, prefix in (("academic", "ACADEMIC"), ("resource", "RESOURCE"), ("teaching", "TEACHING"))}
    try:
        rows = await load_owner_rows({k: engines[k] for k in ("academic", "resource")}, UUID(CURRENT),
            DatabaseTarget(NEON["database"], NEON["neon_project_id"], NEON["neon_branch_id"]))
        owner_counts = {schema: {table: len(values) for table, values in tables.items()}
                        for schema, tables in rows.items()}
        if (owner_counts["academic"]["staff"], owner_counts["academic"]["modules"],
                owner_counts["resource"]["venues"]) != (110, 184, 62):
            raise ValueError("Unexpected canonical Academic Inputs counts")
        store = SqlFormTemplateStore(async_sessionmaker(engines["teaching"], expire_on_commit=False))
        templates, total = await store.list(UUID(CURRENT), 0, 100)
        if total != 4 or len(templates) != 4:
            raise ValueError("Forms runtime adapter cannot read four migrated drafts")
        if (await store.list(UUID(OLD), 0, 100))[1] != 0:
            raise ValueError("Forms runtime adapter exposed another tenant")
        views = json.loads((OUT / "dry-run.json").read_text())["views"]
        shared_counts = {}
        async with engines["teaching"].connect() as conn:
            actual = (await conn.execute(text("SELECT current_database(),current_setting('neon.project_id',true),"
                "current_setting('neon.branch_id',true)"))).one()
            if tuple(actual) != tuple(NEON.values()):
                raise ValueError("Teaching runtime reader connected to wrong database")
            await conn.execute(text("SET LOCAL ROLE schedence_teaching_shared_reader"))
            for tenant in (CURRENT, OLD, ""):
                await conn.execute(text("SELECT set_config('schedence.institution_id',:tenant,true)"), {"tenant": tenant})
                counts = {name: (await conn.execute(text("SELECT count(*) FROM teaching_shared." + name))).scalar_one()
                          for name in views}
                if tenant != CURRENT and any(counts.values()):
                    raise ValueError("Shared role exposed old/unset institution data")
                if tenant == CURRENT:
                    shared_counts = counts
            await conn.rollback()
        # Examination's existing configured connection; read its tenant binding
        # and module count without changing its implementation or data.
        # Match the existing pooled connection. Pooler startup options reject
        # statement_timeout, so enforce read-only as the first SQL statement.
        exam_url = read_url(ROOT / ".env", "DATABASE_URL").set(drivername="postgresql")
        with psycopg.connect(exam_url.render_as_string(hide_password=False), connect_timeout=15) as exam:
            exam.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            exam.execute("SET LOCAL statement_timeout='15s'")
            if database_identity(exam) != NEON:
                raise ValueError("Existing Examination connection targets a different database")
            binding = exam.execute("SELECT institution_id::text FROM examinations.exam_dataset_owner WHERE singleton").fetchone()
            exam_count = exam.execute("SELECT count(*) FROM examinations.seed_modules").fetchone()[0]
            if binding != (CURRENT,) or exam_count != 207:
                raise ValueError("Existing Examination canonical dataset changed")
        result = dict(status="PASS", academic_inputs_adapter="READ_ONLY_CANONICAL_OWNER_ROWS_ACCESSIBLE",
            owner_counts=owner_counts, migrated_forms_read_through_existing_store=total,
            forms_other_tenant_visible=0, shared_view_counts=shared_counts,
            old_and_unset_shared_rows=0, examination_canonical_binding=True, examination_module_codes_unchanged=207,
            shared_access_login="Existing restricted Teaching runtime; other component login wiring deferred to owners",
            live_generation_persistence="Unchanged isolated local configuration",
            http_authenticated_ui_check="NOT_PERFORMED", cp_sat_runs=0)
        (OUT / "service-access.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        await asyncio.gather(*(engine.dispose() for engine in engines.values()))


if __name__ == "__main__":
    run(main())

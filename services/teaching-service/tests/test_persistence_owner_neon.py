"""Opt-in restricted-owner RLS checks. Every synthetic record rolls back."""

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from schedence_db import create_db_engine, load_settings
from schedence_db.runtime import run

pytestmark = pytest.mark.skipif(os.environ.get("SCHEDENCE_TEACHING_DB_TESTS") != "1",
    reason="explicit live-test opt-in and restricted role credentials required")


@asynccontextmanager
async def connection_for(schema):
    branch = os.environ["TEACHING_TEST_BRANCH_ID"]
    recovery = json.loads(Path(os.environ["TEACHING_TEST_RECOVERY_MANIFEST"]).read_text(encoding="utf-8"))
    assert recovery["verified"] and recovery["target_branch_id"] == branch
    engine = create_db_engine(load_settings(env_prefix=f"{schema.upper()}_", db_echo=False), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                assert (await connection.execute(text("SELECT current_setting('neon.branch_id',true)"))).scalar_one() == branch
                await connection.execute(text("SELECT set_config('schedence.institution_id',:tenant,true)"),
                    {"tenant": os.environ["TEACHING_TEST_INSTITUTION_ID"]})
                yield connection
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("schema", ["academic", "resource", "teaching"])
def test_live_service_roles_cannot_bypass_rls_or_write_other_owners(schema):
    async def execute():
        async with connection_for(schema) as connection:
            flags = (await connection.execute(text("""SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication
                FROM pg_roles WHERE rolname=current_user"""))).one()
            assert not any(flags)
            elevated = (await connection.execute(text("""SELECT count(*) FROM pg_roles r
                WHERE (r.rolsuper OR r.rolbypassrls) AND pg_has_role(current_user,r.oid,'MEMBER')"""))).scalar_one()
            assert elevated == 0
            forbidden = [("identity", "users"), ("examinations", "seed_modules")]
            forbidden.extend((other, table) for other, table in
                (("academic", "staff"), ("resource", "venues"), ("teaching", "input_snapshots")) if other != schema)
            for owner, table in forbidden:
                # OID lookup needs no USAGE on the forbidden schema. Passing a
                # qualified text name would fail name resolution before checking
                # privileges; granting USAGE just for this test would weaken access.
                assert not (await connection.execute(text("""SELECT has_table_privilege(current_user,c.oid,'INSERT')
                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname=:schema AND c.relname=:table"""),
                    {"schema": owner, "table": table})).scalar_one()
    run(execute())


@pytest.mark.parametrize("schema", ["academic", "resource"])
def test_live_owner_source_imports_require_the_current_institution(schema):
    async def execute():
        async with connection_for(schema) as connection:
            tenant, identifier = os.environ["TEACHING_TEST_INSTITUTION_ID"], uuid4()
            # schema comes only from this fixed test parameter list.
            statement = text(f"""INSERT INTO {schema}.source_datasets
                (id,institution_id,record_key,filename,sha256,provenance_kind,provenance)
                VALUES (:id,:tenant,:key,'SYNTHETIC_ROLLBACK_TEST.xlsx',:checksum,'SYNTHETIC_PP1','{{}}'::jsonb)""")
            parameters = dict(id=identifier, tenant=tenant, key=f"SYNTHETIC_ROLLBACK_TEST:{identifier}", checksum="0" * 64)
            await connection.execute(statement, parameters)
            await connection.execute(text("SELECT set_config('schedence.institution_id',:tenant,true)"), {"tenant": str(uuid4())})
            assert (await connection.execute(text(f"SELECT count(*) FROM {schema}.source_datasets WHERE id=:id"),
                {"id": identifier})).scalar_one() == 0
            parameters.update(id=uuid4(), key=f"SYNTHETIC_REJECTED_TEST:{uuid4()}")
            with pytest.raises(DBAPIError) as caught:
                async with connection.begin_nested():
                    await connection.execute(statement, parameters)
            assert getattr(caught.value.orig, "sqlstate", None) == "42501"
    run(execute())

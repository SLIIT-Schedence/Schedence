"""Opt-in production integration tests that issue only SELECT and transaction SET.

Separate from Step 12B.2's rollback-write tests, which stay disabled in this step.
"""

import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from schedence_db import create_db_engine, load_settings
from schedence_db.runtime import run

from schedence_teaching.persistence.database_read import DatabaseTarget, load_database_evidence, readonly_owner
from schedence_teaching.persistence.generation_inputs import DatabaseGenerationRequest, build_database_generation, compare_snapshot_inputs
from schedence_teaching.persistence.historical_generation import historical_draft
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.snapshots import restore_snapshot
from schedence_teaching.pilot.elective_validation import validate_elective_result
from schedence_teaching.solver import solve_timetable

pytestmark=pytest.mark.skipif(os.environ.get("SCHEDENCE_TEACHING_READONLY_DB_TESTS")!="1",
    reason="read-only production tests require separate explicit opt-in")


def configuration():
    path=Path(os.environ["TEACHING_READONLY_REQUEST"])
    config=json.loads(path.read_text(encoding="utf-8"))
    target=DatabaseTarget(os.environ["TEACHING_READONLY_DATABASE"],os.environ["TEACHING_READONLY_PROJECT_ID"],os.environ["TEACHING_READONLY_BRANCH_ID"])
    return config,target


def engines():
    return {schema:create_db_engine(load_settings(env_file=os.environ["TEACHING_READONLY_CREDENTIALS_FILE"],
        env_prefix=schema.upper()+"_",db_echo=False,db_connect_timeout=30),direct=True,hide_parameters=True)
        for schema in ("teaching","academic","resource")}


@pytest.fixture(scope="module")
def real_evidence():
    async def load():
        config,target=configuration();connections=engines()
        try:
            return await load_database_evidence(connections,UUID(config["institution_id"]),UUID(config["snapshots"]["1"]),target)
        finally:
            for engine in connections.values():await engine.dispose()
    return run(load())


def test_live_database_reconstructs_original_inputs_and_validates_saved_history(real_evidence):
    config,_=configuration()
    request=DatabaseGenerationRequest(institution_id=config["institution_id"],snapshot_id=config["snapshots"]["1"],semester=1,
        calendar_label=config["calendar_label"],policy_version=config["policy_version"])
    loaded=build_database_generation(real_evidence,request)
    assert len(loaded.enrolled.batch.prepared.sessions)==697
    result,audit=historical_draft(real_evidence,loaded.enrolled)
    assert audit["status"]=="PASSED" and len(result.schedule)==697
    earlier=restore_snapshot(json.loads((Path(os.environ["TEACHING_READONLY_EARLIER_SNAPSHOTS"])/"semester-1.snapshot.json").read_text(encoding="utf-8")))
    assert compare_snapshot_inputs(real_evidence.document,earlier)["status"]=="PASSED"
    assert all(row["role"]["transaction_read_only"]=="on" for row in real_evidence.security)


def test_live_database_backed_small_scope_native_cp_sat(real_evidence,monkeypatch):
    monkeypatch.setattr("openpyxl.load_workbook",lambda *a,**k:pytest.fail("database generation opened Excel"))
    config,_=configuration()
    request=DatabaseGenerationRequest(institution_id=config["institution_id"],snapshot_id=config["snapshots"]["1"],semester=1,
        academic_years=[4],streams=["WEEKDAY"],specializations=["IT"],calendar_label=config["calendar_label"],
        policy_version=config["policy_version"],max_time_seconds=30.0)
    loaded=build_database_generation(real_evidence,request)
    batch=loaded.enrolled.batch
    result=solve_timetable(batch.prepared,batch.config,**batch.solver_inputs)
    assert validate_elective_result(loaded.enrolled,result)["status"]=="PASSED"
    assert len(result.schedule)==len(batch.prepared.sessions)


def test_live_missing_year4_ds_mapping_stays_blocked_with_allocations_preserved(real_evidence):
    config,_=configuration()
    request=DatabaseGenerationRequest(institution_id=config["institution_id"],snapshot_id=config["snapshots"]["1"],semester=1,
        academic_years=[4],specializations=["DS"],calendar_label=config["calendar_label"],policy_version=config["policy_version"])
    blocked=[scope for scope in real_evidence.document.scopes
        if scope.academic_year==4 and scope.specialization=="DS"]
    assert len(blocked)==2 and sum(scope.student_count for scope in blocked)==148
    with pytest.raises(PersistenceConflict,match="BLOCKED_MISSING_ACADEMIC_MAPPING"):
        build_database_generation(real_evidence,request)


def test_live_other_institution_cannot_load_the_real_snapshot():
    async def execute():
        config,target=configuration();connections=engines()
        try:
            with pytest.raises(PersistenceConflict,match="snapshot not found"):
                await load_database_evidence(connections,uuid4(),UUID(config["snapshots"]["1"]),target)
        finally:
            for engine in connections.values():await engine.dispose()
    run(execute())


@pytest.mark.parametrize("schema,table",[("teaching","input_snapshots"),("academic","source_datasets"),("resource","source_datasets")])
def test_live_readonly_rls_hides_the_actual_institution_under_another_context(schema,table):
    async def execute():
        config,target=configuration();connections=engines();tenant=UUID(config["institution_id"])
        try:
            async with readonly_owner(connections[schema],schema,tenant,target) as (connection,audit):
                assert audit["role"]["transaction_read_only"]=="on"
                assert (await connection.execute(text(f"SELECT count(*) FROM {schema}.{table} WHERE institution_id=:tenant"),
                    {"tenant":tenant})).scalar_one()>0
                await connection.execute(text("SELECT set_config('schedence.institution_id',:tenant,true)"),{"tenant":str(uuid4())})
                assert (await connection.execute(text(f"SELECT count(*) FROM {schema}.{table} WHERE institution_id=:tenant"),
                    {"tenant":tenant})).scalar_one()==0
        finally:
            for engine in connections.values():await engine.dispose()
    run(execute())

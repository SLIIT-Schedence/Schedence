"""Write-capable PostgreSQL tests: explicitly LOCAL-ONLY, never Neon.

The configured URL must target loopback and the dedicated test database. No
production settings or .env fallback are read. All source records are synthetic.
"""

from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func,select,text,update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker,create_async_engine
from schedence_db.runtime import run

from schedence_teaching.persistence.models import Delivery,DeliveryCoverage,GenerationRun,TimetableSeries,TimetableVersion,ValidationReport
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.repositories import SqlTeachingRepository,teaching_unit_of_work
from schedence_teaching.persistence.timetable_service import persist_generation_result,timetable_reader
from schedence_teaching.persistence.snapshots import content_digest
from schedence_teaching.solver import SolverStatus

from test_persistence_contracts import TENANT,OTHER_TENANT,example

pytestmark=pytest.mark.skipif(os.environ.get("SCHEDENCE_TEACHING_ISOLATED_DB_TESTS")!="1",
    reason="write tests require explicit isolated localhost PostgreSQL configuration")


def isolated_engine(*,admin=False):
    config=json.loads(Path(os.environ["TEACHING_ISOLATED_DATABASE_CONFIG"]).read_text(encoding="utf-8"))
    url=make_url(config["admin_url" if admin else "runtime_url"])
    if url.host!="127.0.0.1" or url.database!="schedence_step12b4_test" or not url.port:
        raise AssertionError("write tests are restricted to the dedicated loopback test database")
    return create_async_engine(url,echo=False,hide_parameters=True)


async def setup(factory,document):
    async with teaching_unit_of_work(factory,document.institution_id) as repository:
        assert await repository._session.scalar(text("SELECT current_database()"))=="schedence_step12b4_test"
        assert await repository._session.scalar(text("SELECT current_setting('neon.project_id',true)")) is None
        snapshot_id=await repository.store_snapshot(document)
        series_id=await repository.create_series(f"SYNTHETIC_STEP12B4:{uuid4()}")
    return series_id,snapshot_id


async def save(factory,series,snapshot,result,previous=None,**options):
    return await persist_generation_result(factory,TENANT,series,snapshot,result,
        expected_previous_version_id=previous,**options)


async def counts(factory,series):
    async with timetable_reader(factory,TENANT) as repository:
        versions=await repository.list_versions(series)
        rows=[await repository.get_timetable(row.id) for row in versions]
        runs=await repository._session.scalar(select(func.count()).select_from(GenerationRun).where(
            GenerationRun.institution_id==TENANT,GenerationRun.series_id==series))
        return len(versions),sum(len(row.deliveries) for row in rows),sum(len(row.coverage) for row in rows),runs


@pytest.mark.parametrize("mode",["PHYSICAL","ONLINE"])
@pytest.mark.parametrize("logical_status",["DRAFT","VALIDATED"])
def test_first_save_repeated_save_and_all_retrieval_views(mode,logical_status):
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example(mode);series,snapshot=await setup(factory,document)
            first=await save(factory,series,snapshot,result,logical_status=logical_status)
            second=await save(factory,series,snapshot,replace(result,runtime_seconds=result.runtime_seconds+1),logical_status=logical_status)
            assert not first.reused and second.reused and first.version_id==second.version_id
            assert await counts(factory,series)==(1,2,2,1)
            async with timetable_reader(factory,TENANT) as repository:
                assert (await repository.get_series(series))["series_reference"].startswith("SYNTHETIC_STEP12B4:")
                latest=await repository.get_latest_draft(series)
                specific=await repository.get_version(series,1)
                assert latest.version["id"]==specific.version["id"]==first.version_id
                assert specific.logical_status==logical_status
                assert all(row["delivery_mode"]==mode for row in specific.deliveries)
                assert all((row["venue_reference"] is None)==(mode=="ONLINE") for row in specific.deliveries)
                assert len(specific.lecturer_timetable("SYN-A"))==1
                assert len(specific.student_group_timetable("A"))==1
                assert len(specific.specialization_timetable("IT"))==2
                if mode=="PHYSICAL":assert specific.venue_timetable(specific.deliveries[0]["venue_reference"])
                assert specific.coverage_report()["covered_original_occurrences"]==2
                assert specific.limitations["research_pending"] and not specific.version["academic_complete"]
                assert specific.run["snapshot_id"]==snapshot and specific.run["runtime_seconds"]==result.runtime_seconds
        finally:await engine.dispose()
    run(execute())


def test_new_policy_snapshot_appends_version_without_rewriting_history_and_old_retry_reuses():
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example();series,snapshot=await setup(factory,document)
            first=await save(factory,series,snapshot,result)
            async with timetable_reader(factory,TENANT) as repository:
                old=await repository.get_timetable(first.version_id)
                digest=content_digest((old.version,old.run,old.validation,old.deliveries,old.coverage))
            changed=document.model_copy(deep=True);changed.sources[0].provenance["policy_version"]="SYNTHETIC_REVIEW_2"
            async with teaching_unit_of_work(factory,TENANT) as repository:new_snapshot=await repository.store_snapshot(changed)
            second=await save(factory,series,new_snapshot,result,first.version_id)
            assert second.version_number==2 and second.version_id!=first.version_id
            retry=await save(factory,series,snapshot,result)
            assert retry.reused and retry.version_id==first.version_id
            async with timetable_reader(factory,TENANT) as repository:
                old=await repository.get_timetable(first.version_id)
                assert digest==content_digest((old.version,old.run,old.validation,old.deliveries,old.coverage))
                assert (await repository.get_version(series,2)).version["previous_version_id"]==first.version_id
            assert await counts(factory,series)==(2,4,4,2)
        finally:await engine.dispose()
    run(execute())


def test_legacy_append_is_recognized_without_mutating_or_duplicate_inserts():
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example();series,snapshot=await setup(factory,document)
            async with teaching_unit_of_work(factory,TENANT) as repository:
                first=await repository.append_attempt(series,snapshot,result,expected_previous_version_id=None)
            again=await save(factory,series,snapshot,result)
            assert again.reused and again.version_id==first.version_id
            assert await counts(factory,series)==(1,2,2,1)
        finally:await engine.dispose()
    run(execute())


@pytest.mark.parametrize("failure",["partial","unknown","bad_staff","publish"])
def test_invalid_result_or_forbidden_status_leaves_no_run_or_version(failure):
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example();series,snapshot=await setup(factory,document)
            options={}
            if failure=="partial":result=replace(result,schedule=result.schedule[:-1])
            elif failure=="unknown":result=replace(result,status=SolverStatus.UNKNOWN,schedule=(),validation=None)
            elif failure=="bad_staff":result=replace(result,schedule=(replace(result.schedule[0],assigned_staff_id="UNKNOWN"),*result.schedule[1:]))
            else:options["logical_status"]="PUBLISHED"
            with pytest.raises(ValueError):await save(factory,series,snapshot,result,**options)
            assert await counts(factory,series)==(0,0,0,0)
        finally:await engine.dispose()
    run(execute())


def test_mid_transaction_coverage_failure_rolls_back_every_run_report_and_booking():
    class FailedCoverageSession(AsyncSession):
        async def flush(self,*args,**kwargs):
            if any(isinstance(row,DeliveryCoverage) for row in self.new):raise RuntimeError("synthetic coverage failure")
            return await super().flush(*args,**kwargs)
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example();series,snapshot=await setup(factory,document)
            faulty=async_sessionmaker(engine,class_=FailedCoverageSession,expire_on_commit=False)
            with pytest.raises(RuntimeError,match="synthetic coverage failure"):await save(faulty,series,snapshot,result)
            assert await counts(factory,series)==(0,0,0,0)
            async with timetable_reader(factory,TENANT) as repository:
                assert await repository._session.scalar(select(func.count()).select_from(ValidationReport).join(GenerationRun,
                    (GenerationRun.id==ValidationReport.run_id)&(GenerationRun.institution_id==ValidationReport.institution_id))
                    .where(ValidationReport.institution_id==TENANT,GenerationRun.institution_id==TENANT,GenerationRun.series_id==series))==0
        finally:await engine.dispose()
    run(execute())


def test_concurrent_identical_saves_create_only_one_version():
    import asyncio
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example();series,snapshot=await setup(factory,document)
            receipts=await asyncio.gather(save(factory,series,snapshot,result),save(factory,series,snapshot,result))
            assert receipts[0].version_id==receipts[1].version_id
            assert sorted(row.reused for row in receipts)==[False,True]
            assert await counts(factory,series)==(1,2,2,1)
        finally:await engine.dispose()
    run(execute())


def test_concurrent_distinct_saves_reject_stale_parent_then_append_unique_next_number():
    import asyncio
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example();series,first_snapshot=await setup(factory,document)
            changed=document.model_copy(deep=True);changed.sources[0].provenance["policy_version"]="SYNTHETIC_NEXT"
            async with teaching_unit_of_work(factory,TENANT) as repository:second_snapshot=await repository.store_snapshot(changed)
            snapshots=(first_snapshot,second_snapshot)
            receipts=await asyncio.gather(*(save(factory,series,snapshot,result) for snapshot in snapshots),return_exceptions=True)
            assert sum(isinstance(row,PersistenceConflict) for row in receipts)==1
            winner=next(row for row in receipts if not isinstance(row,Exception))
            loser=snapshots[next(i for i,row in enumerate(receipts) if isinstance(row,PersistenceConflict))]
            retry=await save(factory,series,loser,result,winner.version_id)
            assert retry.version_number==2
            async with timetable_reader(factory,TENANT) as repository:
                assert [row.version_number for row in await repository.list_versions(series)]==[1,2]
            assert await counts(factory,series)==(2,4,4,2)
        finally:await engine.dispose()
    run(execute())


def test_rls_read_isolation_and_cross_tenant_write_authorization():
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example();series,snapshot=await setup(factory,document)
            receipt=await save(factory,series,snapshot,result)
            async with timetable_reader(factory,OTHER_TENANT) as repository:
                assert await repository._session.scalar(select(TimetableSeries).where(TimetableSeries.id==series)) is None
                for action in (repository.get_series(series),repository.get_timetable(receipt.version_id),repository.get_version(series,1)):
                    with pytest.raises(PersistenceConflict):await action
            with pytest.raises(PersistenceConflict):await persist_generation_result(factory,OTHER_TENANT,series,snapshot,result,expected_previous_version_id=None)
            with pytest.raises(DBAPIError) as caught:
                async with teaching_unit_of_work(factory,TENANT) as repository:
                    other=document.model_copy(deep=True);other.institution_id=OTHER_TENANT
                    await SqlTeachingRepository(repository._session,OTHER_TENANT).store_snapshot(other)
            assert caught.value.orig.sqlstate=="42501"
            assert await counts(factory,series)==(1,2,2,1)
        finally:await engine.dispose()
    run(execute())


def test_database_immutability_trigger_and_readonly_facade_reject_writes():
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example();series,snapshot=await setup(factory,document)
            await save(factory,series,snapshot,result)
            with pytest.raises(DBAPIError) as mutation:
                async with teaching_unit_of_work(factory,TENANT) as repository:
                    await repository._session.execute(update(TimetableSeries).where(TimetableSeries.id==series,
                        TimetableSeries.institution_id==TENANT).values(series_reference="SYNTHETIC_FORBIDDEN"))
            assert mutation.value.orig.sqlstate=="55000"
            with pytest.raises(DBAPIError) as readonly:
                async with timetable_reader(factory,TENANT) as repository:await repository.create_series("SYNTHETIC_FORBIDDEN")
            assert readonly.value.orig.sqlstate=="25006"
            assert await counts(factory,series)==(1,2,2,1)
        finally:await engine.dispose()
    run(execute())


def test_database_unique_constraints_prevent_duplicate_bookings_and_coverage():
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example();series,snapshot=await setup(factory,document)
            receipt=await save(factory,series,snapshot,result)
            for model in (Delivery,DeliveryCoverage):
                with pytest.raises(DBAPIError) as error:
                    async with teaching_unit_of_work(factory,TENANT) as repository:
                        row=(await repository._session.scalars(select(model).where(model.institution_id==TENANT,model.version_id==receipt.version_id))).first()
                        fields={col.name:getattr(row,col.name) for col in row.__table__.columns}
                        repository._session.expunge(row)
                        if model is Delivery:fields["id"]=uuid4()
                        repository._session.add(model(**fields));await repository._session.flush()
                assert error.value.orig.sqlstate=="23505"
            assert await counts(factory,series)==(1,2,2,1)
        finally:await engine.dispose()
    run(execute())


def test_elevated_owner_role_is_rejected_before_save():
    async def execute():
        runtime=isolated_engine();owner=isolated_engine(admin=True)
        factory=async_sessionmaker(runtime,expire_on_commit=False)
        try:
            document,result=example();series,snapshot=await setup(factory,document)
            with pytest.raises(PersistenceConflict,match="BYPASSRLS"):
                await save(async_sessionmaker(owner,expire_on_commit=False),series,snapshot,result)
            assert await counts(factory,series)==(0,0,0,0)
        finally:
            await runtime.dispose();await owner.dispose()
    run(execute())


@pytest.mark.parametrize("mixed",[False,True])
def test_combined_and_mixed_delivery_write_readback_has_no_duplicate_coverage(mixed):
    from test_combination_solver import inputs,venues
    from test_staff_solver import windows
    from test_persistence_contracts import snapshot_for
    from schedence_teaching.preparation import prepare_group_combinations
    from schedence_teaching.solver import CombinationSolverConfig,solve_timetable
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            source=inputs();prepared=prepare_group_combinations(source)
            config,rooms,options=windows(120),venues(large_minutes=30) if mixed else venues(),CombinationSolverConfig()
            document=snapshot_for(prepared.prepared,config,specializations=source.group_specializations,
                contexts=dict(venue_input=rooms,combination_input=source,combination_config=options))
            result=solve_timetable(prepared.prepared,config,venue_input=rooms,combination_input=prepared,combination_config=options)
            series,snapshot=await setup(factory,document)
            receipt=await save(factory,series,snapshot,result)
            assert await counts(factory,series)==(1,3 if mixed else 2,4,1)
            async with timetable_reader(factory,TENANT) as repository:
                record=await repository.get_timetable(receipt.version_id)
                assert len(record.coverage)==4
                assert sum(row['end_minute']-row['start_minute'] for row in record.deliveries)==(90 if mixed else 60)
                assert sum(len(row['coverage']) for row in record.deliveries)==4
                assert record.coverage_report()['unscheduled_occurrences']==0
        finally:await engine.dispose()
    run(execute())


def test_one_hybrid_version_round_trips_online_lecture_and_physical_lab():
    from copy import deepcopy
    from test_online_attendance_solver import problem
    from test_persistence_contracts import snapshot_for
    from schedence_teaching.preparation import prepare_timetable_input
    from schedence_teaching.schemas import SessionType
    from schedence_teaching.solver import solve_timetable
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            prepared,config,venues=problem()
            teaching=prepared.input_snapshot.model_copy(deep=True)
            teaching.session_requirements[1].session_type=SessionType.LAB
            prepared=prepare_timetable_input(teaching)
            settings=config.model_dump(mode='json')
            tuesday=deepcopy(settings['days'][0]);tuesday['day']='TUESDAY'
            settings['days'].append(tuesday)
            settings['delivery_calendar']['days']=[dict(day='MONDAY',stream='WEEKEND',mode='ONLINE',allowed_session_types=['LECTURE']),
                dict(day='TUESDAY',stream='WEEKEND',mode='PHYSICAL',allowed_session_types=['LAB'])]
            config=type(config).model_validate(settings)
            rooms=venues.model_dump(mode='json')
            for venue in rooms['venues']:venue['venue_type']='Computer Lab'
            for calendar in rooms['calendars']:calendar['available_windows']['TUESDAY']=deepcopy(calendar['available_windows']['MONDAY'])
            venues=type(venues).model_validate(rooms)
            document=snapshot_for(prepared,config,contexts=dict(venue_input=venues))
            result=solve_timetable(prepared,config,venue_input=venues)
            series,snapshot=await setup(factory,document)
            receipt=await save(factory,series,snapshot,result)
            async with timetable_reader(factory,TENANT) as repository:
                record=await repository.get_timetable(receipt.version_id)
                assert {row['delivery_mode'] for row in record.deliveries}=={'ONLINE','PHYSICAL'}
                assert all((row['venue_reference'] is None)==(row['delivery_mode']=='ONLINE') for row in record.deliveries)
                assert {row['session_type'] for row in record.coverage}=={'LECTURE','LAB'}
        finally:await engine.dispose()
    run(execute())

"""Policy metadata round-trip using the existing guarded loopback test database."""
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from schedence_db.runtime import run
from schedence_teaching.persistence.repositories import teaching_unit_of_work
from schedence_teaching.persistence.timetable_service import timetable_reader
from schedence_teaching.persistence.policy import version_policy_metadata
from schedence_teaching.reporting.workload import lecturer_workload
from schedence_teaching.reporting.contracts import WorkloadQuery
from test_institution_policy import document, run_bound
from test_timetable_persistence_isolated import isolated_engine, setup

pytestmark=pytest.mark.skipif(os.environ.get('SCHEDENCE_TEACHING_ISOLATED_DB_TESTS') != '1',
    reason='Requires explicitly configured isolated localhost PostgreSQL; never production.')


@pytest.mark.parametrize('save_method',['append_attempt','save_validated_result'])
def test_new_versions_roundtrip_the_resolved_policy_digest(save_method):
    bound,result,_=run_bound(document(online=True))

    async def execute():
        engine=isolated_engine()
        try:
            factory=async_sessionmaker(engine,expire_on_commit=False)
            series,snapshot=await setup(factory,bound)
            async with teaching_unit_of_work(factory,bound.institution_id) as repository:
                saved=await getattr(repository,save_method)(series,snapshot,result,expected_previous_version_id=None)
            async with timetable_reader(factory,bound.institution_id) as repository:
                record=await repository.get_timetable(saved.version_id)
                expected=version_policy_metadata(bound)
                assert all(record.run['result_metadata']['_teaching_persistence'][key]==value for key,value in expected.items())
                assert record.validation['evidence']['_teaching_persistence']==record.run['result_metadata']['_teaching_persistence']
                report=lecturer_workload(record,WorkloadQuery(version_id=saved.version_id))
                assert report['resolved_policy_digest']==bound.resolved_policy_digest
            if save_method=='save_validated_result':
                async with teaching_unit_of_work(factory,bound.institution_id) as repository:
                    retry=await repository.save_validated_result(series,snapshot,result,expected_previous_version_id=None)
                    assert retry.version_id==saved.version_id
        finally:
            await engine.dispose()
    run(execute())

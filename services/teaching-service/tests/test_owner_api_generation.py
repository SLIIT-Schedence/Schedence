"""OWNER_API generation: worker inputs from the owners' HTTP exports with the caller's token, no owner DB logins."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from schedence_teaching.api.errors import ApiError
from schedence_teaching.api.runtime import TeachingSettings, configure_owner_api_generation
from schedence_teaching.generation.http_readiness import OwnerApiGenerationInputs
from schedence_teaching.generation.inputs import GenerationWork
from schedence_teaching.generation.jobs import DevelopmentGenerationJobs
from schedence_teaching.generation.persistence import TeachingDatabasePersistence

from test_database_generation import database_fixture
from test_elective_enrollment import elective_fixture
from test_generation_jobs import Executor, finished, poll, post, request_for
from test_persistence_contracts import TENANT, example
from test_portable_readiness import export
from test_teaching_read_api import api


class Owners:
    def __init__(self, evidence):
        self.evidence, self.calls = evidence, []

    async def get(self, owner, path, institution, token, **kwargs):
        self.calls.append((owner, path, institution, token))
        return export(owner, getattr(self.evidence, owner))


class Prepared:
    def __init__(self, evidence):
        self.evidence = evidence

    async def by_id(self, institution, snapshot_id):
        matches = institution == self.evidence.document.institution_id and snapshot_id == self.evidence.snapshot_id
        return self.evidence.document if matches else None


def test_inputs_come_from_owner_exports_and_the_stored_snapshot(database_fixture):
    evidence, _ = database_fixture
    owners = Owners(evidence)
    inputs = OwnerApiGenerationInputs(owners, Prepared(evidence), academic_path='/api/v1/academic/teaching-inputs',
        resource_path='/api/v1/resource/teaching-inputs')
    request = request_for(evidence.document, snapshot_id=evidence.snapshot_id, academic_years=[4], streams=['WEEKDAY'])
    work = asyncio.run(inputs.prepare(request, bearer_token='caller-one'))
    assert work.request == request and work.document.institution_id == TENANT
    assert all(row.academic_year == 4 and row.stream.value == 'WEEKDAY' for row in work.document.scopes)
    assert sorted((owner, token) for owner, _, _, token in owners.calls) == [('academic', 'caller-one'), ('resource', 'caller-one')]
    with pytest.raises(ApiError) as error:
        asyncio.run(inputs.prepare(request_for(evidence.document, snapshot_id=uuid4()), bearer_token='caller-one'))
    assert error.value.status == 400


class TokenInputs:
    requires_bearer_token = True

    def __init__(self, document):
        self.document, self.tokens = document, []

    async def prepare(self, request, *, bearer_token):
        self.tokens.append(bearer_token)
        return GenerationWork(request, self.document)


def test_jobs_hand_the_callers_token_only_to_inputs_that_need_it():
    document, result = example()
    inputs = TokenInputs(document)
    jobs = DevelopmentGenerationJobs(inputs, Executor(result), enabled=True)
    assert jobs.requires_bearer_token

    async def run():
        job = await jobs.submit(request_for(document), 'owner-api-1', uuid4(), bearer_token='caller-token')
        return await finished(jobs, job.job_id)
    assert asyncio.run(run()).status == 'COMPLETED'
    assert inputs.tokens == ['caller-token']


def test_generation_route_forwards_the_verified_token(api):
    document, result = example()
    inputs = TokenInputs(document)
    api.runtime.generations = DevelopmentGenerationJobs(inputs, Executor(result), enabled=True)
    response = post(api, request_for(document).model_dump(mode='json'))
    assert response.status_code == 202, response.text
    assert poll(api, response.json()['job_id'])['status'] == 'COMPLETED'
    assert inputs.tokens == [api.headers['Authorization'].removeprefix('Bearer ')]


def test_owner_api_mode_wires_inputs_worker_and_restricted_persistence():
    directory = Path(__file__).resolve().parents[1] / 'local-data/generation-preview/test-workers' / str(uuid4())
    runtime = SimpleNamespace(store=SimpleNamespace(factory=object()), shutdown_hooks=(), generations=None)
    settings = TeachingSettings.model_construct(app_env='development', teaching_generation_mode='OWNER_API',
        teaching_generation_artifact_directory=directory)
    configure_owner_api_generation(runtime, settings, http=object())
    jobs = runtime.generations
    assert isinstance(jobs, DevelopmentGenerationJobs) and jobs.enabled and jobs.requires_bearer_token
    assert isinstance(jobs.inputs, OwnerApiGenerationInputs)
    assert isinstance(jobs.persistence, TeachingDatabasePersistence) and jobs.persistence.mode == 'TEACHING_DATABASE'
    assert jobs.persistence.factory is runtime.store.factory
    with pytest.raises(ValueError, match='development capability'):
        configure_owner_api_generation(runtime, TeachingSettings.model_construct(app_env='production'), http=object())

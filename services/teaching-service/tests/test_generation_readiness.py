"""Read-only generation input readiness: authorization, scope, owner-record validation, never stores or solves."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from schedence_teaching.api.runtime import TeachingSettings, readiness_runtime
from schedence_teaching.generation.readiness import ReadinessScope, assess

from test_persistence_contracts import TENANT, OTHER_TENANT
from readiness_fixtures import api, forbid_solver
from test_teaching_read_api import grant
from test_database_generation import database_fixture
from test_elective_enrollment import elective_fixture

BASE = '/api/v1/teaching/generation-inputs'


class FakeReadiness:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    async def assess(self, institution_id, scope):
        self.calls.append((institution_id, scope))
        if self.error:
            raise self.error
        return dict(ready=True, checks=[dict(key='scope', title='Selected scope', status='READY', detail='2 sessions.')], inputs=None)


def unbound(evidence):
    """The prepared document before owner binding, as the reviewed preparation produces it."""
    return type(evidence.document).model_validate(evidence.document.model_dump() | dict(external_references=[]))


def rows(evidence):
    return dict(academic={name: list(values) for name, values in evidence.academic.items()},
                resource={name: list(values) for name, values in evidence.resource.items()})


def test_unconfigured_readiness_does_not_claim_ready(api):
    response = api.client.get(BASE, params=dict(semester=1), headers=api.headers)
    assert response.status_code == 503 and response.json()['error']['code'] == 'READINESS_UNAVAILABLE'
    assert TeachingSettings.model_fields['teaching_generation_readiness'].default == 'ENABLED'


@pytest.mark.parametrize('role,permissions', [('ACADEMIC_ADMIN', ['teaching:timetables:read']), ('ACADEMIC_ADMIN', ['teaching:approve']),
    ('LECTURER', ['teaching:timetables:read:self']), ('INSTITUTION_ADMIN', [])])
def test_requires_explicit_generation_permission(api, role, permissions):
    api.runtime.readiness = fake = FakeReadiness()
    grant(api, role, permissions)
    assert api.client.get(BASE, params=dict(semester=1), headers=api.headers).status_code == 403
    assert not fake.calls


def test_authentication_and_tenant_boundaries(api):
    api.runtime.readiness = fake = FakeReadiness()
    assert api.client.get(BASE, params=dict(semester=1)).status_code == 401
    other = api.client.get(BASE, params=dict(semester=1, institution_id=str(OTHER_TENANT)), headers=api.headers)
    assert other.status_code == 403 and not fake.calls


def test_selected_scope_is_passed_and_nothing_is_stored(api):
    api.runtime.readiness = fake = FakeReadiness()
    response = api.client.get('/api/teaching/generation-inputs', headers=api.headers,
                              params=[('semester', 1), ('academic_year', 1), ('stream', 'WEEKDAY')])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['ready'] and body['snapshot_stored'] is False and body['university_approved'] is False
    assert body['generation_enabled'] is False and body['academic_years'] == [1] and body['streams'] == ['WEEKDAY']
    assert fake.calls == [(TENANT, ReadinessScope(1, frozenset({1}), frozenset({'WEEKDAY'})))]


@pytest.mark.parametrize('params', [dict(semester=3), dict(semester=1, academic_year=5), dict(semester=1, stream='EVENING'), {}])
def test_invalid_scope_is_rejected_before_reading(api, params):
    api.runtime.readiness = fake = FakeReadiness()
    assert api.client.get(BASE, params=params, headers=api.headers).status_code == 422
    assert not fake.calls


def test_owner_failure_is_unavailable_without_leaking_details(api):
    api.runtime.readiness = FakeReadiness(error=RuntimeError('password=secret host=db.internal'))
    response = api.client.get(BASE, params=dict(semester=1), headers=api.headers)
    assert response.status_code == 503 and response.json()['error']['code'] == 'READINESS_UNAVAILABLE'
    assert 'secret' not in response.text and 'db.internal' not in response.text


def test_production_readiness_uses_http_and_shared_teaching_factory():
    import httpx
    settings = TeachingSettings.model_construct(app_env='production')
    http = httpx.AsyncClient()
    ready = readiness_runtime(settings, factory=object(), http=http)
    assert ready.requires_bearer_token and not hasattr(ready, 'engines')
    import asyncio
    asyncio.run(http.aclose())


def test_prepared_inputs_validate_against_owner_records(database_fixture):
    evidence, _ = database_fixture
    result = assess(unbound(evidence), rows(evidence), TENANT, ReadinessScope(1))
    assert result['ready'], result['checks']
    statuses = {row['key']: row['status'] for row in result['checks']}
    assert statuses['owner-identifiers'] == statuses['owner-records'] == 'READY'
    assert statuses['configuration'] == 'WARNING'  # Never presented as university-approved.
    inputs = result['inputs']
    assert inputs['expected_sessions'] > 0 and inputs['staff'] == len(evidence.document.teaching_input.staff_members)
    assert inputs['configuration_label'] == 'EXPERIMENTAL_CONFIGURATION_NOT_UNIVERSITY_APPROVED'


def test_missing_owner_staff_is_reported_not_invented(database_fixture):
    evidence, _ = database_fixture
    owner = rows(evidence)
    owner['academic']['staff'] = owner['academic']['staff'][1:]
    result = assess(unbound(evidence), owner, TENANT, ReadinessScope(1))
    assert not result['ready'] and result['inputs'] is None
    assert result['checks'][0]['key'] == 'owner-identifiers' and result['checks'][0]['status'] == 'MISSING'


def test_changed_owner_record_fails_validation_instead_of_being_repaired(database_fixture):
    evidence, _ = database_fixture
    owner = rows(evidence)
    venue = owner['resource']['venues'][0]
    owner['resource']['venues'][0] = dict(venue, capacity=venue['capacity'] + 1)
    result = assess(unbound(evidence), owner, TENANT, ReadinessScope(1))
    assert not result['ready']
    assert {row['key']: row['status'] for row in result['checks']}['owner-records'] == 'MISSING'


def test_other_institution_rows_are_rejected(database_fixture):
    evidence, _ = database_fixture
    owner = rows(evidence)
    owner['academic']['staff'] = [dict(row, institution_id=OTHER_TENANT) for row in owner['academic']['staff']]
    result = assess(unbound(evidence), owner, TENANT, ReadinessScope(1))
    assert not result['ready']

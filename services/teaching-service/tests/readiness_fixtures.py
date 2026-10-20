"""Authentication fixture for read-only readiness: no saved timetable, database, job or solve."""

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest
from schedence_auth.testing import TokenFactory

from schedence_teaching.api.identity import IdentityPermissionsClient
from schedence_teaching.api.runtime import TeachingRuntime
from schedence_teaching.main import create_app
from test_persistence_contracts import TENANT


@pytest.fixture(autouse=True)
def forbid_solver(monkeypatch):
    from ortools.sat.python import cp_model

    def forbidden(*args, **kwargs):
        pytest.fail("readiness tests must never build a CP-SAT model or solve")

    monkeypatch.setattr(cp_model, "CpModel", forbidden)
    monkeypatch.setattr(cp_model.CpSolver, "solve", forbidden)


@pytest.fixture
def api():
    tokens, subject, user = TokenFactory(), str(uuid4()), uuid4()
    profile = dict(user_id=str(user), cognito_sub=subject, account_status="ACTIVE",
        institutions=[dict(institution_id=str(TENANT), roles=["ACADEMIC_ADMIN"], permissions=["teaching:generate"])])

    async def identity(request):
        assert request.url.path == "/api/v1/identity/me/permissions"
        return httpx.Response(200, json=profile)

    class UnusedStore:
        def read(self, *args):
            pytest.fail("readiness must not read or write stored timetables")

    http = httpx.AsyncClient(transport=httpx.MockTransport(identity))
    runtime = TeachingRuntime(tokens.verifier(), IdentityPermissionsClient("http://identity.test", http), UnusedStore(),
        shutdown_hooks=(http.aclose,))
    with TestClient(create_app(runtime), raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, runtime=runtime, profile=profile,
            headers={"Authorization": "Bearer " + tokens.access_token(subject)})

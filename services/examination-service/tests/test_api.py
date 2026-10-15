"""API tests. The database source is replaced by the in-memory dataset."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.deps import get_source
from app.api.errors import DATABASE_UNAVAILABLE
from app.auth.dependencies import require_exam_data_access
from app.main import create_app
from app.seed.generator import generate_dataset
from app.sources import DatasetSource
from auth_support import EXAM_ADMIN, make_auth_context

SOURCE = DatasetSource(generate_dataset())
API = "/api/v1"


def _app(source=SOURCE):
    """App whose caller is an authenticated EXAMINATION_ADMIN (auth itself is tested in test_auth.py)."""
    app = create_app(make_auth_context())
    app.dependency_overrides[get_source] = lambda: source
    app.dependency_overrides[require_exam_data_access] = lambda: EXAM_ADMIN
    return app


@pytest.fixture
def client() -> TestClient:
    # Not used as a context manager: the lifespan (real database engine) never starts.
    return TestClient(_app())


# --- Health -------------------------------------------------------------------------


def test_health_does_not_need_the_database(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "examination-service"}


class _FakeTarget:
    def __init__(self, error: Exception | None = None):
        self.error = error

    async def execute(self, statement):
        if self.error:
            raise self.error

        class _Result:
            def scalar_one(self):
                return 1

        return _Result()


def test_database_health_ok(client: TestClient) -> None:
    client.app.state.engine = _FakeTarget()
    response = client.get("/health/db")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_database_health_failure_is_503_without_secrets(client: TestClient) -> None:
    client.app.state.engine = _FakeTarget(
        OperationalError("SELECT 1", None, Exception("password=hunter2 host=ep-secret.neon.tech"))
    )
    response = client.get("/health/db")
    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"
    assert "hunter2" not in response.text and "ep-secret" not in response.text


# --- Modules ------------------------------------------------------------------------


def test_list_modules(client: TestClient) -> None:
    body = client.get(f"{API}/modules").json()
    assert len(body) == 42
    assert sum(m["candidate_count"] for m in body) == 1700
    assert {"module_code", "specializations", "difficulty", "reading_time_minutes"} <= body[0].keys()


def test_filter_modules(client: TestClient) -> None:
    body = client.get(f"{API}/modules", params={"academic_year": 3, "specialization": "DS"}).json()
    assert body and all(m["academic_year"] == 3 and "DS" in m["specializations"] for m in body)


def test_get_module_and_404(client: TestClient) -> None:
    module = client.get(f"{API}/modules/IT3041").json()
    assert module["module_name"] == "Information Retrieval and Web Analytics"
    missing = client.get(f"{API}/modules/IT9999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Module IT9999 not found"}


@pytest.mark.parametrize(
    "url",
    [
        f"{API}/modules/it3041",  # lowercase code
        f"{API}/modules/DROP%20TABLE",
        f"{API}/modules?academic_year=5",
        f"{API}/modules?academic_year=abc",
        f"{API}/modules?exam_type=oral",
        f"{API}/modules?specialization=se;--",
        f"{API}/students?limit=0",
        f"{API}/students?limit=501",
        f"{API}/students?offset=-1",
        f"{API}/students/12345",
        f"{API}/registrations?registration_type=late",
        f"{API}/registrations?module_code=IT1",
        f"{API}/venues?venue_type=garden",
        f"{API}/venues?venue_type=exam_hall",  # old fictional type
        f"{API}/venue-blocks?venue_id=bad;drop",
        f"{API}/venue-blocks?venue_id=%20F1301",  # leading space
        f"{API}/venue-blocks?source=holiday",
        f"{API}/venue-blocks?date_from=2026-13-01",
        f"{API}/venue-blocks?date_from=2026-11-20&date_to=2026-11-16",
        f"{API}/exam-periods/bad%20code",
    ],
)
def test_invalid_input_is_rejected_with_422(client: TestClient, url: str) -> None:
    assert client.get(url).status_code == 422


# --- Students and registrations ----------------------------------------------------------


def test_students_are_paginated(client: TestClient) -> None:
    first = client.get(f"{API}/students").json()
    assert (first["total"], first["limit"], first["offset"], len(first["items"])) == (390, 50, 0, 50)
    last = client.get(f"{API}/students", params={"limit": 100, "offset": 300}).json()
    assert len(last["items"]) == 90
    beyond = client.get(f"{API}/students", params={"offset": 1000}).json()
    assert beyond["items"] == [] and beyond["total"] == 390


def test_paging_through_all_students_returns_each_once(client: TestClient) -> None:
    seen = []
    offset = 0
    while True:
        page = client.get(f"{API}/students", params={"limit": 500, "offset": offset}).json()
        seen += [s["student_id"] for s in page["items"]]
        offset += 500
        if offset >= page["total"]:
            break
    assert len(seen) == len(set(seen)) == 390


def test_filter_students(client: TestClient) -> None:
    body = client.get(f"{API}/students", params={"academic_year": 4, "specialization": "CYBER"}).json()
    assert body["total"] == 17


def test_get_student_with_registrations(client: TestClient) -> None:
    repeat = client.get(f"{API}/registrations", params={"registration_type": "repeat", "limit": 1}).json()
    student_id = repeat["items"][0]["student_id"]
    body = client.get(f"{API}/students/{student_id}").json()
    assert body["student_id"] == student_id
    assert any(r["registration_type"] == "repeat" for r in body["registrations"])
    assert client.get(f"{API}/students/IT99999999").status_code == 404


def test_registrations(client: TestClient) -> None:
    all_rows = client.get(f"{API}/registrations").json()
    assert all_rows["total"] == 1700 and len(all_rows["items"]) == 50
    repeat = client.get(f"{API}/registrations", params={"registration_type": "repeat"}).json()
    assert repeat["total"] == 63
    module = client.get(f"{API}/registrations", params={"module_code": "IT1100", "limit": 500}).json()
    assert module["total"] == len(module["items"]) == 113


# --- Venues and periods ---------------------------------------------------------------


def test_venues_and_blocks(client: TestClient) -> None:
    venues = client.get(f"{API}/venues").json()
    assert len(venues) == 62
    f303 = next(v for v in venues if v["venue_id"] == "F303")
    assert f303 == {"venue_id": "F303", "venue_name": "F303", "venue_type": "lecture_hall", "building": "F",
                    "floor": 3, "normal_capacity": 240, "exam_capacity": 120}
    assert len(client.get(f"{API}/venues", params={"venue_type": "smart_classroom"}).json()) == 10
    assert len(client.get(f"{API}/venues", params={"venue_type": "byod_lab"}).json()) == 2
    blocks = client.get(f"{API}/venue-blocks").json()
    assert len(blocks) == 27
    venue = blocks[0]["venue_id"]
    one = client.get(f"{API}/venue-blocks", params={"venue_id": venue}).json()
    assert one and all(b["venue_id"] == venue for b in one)
    teaching = client.get(f"{API}/venue-blocks", params={"source": "simulated_teaching"}).json()
    assert len(teaching) == 22 and all(b["reason"].startswith("SIMULATED") for b in teaching)


@pytest.mark.parametrize("code", ["F1301+F1302", "Robotics Lab", "CyberSecLab", "G1305"])
def test_special_venue_codes_are_accepted(client: TestClient, code: str) -> None:
    response = client.get(f"{API}/venue-blocks", params={"venue_id": code})
    assert response.status_code == 200
    assert all(b["venue_id"] == code for b in response.json())


def test_exam_periods(client: TestClient) -> None:
    periods = client.get(f"{API}/exam-periods").json()
    assert len(periods) == 1
    period = client.get(f"{API}/exam-periods/{periods[0]['period_code']}").json()
    assert len(period["exam_days"]) == 9
    assert period["exclusions"][0]["excluded_date"] == "2026-11-24"
    assert client.get(f"{API}/exam-periods/NO-SUCH-PERIOD").status_code == 404


# --- Errors and safety --------------------------------------------------------------------


class _BrokenSource:
    async def list_modules(self, **kwargs):
        raise OperationalError("SELECT secret_column", None, Exception("password=hunter2 host=ep-secret"))


def test_database_error_is_safe_503() -> None:
    response = TestClient(_app(_BrokenSource())).get(f"{API}/modules")
    assert response.status_code == 503
    assert response.json() == {"detail": DATABASE_UNAVAILABLE}
    for secret in ("hunter2", "ep-secret", "secret_column"):
        assert secret not in response.text


def test_api_is_read_only(client: TestClient) -> None:
    for path in ("/modules", "/students", "/registrations", "/venues", "/venue-blocks", "/exam-periods"):
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)(f"{API}{path}").status_code == 405


def test_openapi_lists_every_endpoint(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == {
        "/health",
        "/health/db",
        f"{API}/modules",
        f"{API}/modules/{{module_code}}",
        f"{API}/students",
        f"{API}/students/{{student_id}}",
        f"{API}/registrations",
        f"{API}/venues",
        f"{API}/venue-blocks",
        f"{API}/exam-periods",
        f"{API}/exam-periods/{{period_code}}",
    }

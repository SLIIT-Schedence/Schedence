"""Export/auth regression tests use the existing in-memory test harness; no Neon writes."""

import asyncio
import copy
import datetime as dt
import io
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from app.api.duty_roster_schemas import RosterExportOut
from app.duty_allocation.demo_service import PreviewStore
from app.duty_allocation.persistence.published_timetable import SavedPublishedTimetableProvider, PublishedTimetableUnavailable, PublishedTimetable
from app.duty_allocation.roster_exports import build_xlsx, DEMO_WARNING
from app.saved_timetables.store import TimetableDetail
from app.saved_timetables.content import SESSION_COLUMNS, ROOM_COLUMNS, SEAT_COLUMNS, content_fingerprint
from test_duty_roster_api import env, save, approved, memory_state, READ  # noqa: F401 (existing fixture)
from test_duty_allocation_api import API

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def test_exports_preserve_every_saved_assignment_and_work_without_a_preview(env):
    roster = approved(env)
    roster = env.get(f"/rosters/{roster['roster_id']}").json()
    before = copy.deepcopy(vars(memory_state(env)))
    env.service.store = PreviewStore()
    reply = env.get(f"/rosters/{roster['roster_id']}/export-data", params={"expected_version": roster["row_version"]})
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert data["warning"] == DEMO_WARNING
    assert len(data["assignments"]) == roster["assignment_count"]
    assert {tuple(a[k] for k in ("session_id", "venue_id", "staff_id", "role")) for a in data["assignments"]} == {
        tuple(a[k] for k in ("session_id", "venue_id", "staff_id", "role")) for a in roster["assignments"]}
    assert all(a["staff_name"] and a["module_name"] for a in data["assignments"])
    assert [a["starts_at"] for a in data["assignments"]] == sorted(a["starts_at"] for a in data["assignments"])
    xlsx = env.get(f"/rosters/{roster['roster_id']}/export.xlsx", params={"expected_version": roster["row_version"]})
    assert xlsx.status_code == 200 and "spreadsheetml.sheet" in xlsx.headers["content-type"]
    assert "attachment" in xlsx.headers["content-disposition"] and "no-store" in xlsx.headers["cache-control"]
    with ZipFile(io.BytesIO(xlsx.content)) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows = sheet.findall("s:sheetData/s:row", NS)
        body = [r for r in rows if int(r.attrib["r"]) >= 9]
        assert len(body) == roster["assignment_count"]
        assert DEMO_WARNING in archive.read("xl/worksheets/sheet1.xml").decode()
        assert sheet.find("s:sheetViews/s:sheetView/s:pane", NS).attrib["ySplit"] == "8"
        assert sheet.find("s:pageSetup", NS).attrib["orientation"] == "landscape"
        assert ET.fromstring(archive.read("xl/tables/table1.xml")).attrib["ref"] == f"A8:N{8+len(body)}"
        for row, assignment in zip(body, data["assignments"]):
            values = ["".join(c.itertext()) for c in row]
            assert values[5] == assignment["module_code"]
            assert values[8] == assignment["staff_name"]
            assert values[9] == assignment["staff_id"]
            assert values[11] == assignment["role"]
            assert values[13] == "APPROVED"
    assert vars(memory_state(env)) == before


@pytest.mark.parametrize("suffix", ("export-data", "export.xlsx", "publication"))
def test_downloads_and_publication_checks_require_permission_and_institution_scope(env, suffix):
    roster = save(env)
    path = f"/rosters/{roster['roster_id']}/{suffix}"
    assert env.client.get(API + path).status_code == 401
    assert env.get(path, headers=env.headers()).status_code == 403
    sub = env.identity.register({uuid.uuid4(): (["EXAMINATION_ADMIN"], [READ])})
    headers = {"Authorization": f"Bearer {env.tokens.access_token(sub)}"}
    assert env.get(path, headers=headers).status_code == 404


def test_stale_downloads_fail_instead_of_exporting_a_different_roster_version(env):
    roster = approved(env)
    for suffix in ("export-data", "export.xlsx"):
        reply = env.get(f"/rosters/{roster['roster_id']}/{suffix}", params={"expected_version": 1})
        assert reply.status_code == 409 and reply.json()["code"] == "STALE_ROSTER"


def test_synthetic_publication_preflight_explains_blockers_without_mutation(env):
    roster = approved(env)
    before = copy.deepcopy(vars(memory_state(env)))
    data = env.get(f"/rosters/{roster['roster_id']}/publication").json()
    assert data["can_publish"] is False
    assert {b["code"] for b in data["blockers"]} >= {"SYNTHETIC_ROSTER_CANNOT_BE_PUBLISHED", "DEMO_TIMETABLE_REFERENCE", "PUBLISHED_TIMETABLE_UNAVAILABLE"}
    assert vars(memory_state(env)) == before


def test_excel_text_is_literal_and_preserves_unicode(env):
    roster = save(env)
    data = RosterExportOut(**env.get(f"/rosters/{roster['roster_id']}/export-data").json())
    data.assignments[0].staff_name = '=HYPERLINK("https://example.invalid") සිංහල'
    with ZipFile(io.BytesIO(build_xlsx(data))) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml").decode()
        assert '=HYPERLINK(' in xml and 'සිංහල' in xml
        assert ET.fromstring(xml).findall(".//s:f", NS) == []


def test_roster_flag_alone_cannot_make_synthetic_current_inputs_publishable(env):
    roster = approved(env)
    rid = uuid.UUID(roster["roster_id"])
    state = memory_state(env)
    state.data.rosters[rid] = replace(state.data.rosters[rid], synthetic=False)
    class Provider:
        async def get_published(self, institution_id, period_code):
            context = await env.service.context(period_code)
            draft = context.inputs.timetable.draft
            return PublishedTimetable("verified-test-reference", draft.input_fingerprint, draft)
    env.app.state.m3_published_timetable_provider = Provider()
    result = env.post(f"/rosters/{rid}/publish", json={"expected_version": roster["row_version"]})
    assert result.status_code == 409 and result.json()["code"] == "UNVERIFIED_DATA_SOURCE"
    assert env.get(f"/rosters/{rid}").json()["status"] == "APPROVED"


def test_published_provider_uses_owner_status_and_checks_content_integrity():
    institution = uuid.uuid4()
    sessions = [{**dict.fromkeys(SESSION_COLUMNS, 1), "session_id": "S", "module_code": "IT1001",
                 "starts_at": dt.datetime(2026, 6, 1, 8), "ends_at": dt.datetime(2026, 6, 1, 10),
                 "session_number": 1, "session_count": 1}]
    rooms = [{**dict.fromkeys(ROOM_COLUMNS, 1), "session_id": "S", "venue_id": "R"}]
    seats = [{**dict.fromkeys(SEAT_COLUMNS, 1), "session_id": "S", "venue_id": "R", "student_id": "existing-test-student", "seat_number": 1}]
    header = {"timetable_id": 71, "period_code": "PERIOD", "status": "PUBLISHED", "revision": 3,
              "validation_passed": True, "published_at": "recorded", "published_by": institution,
              "input_fingerprint": "inputs", "content_fingerprint": content_fingerprint(sessions, rooms, seats)}
    class Store:
        async def published(self, inst, period):
            assert inst == institution and period == "PERIOD"
            return header if header["status"] == "PUBLISHED" else None
        async def get(self, inst, tid):
            assert inst == institution and tid == 71
            return TimetableDetail(header, sessions, rooms)
        async def allocations(self, tid):
            assert tid == 71
            return sessions, rooms, seats
    @asynccontextmanager
    async def open_store():
        yield Store()
    provider = SavedPublishedTimetableProvider(open_store)
    result = asyncio.run(provider.get_published(institution, "PERIOD"))
    assert result.reference == "EXAM-TIMETABLE:71" and result.draft.sessions[0].rooms[0].venue_id == "R"
    header["status"] = "APPROVED"
    assert asyncio.run(provider.get_published(institution, "PERIOD")) is None
    header["status"] = "PUBLISHED"
    rooms[0]["venue_id"] = "CHANGED"
    with pytest.raises(PublishedTimetableUnavailable, match="integrity"):
        asyncio.run(provider.get_published(institution, "PERIOD"))

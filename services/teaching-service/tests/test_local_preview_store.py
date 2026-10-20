"""Read-only local development preview store behind the existing authorized timetable read API.

A real synthetic combined run is written to a temporary 'local-data' root; nothing reads the repository's
ignored local data, connects to a database or solves anything new.
"""

import asyncio
import json
import pickle
from types import SimpleNamespace
from uuid import uuid4

import pytest

from schedence_teaching.api.authorization import READ_PUBLISHED
from schedence_teaching.api.local_preview import PREVIEW_LABEL, LocalPreviewTimetableStore
from schedence_teaching.persistence.ports import PersistenceConflict

from test_persistence_contracts import TENANT
from test_product_generation import evidence, generated  # noqa: F401  (synthetic combined run fixtures)
from test_teaching_read_api import BASE, api, bind, grant  # noqa: F401


def write_run(root, name, work, result, *, tamper=False):
    folder = root / "runs" / name
    folder.mkdir(parents=True)
    (folder / "input-snapshot.json").write_text(work.document.model_dump_json(), encoding="utf-8")
    saved = result.to_dict()
    if tamper:
        saved["runtime_seconds"] += 1
    (folder / "solver-result.json").write_text(json.dumps(saved), encoding="utf-8")
    with (folder / "trusted-result.pickle").open("wb") as handle:
        pickle.dump(result, handle)
    return f"runs/{name}"


def manifest(root, *entries):
    path = root / "frontend-preview" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(entries=[dict(key=key, directory=directory) for key, directory in entries])), encoding="utf-8")
    return path


@pytest.fixture
def preview(generated, tmp_path):
    work, result, _ = generated
    root = tmp_path / "local-data"
    store = LocalPreviewTimetableStore(manifest(root, ("synthetic-run", write_run(root, "one", work, result))), root=root)
    return store, work, result


def test_saved_runs_are_regated_and_served_as_unpublished_validated_drafts(preview):
    store, work, result = preview
    (record,) = store.load()
    assert record.logical_status == "VALIDATED" and record.version["lifecycle_status"] == "DRAFT_VALIDATED"
    assert record.version["preview_label"] == PREVIEW_LABEL and record.version["academic_complete"] is False
    assert record.run["solver_status"] == result.status.value and len(record.deliveries) == len(result.schedule)
    assert record.document.institution_id == work.document.institution_id
    assert store.load()[0].version["id"] == record.version["id"]  # Stable identifiers across restarts.


def test_a_result_differing_from_its_saved_report_is_refused(generated, tmp_path):
    work, result, _ = generated
    root = tmp_path / "local-data"
    store = LocalPreviewTimetableStore(manifest(root, ("tampered", write_run(root, "bad", work, result, tamper=True))), root=root)
    with pytest.raises(PersistenceConflict, match="differs from its saved solver report"):
        store.load()


def test_manifests_and_runs_must_stay_inside_the_local_data_root(generated, tmp_path):
    work, result, _ = generated
    root = tmp_path / "local-data"
    write_run(root, "one", work, result)
    with pytest.raises(ValueError, match="inside the Teaching local-data folder"):
        LocalPreviewTimetableStore(tmp_path / "elsewhere.json", root=root)
    store = LocalPreviewTimetableStore(manifest(root, ("escape", "../outside")), root=root)
    with pytest.raises(ValueError, match="inside the Teaching local-data folder"):
        store.load()


def test_reads_are_institution_scoped_and_refused_until_validation_finishes(preview):
    asyncio.run(scoped_reads(*preview))


async def scoped_reads(store, work, _):
    from schedence_teaching.api.errors import ApiError
    with pytest.raises(ApiError) as error:
        async with store.read(work.document.institution_id):
            pass
    assert error.value.status == 503 and error.value.code in {"PREVIEW_LOADING", "PREVIEW_UNAVAILABLE"}
    store._thread.join()
    filters = dict(years=(), semesters=(), streams=(), specializations=(), groups=(), lecturer=None, venue=None, lifecycle_status=None)
    async with store.read(work.document.institution_id) as repository:
        assert (await repository.find_timetables(**filters))[1] == 1
        assert (await repository.find_timetables(**(filters | dict(years=(4,)))))[1] == 0
    async with store.read(uuid4()) as repository:
        assert (await repository.find_timetables(**filters))[1] == 0  # Another institution sees nothing.


def served(api, preview):
    store, work, result = preview
    store._records = store.load()
    api.runtime.store = store
    return store, work, result


def test_academic_admin_reads_the_preview_through_the_existing_api(api, preview):
    _, work, result = served(api, preview)
    listing = api.client.get(BASE, headers=api.headers, params=dict(institution_id=str(TENANT)))
    assert listing.status_code == 200, listing.text
    (item,) = listing.json()["items"]
    assert item["preview_label"] == PREVIEW_LABEL and item["lifecycle_status"] == "VALIDATED"
    assert item["calendar"]["synthetic_data"] is True and item["academic_complete"] is False
    version = item["version_id"]
    sessions = api.client.get(f"{BASE}/{version}/sessions", headers=api.headers, params=dict(page_size=100))
    assert sessions.status_code == 200 and sessions.json()["total"] == len(result.schedule)
    validation = api.client.get(f"{BASE}/{version}/validation", headers=api.headers).json()
    assert validation["status"] == "PASSED" and validation["hard_violations"] == 0
    # The flag follows the snapshot's own calendar label; this synthetic fixture uses the PP1 test calendar,
    # which is still synthetic testing input (never publishable). Real PP1 runs carry the experimental label.
    assert item["calendar"]["label"] == work.document.calendar_label
    assert validation["experimental_calendar"] is (work.document.calendar_label == "PP1_EXPERIMENTAL_CALENDAR")
    assert validation["optimization"]["priority_order"] == list(result.objective_stage_report.priority_order)


def test_students_and_lecturers_never_see_an_unpublished_preview(api, preview):
    served(api, preview)
    grant(api, "STUDENT", [READ_PUBLISHED])
    bind(api, group_ids=frozenset({"A"}))
    response = api.client.get(BASE, headers=api.headers)
    assert response.status_code in {200, 403}
    if response.status_code == 200:
        assert response.json()["items"] == []


def test_the_preview_store_is_refused_in_production_and_with_generation(tmp_path, monkeypatch):
    from schedence_teaching.api.runtime import TeachingSettings, local_preview_runtime
    base = dict(cognito_region="ap-south-1", cognito_user_pool_id="ap-south-1_EXAMPLE",
                teaching_read_store="LOCAL_PREVIEW", teaching_local_preview_manifest=tmp_path / "manifest.json")
    for changes in (dict(app_env="production"), dict(teaching_generation_mode="DEVELOPMENT")):
        settings = SimpleNamespace(**(dict(app_env="development", teaching_generation_mode="DISABLED") | base | changes))
        with pytest.raises(ValueError, match="development reads only"):
            local_preview_runtime(settings)
    assert TeachingSettings.model_fields["teaching_read_store"].default == "DATABASE"


@pytest.mark.parametrize("floor", [13, "Ground", None])
def test_session_venues_return_every_floor_form_the_source_schema_allows(floor):
    # Real venue records store some floors as numbers; the response must not reject them.
    from schedence_teaching.api.contracts import VenueOut
    from schedence_teaching.schemas import Venue
    venue = Venue(venue_id="SYN-ROOM", venue_name="Synthetic", venue_type="Lecture Hall", capacity=60, floor=floor)
    out = VenueOut(**{key: getattr(venue, key) for key in ("venue_id", "venue_name", "venue_type", "capacity", "building",
                                                           "floor", "lab_specialization")})
    assert out.floor == floor

"""Phase 1 builder contracts and authenticated routes; no scheduler or live database."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from schedence_auth.testing import TokenFactory
from schedence_teaching.api.errors import ApiError
from schedence_teaching.api.identity import IdentityPermissionsClient
from schedence_teaching.api.runtime import TeachingRuntime
from schedence_teaching.forms.contracts import CreateTemplate, SaveTemplate, DuplicateTemplate, Question
from schedence_teaching.forms.presets import PRESET_ID, teaching_preferences
from schedence_teaching.forms.service import FormTemplates
from schedence_teaching.forms.store import FormsBase, FormTemplateDraft, SqlFormTemplateStore
from schedence_teaching.main import create_app

TENANT = UUID("11111111-1111-4111-8111-111111111111")
OTHER = UUID("22222222-2222-4222-8222-222222222222")
BASE = "/api/v1/teaching/form-templates"


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    from ortools.sat.python import cp_model
    import psycopg

    def forbidden(*args, **kwargs):
        pytest.fail("Form builder tests must not solve or connect to PostgreSQL.")

    monkeypatch.setattr(cp_model, "CpModel", forbidden)
    monkeypatch.setattr(cp_model.CpSolver, "solve", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", forbidden)


class MemoryForms:
    def __init__(self):
        self.rows = {}
        self.calls = 0

    async def create(self, template):
        self.calls += 1
        self.rows[template.id] = template.model_copy(deep=True)

    async def get(self, institution, template_id):
        self.calls += 1
        row = self.rows.get(template_id)
        return row.model_copy(deep=True) if row and row.institution_id == institution else None

    async def list(self, institution, offset, limit):
        self.calls += 1
        rows = sorted((row for row in self.rows.values() if row.institution_id == institution), key=lambda row: row.id)
        return tuple(row.model_copy(deep=True) for row in rows[offset:offset + limit]), len(rows)

    async def replace(self, template, expected_revision):
        self.calls += 1
        old = self.rows.get(template.id)
        if old is None or (old.institution_id, old.revision) != (template.institution_id, expected_revision):
            raise ApiError(409, "REVISION_CONFLICT", "The template changed.")
        self.rows[template.id] = template.model_copy(deep=True)


@pytest.fixture
def api():
    tokens, subject, user = TokenFactory(), str(uuid4()), uuid4()
    profile = dict(user_id=str(user), cognito_sub=subject, account_status="ACTIVE", institutions=[
        dict(institution_id=str(TENANT), roles=["ACADEMIC_ADMIN"], permissions=["teaching:generate"])])
    identity = SimpleNamespace(calls=0, status=200)

    async def handler(request):
        identity.calls += 1
        assert request.method == "GET" and request.url.path == "/api/v1/identity/me/permissions"
        return httpx.Response(identity.status, json=profile)

    class NoTimetables:
        def read(self, *args):
            pytest.fail("Form operations must not access timetable history.")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    forms = MemoryForms()
    runtime = TeachingRuntime(tokens.verifier(), IdentityPermissionsClient("http://identity.test", http),
        NoTimetables(), shutdown_hooks=(http.aclose,), forms=forms)
    with TestClient(create_app(runtime), raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, tokens=tokens, subject=subject, user=user, profile=profile,
            identity=identity, forms=forms, runtime=runtime,
            headers={"Authorization": "Bearer " + tokens.access_token(subject)})


def create(api, *, preset=True, title="Lecturer Teaching Preferences", institution=TENANT, questions=None):
    body = dict(institution_id=str(institution), title=title)
    if preset:
        body["preset_id"] = PRESET_ID
    if questions is not None:
        body["questions"] = questions
    return api.client.post(BASE, headers=api.headers, json=body)


def edits(row):
    return dict(institution_id=row["institution_id"], title=row["title"], description=row["description"],
        expected_revision=row["revision"], questions=deepcopy(row["questions"]))


def test_predefined_template_is_day_parts_only_and_is_not_saved_by_reading(api):
    response = api.client.get(BASE + "/presets", params={"institution_id": str(TENANT)}, headers=api.headers)
    assert response.status_code == 200
    (preset,) = response.json()
    assert preset["id"] == PRESET_ID and preset["title"] == "Lecturer Teaching Preferences"
    availability = preset["questions"][0]
    assert availability["matrix"] == dict(days=["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        day_parts=["MORNING", "AFTERNOON"], values=["Preferred", "Available", "Not Preferred", "Not Available"])
    assert [q["field_key"] for q in preset["questions"][:3]] == [
        "teaching_availability", "module_preferences", "preferred_weekly_teaching_hours"]
    assert preset["questions"][-1]["kind"] == "SUGGESTED" and not preset["questions"][-1]["required"]
    assert api.forms.calls == 0 and not api.forms.rows


def test_create_save_rename_reorder_and_read_back_draft(api):
    response = create(api)
    assert response.status_code == 201
    row = response.json()
    assert row["status"] == "DRAFT" and row["revision"] == 1 and row["created_by"] == str(api.user)
    body = edits(row)
    body["questions"][0].update(label="When would you prefer to teach?", help_text="Select each day part.")
    body["questions"].reverse()
    body["title"] = "Semester 1 preferences"
    saved = api.client.put(BASE + "/" + row["id"], headers=api.headers, json=body)
    assert saved.status_code == 200
    updated = saved.json()
    assert updated["revision"] == 2 and updated["created_at"] == row["created_at"]
    assert updated["questions"][-1]["label"] == "When would you prefer to teach?"
    fetched = api.client.get(BASE + "/" + row["id"], headers=api.headers, params={"institution_id": str(TENANT)})
    assert fetched.json() == updated


@pytest.mark.parametrize("change", ["remove", "field_key", "field_type", "kind", "id", "required", "day", "part", "value", "bounds", "module_reference"])
def test_locked_schema_cannot_be_removed_or_changed(api, change):
    row = create(api).json()
    body = edits(row)
    first = body["questions"][0]
    if change == "remove": body["questions"].pop(0)
    elif change == "field_key": first["field_key"] = "other_availability"
    elif change == "field_type": first.update(field_type="text", matrix=None)
    elif change == "kind": first["kind"] = "CUSTOM"
    elif change == "id": first["id"] = "custom.replacement"
    elif change == "required": first["required"] = False
    elif change == "day": first["matrix"]["days"] = ["MONDAY"]
    elif change == "part": first["matrix"]["day_parts"] = ["09:00-10:00"]
    elif change == "value": first["matrix"]["values"] = ["Available"]
    elif change == "bounds": body["questions"][2]["maximum"] = 20
    elif change == "module_reference": body["questions"][1]["module_reference"] = "module_code"
    response = api.client.put(BASE + "/" + row["id"], headers=api.headers, json=body)
    assert response.status_code == 422
    original = api.client.get(BASE + "/" + row["id"], headers=api.headers, params={"institution_id": str(TENANT)})
    assert original.json() == row


def test_suggested_and_custom_questions_are_editable_and_removable(api):
    row = create(api).json()
    body = edits(row)
    body["questions"][-1].update(label="Anything else?", field_type="text", required=True)
    body["questions"].append(dict(id="custom.equipment", kind="CUSTOM", label="Equipment requested",
        field_type="multi_choice", choices=["Projector", "Whiteboard"]))
    result = api.client.put(BASE + "/" + row["id"], headers=api.headers, json=body)
    assert result.status_code == 200
    updated = result.json()
    assert updated["questions"][-1]["field_key"] is None
    body = edits(updated)
    body["questions"] = body["questions"][:3]
    result = api.client.put(BASE + "/" + row["id"], headers=api.headers, json=body)
    assert result.status_code == 200 and len(result.json()["questions"]) == 3


@pytest.mark.parametrize("field_type,settings", [
    ("text", {}), ("long_text", {}), ("number", {"minimum": 0, "maximum": 40}),
    ("boolean", {}), ("date", {}), ("single_choice", {"choices": ["A", "B"]}),
    ("multi_choice", {"choices": ["A", "B"]}),
])
def test_blank_templates_accept_supported_custom_fields(api, field_type, settings):
    result = create(api, preset=False, questions=[dict(label="Custom question", field_type=field_type, **settings)])
    assert result.status_code == 201
    question = result.json()["questions"][0]
    assert question["kind"] == "CUSTOM" and question["id"].startswith("custom.")


@pytest.mark.parametrize("question", [
    dict(label="Exact times", field_type="availability_matrix", matrix={}),
    dict(label="Scheduler key", field_type="text", field_key="teaching_availability"),
    dict(id="system.forged", label="Forged", field_type="text"),
    dict(id="suggested.forged", kind="SUGGESTED", label="Forged", field_type="text"),
    dict(id="custom.forged", kind="LOCKED", label="Forged", field_type="text", field_key="new_key"),
    dict(label="No choices", field_type="single_choice"),
    dict(label="Duplicates", field_type="multi_choice", choices=["A", "A"]),
    dict(label="Invalid range", field_type="number", minimum=10, maximum=1),
    dict(label="Wrong bounds", field_type="text", minimum=1),
    dict(label="Boolean bound", field_type="number", minimum=True),
    dict(label="Unknown type", field_type="file"),
    dict(label="", field_type="text"),
    dict(label="Missing field type"),
    dict(label="Unsupported flag", field_type="text", scheduler_locked=False),
])
def test_invalid_and_forged_questions_never_save(api, question):
    assert create(api, preset=False, questions=[question]).status_code == 422
    assert not api.forms.rows


def test_duplicate_preserves_schema_but_edits_are_independent(api):
    original = create(api).json()
    copy = api.client.post(BASE + "/" + original["id"] + "/duplicate", headers=api.headers,
        json=dict(institution_id=str(TENANT), title="Next semester")).json()
    assert copy["id"] != original["id"] and copy["revision"] == 1 and copy["questions"] == original["questions"]
    body = edits(copy)
    body["questions"][0]["label"] = "Copy wording"
    assert api.client.put(BASE + "/" + copy["id"], headers=api.headers, json=body).status_code == 200
    assert api.forms.rows[UUID(original["id"])].questions[0].label == "Teaching availability"
    body["questions"].pop(0)
    body["expected_revision"] = 2
    assert api.client.put(BASE + "/" + copy["id"], headers=api.headers, json=body).status_code == 422


def test_stale_save_does_not_overwrite_later_edits(api):
    row = create(api).json()
    body = edits(row)
    body["title"] = "First editor"
    assert api.client.put(BASE + "/" + row["id"], headers=api.headers, json=body).status_code == 200
    body["title"] = "Stale editor"
    response = api.client.put(BASE + "/" + row["id"], headers=api.headers, json=body)
    assert response.status_code == 409
    assert api.forms.rows[UUID(row["id"])].title == "First editor"


def test_ids_and_managed_fields_cannot_be_injected(api):
    row = create(api).json()
    for field, value in [("id", str(uuid4())), ("status", "PUBLISHED"), ("preset_id", None),
                         ("revision", 200), ("created_by", str(uuid4())), ("schema_version", 2)]:
        body = edits(row) | {field: value}
        assert api.client.put(BASE + "/" + row["id"], headers=api.headers, json=body).status_code == 422


def test_same_identity_with_multiple_institutions_cannot_read_or_modify_other_tenants_record(api):
    row = create(api).json()
    api.profile["institutions"].append(dict(institution_id=str(OTHER), roles=["ACADEMIC_ADMIN"], permissions=["teaching:generate"]))
    unknown = str(uuid4())
    for identifier in (row["id"], unknown):
        assert api.client.get(BASE + "/" + identifier, headers=api.headers, params={"institution_id": str(OTHER)}).status_code == 404
        assert api.client.put(BASE + "/" + identifier, headers=api.headers,
            json=edits(row) | {"institution_id": str(OTHER)}).status_code == 404
        assert api.client.post(BASE + "/" + identifier + "/duplicate", headers=api.headers,
            json={"institution_id": str(OTHER)}).status_code == 404
    assert api.client.get(BASE, headers=api.headers, params={"institution_id": str(OTHER)}).json()["total"] == 0
    assert api.forms.rows[UUID(row["id"])].revision == 1


@pytest.mark.parametrize("permissions", [[], ["teaching:approve"], ["academic:staff:read"], ["platform:institutions:read"]])
def test_permission_must_be_explicit_and_in_requested_institution(api, permissions):
    api.profile["institutions"][0]["permissions"] = permissions
    assert create(api).status_code == 403
    assert api.client.get(BASE + "/presets", headers=api.headers, params={"institution_id": str(TENANT)}).status_code == 403
    assert api.forms.calls == 0


def test_spoofed_claims_revocation_and_platform_grants_cannot_authorize(api):
    api.profile["institutions"][0]["permissions"] = []
    token = api.tokens.access_token(api.subject, **{"cognito:groups": ["ACADEMIC_ADMIN"], "permissions": ["teaching:generate"]})
    assert api.client.post(BASE, headers={"Authorization": "Bearer " + token},
        json=dict(institution_id=str(TENANT), title="Spoof")).status_code == 403
    api.profile["institutions"][0]["permissions"] = ["teaching:generate"]
    assert create(api).status_code == 201
    api.profile["institutions"] = []
    api.profile["platform"] = dict(roles=["SYSTEM_ADMIN"], permissions=["teaching:generate"])
    before = api.forms.calls
    assert create(api).status_code == 403 and api.forms.calls == before


@pytest.mark.parametrize("kind", ["missing", "expired", "id-token", "wrong-client"])
def test_invalid_auth_never_reaches_identity_or_storage(api, kind):
    options = {"expires_in": -30} if kind == "expired" else {"token_use": "id"} if kind == "id-token" else {"client_id": "wrong"} if kind == "wrong-client" else {}
    headers = {} if kind == "missing" else {"Authorization": "Bearer " + api.tokens.access_token(api.subject, **options)}
    response = api.client.get(BASE + "/presets", headers=headers, params={"institution_id": str(TENANT)})
    assert response.status_code == 401 and api.identity.calls == api.forms.calls == 0


def test_identity_failure_and_disabled_store_are_safe(api):
    api.identity.status = 500
    assert create(api).status_code == 503 and api.forms.calls == 0
    api.identity.status = 200
    api.runtime.forms = None
    result = create(api)
    assert result.status_code == 503 and result.json()["error"]["code"] == "FORM_STORE_UNAVAILABLE"


def test_other_institution_and_zero_institution_are_denied_before_storage(api):
    assert create(api, institution=OTHER).status_code == 403
    assert create(api, institution=UUID(int=0)).status_code == 422
    assert api.forms.calls == 0


def test_editing_one_template_does_not_change_the_preset_or_another_template(api):
    first, second = create(api).json(), create(api).json()
    body = edits(first)
    body["questions"][0]["label"] = "Changed wording"
    assert api.client.put(BASE + "/" + first["id"], headers=api.headers, json=body).status_code == 200
    assert api.forms.rows[UUID(second["id"])].questions[0].label == "Teaching availability"
    assert teaching_preferences().questions[0].label == "Teaching availability"


def test_pagination_duplicate_ids_and_request_limits(api):
    for title in ["A", "B", "C"]:
        assert create(api, title=title).status_code == 201
    first = api.client.get(BASE, headers=api.headers, params=dict(institution_id=str(TENANT), page=1, page_size=2)).json()
    second = api.client.get(BASE, headers=api.headers, params=dict(institution_id=str(TENANT), page=2, page_size=2)).json()
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
    assert api.client.get(BASE, headers=api.headers, params=dict(institution_id=str(TENANT), page_size=101)).status_code == 422
    question = dict(id="custom.same", label="Same ID", field_type="text")
    assert create(api, preset=False, questions=[question, question]).status_code == 422
    assert create(api, preset=False, title="x" * 201).status_code == 422
    assert create(api, preset=False, questions=[dict(label="Q", field_type="text") for _ in range(101)]).status_code == 422
    assert create(api, questions=[question]).status_code == 422


def test_route_alias_is_secured_and_no_submission_endpoints_exist(api):
    alias = "/api/teaching/form-templates"
    response = api.client.post(alias, headers=api.headers, json=dict(institution_id=str(TENANT), title="Blank"))
    assert response.status_code == 201
    schema = api.client.get("/openapi.json").json()
    paths = {p for p in schema["paths"] if "/form-templates" in p}
    assert len(paths) == 10
    assert not any(word in p for p in paths for word in ("submissions", "responses", "publish"))
    for p in paths:
        for operation in schema["paths"][p].values():
            assert operation["security"] == [{"BearerToken": []}]
    assert api.client.post(BASE + "/" + response.json()["id"] + "/send", headers=api.headers).status_code == 422


class AsyncSessionAdapter:
    """Exercise the real SQL repository with stdlib SQLite, without a new async driver."""
    def __init__(self, engine):
        self.session = Session(engine)
        self.bind = engine

    async def execute(self, *args, **kwargs): return self.session.execute(*args, **kwargs)
    async def scalar(self, *args, **kwargs): return self.session.scalar(*args, **kwargs)
    async def scalars(self, *args, **kwargs): return self.session.scalars(*args, **kwargs)
    def add(self, row): self.session.add(row)
    async def commit(self): self.session.commit()
    async def rollback(self): self.session.rollback()
    async def close(self): self.session.close()


def test_sql_store_persists_across_service_instances_filters_tenants_and_atomically_rejects_stale_updates():
    async def exercise(engine):
        store = SqlFormTemplateStore(lambda: AsyncSessionAdapter(engine))
        service = FormTemplates(store)
        actor = uuid4()
        row = await service.create(CreateTemplate(institution_id=TENANT, title="Stored", preset_id=PRESET_ID), actor)
        restarted = FormTemplates(SqlFormTemplateStore(lambda: AsyncSessionAdapter(engine)))
        assert await restarted.get(TENANT, row.id) == row
        assert await store.get(OTHER, row.id) is None
        assert await store.list(OTHER, 0, 20) == ((), 0)
        saved = await restarted.save(row.id, SaveTemplate(institution_id=TENANT, title="Updated",
            expected_revision=1, questions=row.questions), actor)
        assert saved.revision == 2
        stale = row.model_copy(update={"revision": 2, "title": "Lost update"})
        with pytest.raises(ApiError) as error:
            await store.replace(stale, 1)
        assert error.value.status == 409
        assert (await store.get(TENANT, row.id)).title == "Updated"
        foreign = saved.model_copy(update={"institution_id": OTHER, "revision": 3})
        with pytest.raises(ApiError): await store.replace(foreign, 2)
        duplicate = await restarted.duplicate(row.id, DuplicateTemplate(institution_id=TENANT), actor)
        assert duplicate.id != row.id and duplicate.revision == 1
        assert (await store.list(TENANT, 0, 1))[1] == 2
        return row

    engine = create_engine("sqlite://", poolclass=StaticPool,
        execution_options={"schema_translate_map": {"teaching": None}})
    try:
        FormsBase.metadata.create_all(engine)
        asyncio.run(exercise(engine))
    finally:
        engine.dispose()


def test_postgres_sessions_set_local_tenant_and_read_only_before_queries():
    class RecordingSession:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        def __init__(self): self.events = []
        async def execute(self, statement, parameters=None): self.events.append((str(statement), parameters))
        async def commit(self): self.events.append("commit")
        async def rollback(self): self.events.append("rollback")
        async def close(self): self.events.append("close")

    async def exercise():
        session = RecordingSession()
        store = SqlFormTemplateStore(lambda: session)
        async with store.transaction(TENANT, read=True):
            assert session.events[0][0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            assert session.events[1][1] == {"tenant": str(TENANT)}
        assert session.events[-1] == "close" and "commit" not in session.events
        session.events.clear()
        with pytest.raises(ValueError):
            async with store.transaction(OTHER):
                assert session.events[0][1] == {"tenant": str(OTHER)}
                raise ValueError("abort")
        assert session.events[-2:] == ["rollback", "close"]

    asyncio.run(exercise())


def test_form_migration_is_additive_and_rendered_offline_without_connections(monkeypatch):
    from test_persistence_migrations import offline_sql
    from schedence_teaching.persistence.models import Base
    sql = offline_sql(monkeypatch)
    assert "CREATE TABLE teaching.form_template_drafts" in sql
    assert "ALTER TABLE teaching.form_template_drafts ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE teaching.form_template_drafts FORCE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY form_template_drafts_tenant" in sql and "WITH CHECK" in sql
    assert "document->>'status' = 'DRAFT'" in sql
    assert "GRANT ALL" not in sql and "CREATE TABLE academic." not in sql
    assert "teaching.form_template_drafts" not in Base.metadata.tables
    assert len(Base.metadata.tables) == 10

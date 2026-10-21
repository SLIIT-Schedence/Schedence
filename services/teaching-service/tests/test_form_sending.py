"""Sending through authenticated HTTP routes and SQL storage; no external services or solver."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from schedence_teaching.api.errors import ApiError
from schedence_teaching.forms.contracts import SaveTemplate
from schedence_teaching.forms.directory import AcademicLecturerDirectory
from schedence_teaching.forms.sending import FormSending, public_origin
from schedence_teaching.forms.sending_contracts import EligibleLecturer, SendForm
from schedence_teaching.forms.sending_store import SqlFormSendingStore
from schedence_teaching.forms.store import FormsBase, SqlFormTemplateStore
from schedence_teaching.forms.service import FormTemplates

from test_form_templates import AsyncSessionAdapter, BASE, OTHER, TENANT, api, create, edits, no_external_execution


@pytest.fixture
def sent(api):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": {"teaching": None}})
    FormsBase.metadata.create_all(engine)
    api.runtime.forms = SqlFormTemplateStore(lambda: AsyncSessionAdapter(engine))
    dept, other_dept = uuid4(), uuid4()
    records = (
        EligibleLecturer(staff_id=uuid4(), staff_number="L1", full_name="Lecturer One", department_ids=(dept,),
            identity_user_id=uuid4(), link_status="READY"),
        EligibleLecturer(staff_id=uuid4(), staff_number="L2", full_name="Unlinked Lecturer", department_ids=(dept,),
            link_status="NO_LINK"),
        EligibleLecturer(staff_id=uuid4(), staff_number="L3", full_name="Ambiguous Lecturer", department_ids=(other_dept,),
            identity_user_id=uuid4(), link_status="AMBIGUOUS_LINK"),
    )
    clock = SimpleNamespace(now=datetime.now(timezone.utc))
    directory = SimpleNamespace(rows=records, calls=0)

    async def eligible(institution):
        directory.calls += 1
        return directory.rows if institution == TENANT else ()

    directory.eligible = eligible
    api.runtime.form_sending = FormSending(SqlFormSendingStore(api.runtime.forms), directory,
        "http://localhost:3000", clock=lambda: clock.now)
    row = create(api).json()
    result = SimpleNamespace(api=api, template=row, directory=directory, clock=clock, dept=dept,
        records=records, service=api.runtime.form_sending)
    yield result
    engine.dispose()


def send(sent, *, kind="ALL_ELIGIBLE", channel="SHARE_LINK", key="send.1", **changes):
    audience = {"kind": kind}
    if kind == "DEPARTMENT": audience["department_id"] = str(sent.dept)
    if kind == "SELECTED": audience["staff_ids"] = [str(sent.records[0].staff_id)]
    body = dict(institution_id=str(TENANT), expected_revision=sent.template["revision"], channel=channel,
        audience=audience, deadline=(sent.clock.now + timedelta(days=7)).isoformat()) | changes
    return sent.api.client.post(BASE + "/" + sent.template["id"] + "/send", json=body,
        headers=sent.api.headers | {"Idempotency-Key": key})


def lecturer(sent, user=None, *, tenant=TENANT, role="LECTURER"):
    sent.api.profile.update(user_id=str(user or sent.records[0].identity_user_id), institutions=[dict(
        institution_id=str(tenant), roles=[role], permissions=[])])


@pytest.mark.parametrize("kind,count", [("ALL_ELIGIBLE", 3), ("DEPARTMENT", 2), ("SELECTED", 1)])
@pytest.mark.parametrize("channel", ["DIRECT", "SHARE_LINK"])
def test_send_audiences_freezes_schema_and_tracks_account_link_problems(sent, kind, count, channel):
    response = send(sent, kind=kind, channel=channel)
    assert response.status_code == 201, response.text
    out = response.json(); row = out["distribution"]
    assert row["recipient_count"] == count and row["frozen_template"] == sent.template
    assert row["unlinked_count"] == int(kind != "SELECTED")
    assert row["ambiguous_count"] == int(kind == "ALL_ELIGIBLE")
    assert out["status"] == "OPEN" and out["submissions_enabled"] is False
    assert bool(out["share_url"]) == (channel == "SHARE_LINK")
    if out["share_url"]:
        assert out["share_url"] == f"http://localhost:3000/forms/{row['id']}?institution_id={TENANT}"
    page = sent.api.client.get(f"/api/v1/teaching/form-distributions/{row['id']}/recipients",
        params={"institution_id": str(TENANT), "page_size": 1}, headers=sent.api.headers).json()
    assert page["total"] == count and len(page["items"]) == 1
    assert page["items"][0]["first_opened_at"] is None


def test_idempotency_and_frozen_version_survive_later_draft_edits(sent):
    first = send(sent).json(); identifier = first["distribution"]["id"]
    assert send(sent).json() == first
    assert send(sent, channel="DIRECT").status_code == 409
    changes = edits(sent.template); changes["title"] = "Edited after sending"
    assert sent.api.client.put(BASE + "/" + sent.template["id"], json=changes, headers=sent.api.headers).status_code == 200
    assert send(sent).json() == first  # A retry remains the original issue after source changes.
    assert send(sent, key="new-request").status_code == 409
    fetched = sent.api.client.get(f"/api/v1/teaching/form-distributions/{identifier}",
        params={"institution_id": str(TENANT)}, headers=sent.api.headers)
    assert fetched.json() == first
    listing = sent.api.client.get('/api/v1/teaching/form-distributions',
        params={"institution_id": str(TENANT)}, headers=sent.api.headers).json()
    assert listing["total"] == 1


@pytest.mark.parametrize("audience", [
    {"kind": "SELECTED", "staff_ids": []},
    {"kind": "SELECTED", "staff_ids": [str(uuid4())]},
    {"kind": "DEPARTMENT", "department_id": str(uuid4())},
    {"kind": "ALL_ELIGIBLE", "department_id": str(uuid4())},
])
def test_empty_invalid_or_foreign_audiences_do_not_create_distributions(sent, audience):
    assert send(sent, audience=audience).status_code == 422
    assert asyncio.run(sent.service.store.list(TENANT, 0, 20))[1] == 0


def test_invalid_deadlines_and_request_ids_rejected(sent):
    assert send(sent, deadline="2030-01-01T00:00:00").status_code == 422
    assert send(sent, deadline=(sent.clock.now - timedelta(seconds=1)).isoformat()).status_code == 422
    assert send(sent, key="bad key").status_code == 422
    assert send(sent, expected_revision=999).status_code == 409
    assert send(sent, institution_id=str(OTHER)).status_code == 403


def test_recipient_reads_frozen_form_and_open_tracking_is_explicit_and_idempotent(sent):
    row = send(sent).json()["distribution"]
    lecturer(sent)
    url = f"/api/v1/teaching/lecturer/form-distributions/{row['id']}"
    query = {"institution_id": str(TENANT)}
    result = sent.api.client.get(url, params=query, headers=sent.api.headers)
    assert result.status_code == 200, result.text
    assert result.json()["frozen_template"] == sent.template and result.json()["first_opened_at"] is None
    opened = sent.api.client.post(url + "/opened", params=query, headers=sent.api.headers)
    assert opened.status_code == 200 and opened.json()["first_opened_at"] is not None
    sent.clock.now += timedelta(hours=1)
    assert sent.api.client.post(url + "/opened", params=query, headers=sent.api.headers).json() == opened.json()
    assert result.json()["submissions_enabled"] is False
    assert sent.api.client.post(url + "/submissions", json={}, params=query, headers=sent.api.headers).status_code == 404


@pytest.mark.parametrize("case,expected", [("outsider",404), ("unlinked",404), ("ambiguous",404),
    ("wrong_role",403), ("wrong_institution",404), ("relinked",403), ("disabled_staff",403)])
def test_shared_link_never_grants_access_without_current_unique_recipient_link(sent, case, expected):
    row = send(sent).json()["distribution"]
    if case == "outsider": lecturer(sent, uuid4())
    elif case == "unlinked": lecturer(sent, uuid4())
    elif case == "ambiguous": lecturer(sent, sent.records[2].identity_user_id)
    elif case == "wrong_role": lecturer(sent, role="STUDENT")
    elif case == "wrong_institution": lecturer(sent, tenant=OTHER)
    else:
        lecturer(sent)
        sent.directory.rows = () if case == "disabled_staff" else (
            sent.records[0].model_copy(update={"staff_id": uuid4()}),)
    response = sent.api.client.get(f"/api/v1/teaching/lecturer/form-distributions/{row['id']}",
        params={"institution_id": str(OTHER if case == "wrong_institution" else TENANT)}, headers=sent.api.headers)
    assert response.status_code == expected, response.text


def test_deadline_closes_read_and_open_but_admin_tracking_survives(sent):
    row = send(sent).json()["distribution"]
    sent.clock.now = datetime.fromisoformat(row["deadline"].replace("Z", "+00:00"))
    admin = sent.api.client.get(f"/api/v1/teaching/form-distributions/{row['id']}",
        params={"institution_id": str(TENANT)}, headers=sent.api.headers)
    assert admin.json()["status"] == "CLOSED"
    lecturer(sent)
    url = f"/api/v1/teaching/lecturer/form-distributions/{row['id']}"
    for method, path in [("GET",url),("POST",url+"/opened")]:
        assert sent.api.client.request(method,path,params={"institution_id":str(TENANT)},headers=sent.api.headers).status_code == 410


def test_direct_assignments_are_discoverable_only_in_authorized_lecturer_inbox(sent):
    row=send(sent,kind="SELECTED",channel="DIRECT").json()["distribution"]
    lecturer(sent)
    url='/api/v1/teaching/lecturer/form-distributions';query={"institution_id":str(TENANT)}
    response=sent.api.client.get(url,params=query,headers=sent.api.headers)
    assert response.status_code==200,response.text
    assert response.json()["total"]==1
    assert response.json()["items"][0]["distribution_id"]==row["id"]
    assert "audience" not in response.json()["items"][0]
    sent.clock.now+=timedelta(days=8)
    assert sent.api.client.get(url,params=query,headers=sent.api.headers).json()["items"][0]["status"]=="CLOSED"
    sent.directory.rows=()
    assert sent.api.client.get(url,params=query,headers=sent.api.headers).json()["total"]==0


def test_admin_permissions_and_authentication_are_required(sent):
    url = BASE + "/" + sent.template["id"] + "/send"
    assert sent.api.client.post(url,json={}).status_code == 401
    lecturer(sent)
    assert send(sent).status_code == 403
    assert sent.api.client.get('/api/v1/teaching/form-recipients',params={"institution_id":str(TENANT)},headers=sent.api.headers).status_code == 403
    assert sent.api.client.get('/api/v1/teaching/form-distributions',params={"institution_id":str(TENANT)},headers=sent.api.headers).status_code == 403


def test_duplicate_mapping_and_employment_and_profile_eligibility_are_not_inferred_from_names():
    user, dept = uuid4(), uuid4()
    staff = [dict(id=uuid4(),staff_number=str(n),full_name="Same Name",identity_user_id=account,employment_status=status)
        for n,account,status in [(1,user,"ACTIVE"),(2,user,"INACTIVE"),(3,None,"ACTIVE"),(4,uuid4(),"ACTIVE")]]
    profiles = [dict(staff_number=str(n),department_id=dept) for n in (1,2,3)]
    rows = AcademicLecturerDirectory.resolve(staff,profiles,[dict(id=dept,name="Computing")])
    assert [(r.staff_number,r.link_status) for r in rows] == [("1","AMBIGUOUS_LINK"),("3","NO_LINK")]
    assert all(r.departments[0].id == dept and r.departments[0].name == "Computing" for r in rows)


def test_source_revision_rechecked_inside_atomic_issue_transaction(sent):
    body = SendForm(institution_id=TENANT,expected_revision=1,channel="DIRECT",
        audience={"kind":"ALL_ELIGIBLE"},deadline=sent.clock.now+timedelta(days=1))
    original = sent.service.store.templates.get

    async def changed_after_read(*args):
        old = await original(*args)
        sent.service.store.templates.get = original
        service = FormTemplates(sent.api.runtime.forms)
        await service.save(old.id,SaveTemplate(institution_id=TENANT,expected_revision=1,
            title="Concurrent edit",questions=old.questions),sent.api.user)
        return old

    sent.service.store.templates.get = changed_after_read
    with pytest.raises(ApiError) as error:
        asyncio.run(sent.service.send(UUID(sent.template["id"]),body,sent.api.user,"race"))
    assert error.value.status == 409
    assert asyncio.run(sent.service.store.list(TENANT,0,20))[1] == 0


@pytest.mark.parametrize("origin", ["http://example.org","https://example.org/path","https://a:password@example.org","https://example.org/?x=1"])
def test_share_origin_cannot_be_injected(origin):
    with pytest.raises(ValueError): public_origin(origin)


def test_sending_configuration_fails_closed_outside_isolated_development():
    from schedence_teaching.api.runtime import configure_form_sending
    from sqlalchemy.engine import make_url
    for env,host,mode in [("production","127.0.0.1","DURABLE_ISOLATED"),
                          ("development","example.neon.tech","DURABLE_ISOLATED"),
                          ("development","127.0.0.1","DISABLED")]:
        runtime = SimpleNamespace(forms=SimpleNamespace(factory=SimpleNamespace(kw={"bind":SimpleNamespace(
            url=make_url(f"postgresql+psycopg://runtime@{host}/isolated"))})),readiness=None)
        settings=SimpleNamespace(app_env=env,teaching_generation_mode=mode)
        with pytest.raises(ValueError): configure_form_sending(runtime,settings)

"""Opt-in local PostgreSQL security checks. Every fixture transaction is rolled back."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest
from sqlalchemy.engine import make_url

from schedence_teaching.forms.contracts import FormTemplate
from schedence_teaching.forms.presets import PRESET_ID, teaching_preferences
from schedence_teaching.forms.sending import digest
from schedence_teaching.forms.sending_contracts import Distribution, Recipient


@pytest.fixture
def pg():
    path = os.environ.get("SCHEDENCE_FORMS_TEST_DATABASE_CONFIG")
    if not path:
        pytest.skip("Explicit isolated PostgreSQL config is required")
    config = json.loads(Path(path).read_text())
    url = make_url(config["runtime_url"])
    assert (url.host, url.port, url.database, url.username) == (
        "127.0.0.1", 52870, "schedence_step12b4_test", "schedence_teaching_runtime")
    assert not url.query
    connection = psycopg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False))
    try:
        with connection.cursor() as q:
            q.execute("SELECT current_database(),inet_server_addr()::text,inet_server_port(),current_setting('neon.project_id',true)")
            assert q.fetchone() == ("schedence_step12b4_test", "127.0.0.1/32", 52870, None)
            q.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert q.fetchone() == (False,False)
            tenant, actor, staff, user = uuid4(), uuid4(), uuid4(), uuid4()
            q.execute("SELECT set_config('schedence.institution_id',%s,true)",(str(tenant),))
            now = datetime.now(timezone.utc)
            template=FormTemplate(id=uuid4(),institution_id=tenant,title="Rollback-only test",description="",
                preset_id=PRESET_ID,questions=teaching_preferences().questions,created_by=actor,updated_by=actor,
                created_at=now,updated_at=now)
            issue=Distribution(id=uuid4(),institution_id=tenant,channel="SHARE_LINK",audience={"kind":"ALL_ELIGIBLE"},
                deadline=now+timedelta(days=1),created_at=now,created_by=actor,frozen_template=template,
                template_sha256=digest(template.model_dump(mode="json")),recipient_count=1,unlinked_count=0,ambiguous_count=0)
            recipient=Recipient(staff_id=staff,staff_number="TEST",full_name="Rollback Fixture",identity_user_id=user,link_status="READY")
            q.execute('INSERT INTO teaching.form_template_drafts(id,institution_id,revision,document) VALUES (%s,%s,1,%s)',
                (template.id,tenant,Jsonb(template.model_dump(mode="json"))))
            q.execute('INSERT INTO teaching.form_distributions(id,institution_id,template_id,template_revision,created_by,idempotency_key,request_sha256,document) VALUES (%s,%s,%s,1,%s,%s,%s,%s)',
                (issue.id,tenant,template.id,actor,"test-key","a"*64,Jsonb(issue.model_dump(mode="json"))))
            q.execute('INSERT INTO teaching.form_distribution_recipients(institution_id,distribution_id,staff_id,identity_user_id,document) VALUES (%s,%s,%s,%s,%s)',
                (tenant,issue.id,staff,user,Jsonb(recipient.model_dump(mode="json",exclude={"first_opened_at"}))))
        yield connection, tenant, issue, recipient
    finally:
        connection.rollback()
        connection.close()


def test_local_postgres_tenant_rls_hides_roster_and_issues(pg):
    connection,tenant,issue,recipient=pg
    with connection.cursor() as q:
        for table in ('form_distributions','form_distribution_recipients'):
            q.execute(f'SELECT count(*) FROM teaching.{table}');assert q.fetchone()[0]==1
        q.execute("SELECT set_config('schedence.institution_id',%s,true)",(str(uuid4()),))
        for table in ('form_distributions','form_distribution_recipients'):
            q.execute(f'SELECT count(*) FROM teaching.{table}');assert q.fetchone()[0]==0
        with pytest.raises(psycopg.errors.InsufficientPrivilege),connection.transaction():
            q.execute('INSERT INTO teaching.form_distribution_recipients(institution_id,distribution_id,staff_id,identity_user_id,document) VALUES (%s,%s,%s,%s,%s)',
                (tenant,issue.id,recipient.staff_id,recipient.identity_user_id,Jsonb(recipient.model_dump(mode="json",exclude={"first_opened_at"}))))


def test_runtime_cannot_edit_frozen_issues_or_roster_or_delete(pg):
    connection,tenant,issue,recipient=pg
    with connection.cursor() as q:
        for statement in (
            "UPDATE teaching.form_distributions SET template_revision=2",
            "UPDATE teaching.form_distribution_recipients SET identity_user_id=NULL",
            "DELETE FROM teaching.form_distributions", "DELETE FROM teaching.form_distribution_recipients"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege),connection.transaction(): q.execute(statement)


def test_first_open_is_allowed_once_and_timestamp_is_immutable(pg):
    connection,tenant,issue,recipient=pg
    with connection.cursor() as q:
        q.execute('UPDATE teaching.form_distribution_recipients SET first_opened_at=clock_timestamp() RETURNING first_opened_at')
        original=q.fetchone()[0]
        q.execute('UPDATE teaching.form_distribution_recipients SET first_opened_at=coalesce(first_opened_at,clock_timestamp()) RETURNING first_opened_at')
        assert q.fetchone()[0]==original
        with pytest.raises(psycopg.errors.RaiseException),connection.transaction():
            q.execute("UPDATE teaching.form_distribution_recipients SET first_opened_at=first_opened_at+interval '1 hour'")


def test_ready_user_cannot_map_to_multiple_staff_on_same_issue(pg):
    connection,tenant,issue,recipient=pg
    other=recipient.model_copy(update={"staff_id":uuid4()})
    with pytest.raises(psycopg.errors.UniqueViolation),connection.transaction(),connection.cursor() as q:
        q.execute('INSERT INTO teaching.form_distribution_recipients(institution_id,distribution_id,staff_id,identity_user_id,document) VALUES (%s,%s,%s,%s,%s)',
            (tenant,issue.id,other.staff_id,other.identity_user_id,Jsonb(other.model_dump(mode="json",exclude={"first_opened_at"}))))


def test_actual_async_postgres_repository_issue_and_open_are_atomic_and_rollback(pg):
    from sqlalchemy import insert, select, func, text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from schedence_db.runtime import run
    from schedence_teaching.forms.store import FormTemplateDraft, SqlFormTemplateStore
    from schedence_teaching.forms.sending_store import FormDistribution, FormRecipient, SqlFormSendingStore
    _,_,prototype,recipient=pg
    cfg=json.loads(Path(os.environ["SCHEDENCE_FORMS_TEST_DATABASE_CONFIG"]).read_text())

    async def exercise():
        engine=create_async_engine(cfg["runtime_url"],hide_parameters=True)
        try:
            async with engine.connect() as connection:
                transaction=await connection.begin()
                try:
                    actual=(await connection.execute(text("SELECT current_database(),inet_server_addr()::text,inet_server_port(),current_setting('neon.project_id',true)"))).one()
                    assert tuple(actual)==("schedence_step12b4_test","127.0.0.1/32",52870,None)
                    tenant=uuid4()
                    await connection.execute(text("SELECT set_config('schedence.institution_id',:tenant,true)"),{"tenant":str(tenant)})
                    template=prototype.frozen_template.model_copy(update={"id":uuid4(),"institution_id":tenant})
                    issue=prototype.model_copy(update={"id":uuid4(),"institution_id":tenant,"frozen_template":template,
                        "template_sha256":digest(template.model_dump(mode="json"))})
                    await connection.execute(insert(FormTemplateDraft),dict(id=template.id,institution_id=tenant,
                        revision=1,document=template.model_dump(mode="json")))
                    # Session commits join the outer test transaction; its final rollback removes all fixtures.
                    factory=async_sessionmaker(bind=connection,expire_on_commit=False,join_transaction_mode="rollback_only")
                    store=SqlFormSendingStore(SqlFormTemplateStore(factory))
                    assert await store.create(issue,(recipient,),"async-test","b"*64)==issue
                    assert await connection.scalar(select(func.count()).select_from(FormDistribution).where(FormDistribution.institution_id==tenant))==1
                    assert await connection.scalar(select(func.count()).select_from(FormRecipient).where(FormRecipient.institution_id==tenant))==1
                    opened=await store.mark_opened(tenant,issue.id,recipient.staff_id,datetime.now(timezone.utc))
                    assert opened.first_opened_at is not None
                    again=await store.mark_opened(tenant,issue.id,recipient.staff_id,datetime.now(timezone.utc))
                    assert again.first_opened_at==opened.first_opened_at
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()
    run(exercise())

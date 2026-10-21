"""Separate metadata keeps form drafts outside immutable timetable-history models."""

from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, Integer, JSON, Uuid, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from schedence_db import session_scope
from schedence_teaching.api.errors import ApiError

from .contracts import FormTemplate
from .presets import validate_questions


class FormsBase(DeclarativeBase):
    pass


class FormTemplateDraft(FormsBase):
    __tablename__ = "form_template_drafts"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="form_template_revision_positive"),
        Index("ix_form_template_drafts_institution_id", "institution_id", "id"),
        {"schema": "teaching"},
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    institution_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)


class SqlFormTemplateStore:
    def __init__(self, factory):
        self.factory = factory

    @asynccontextmanager
    async def transaction(self, institution, *, read=False):
        async with session_scope(self.factory, commit=False) as session:
            if session.bind.dialect.name == "postgresql":
                if read:
                    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                await session.execute(text("SELECT set_config('schedence.institution_id', :tenant, true)"),
                    {"tenant": str(institution)})
                restricted = await session.scalar(text("""SELECT NOT r.rolsuper AND NOT r.rolbypassrls
                    AND NOT r.rolcreaterole AND NOT r.rolcreatedb
                    AND NOT EXISTS(SELECT 1 FROM pg_roles elevated WHERE (elevated.rolsuper OR elevated.rolbypassrls)
                        AND pg_has_role(current_user,elevated.oid,'MEMBER'))
                    AND EXISTS(SELECT 1 FROM pg_class c WHERE c.oid=to_regclass('teaching.form_template_drafts')
                        AND c.relrowsecurity AND c.relforcerowsecurity
                        AND NOT pg_has_role(current_user,c.relowner,'MEMBER'))
                    FROM pg_roles r WHERE r.rolname=current_user"""))
                if restricted is not True:
                    raise ApiError(503, "FORM_STORE_UNAVAILABLE", "Forms require restricted, tenant-isolated Teaching persistence.")
            yield session
            if not read:
                await session.commit()

    @staticmethod
    def decode(row):
        try:
            result = FormTemplate.model_validate(row.document)
            validate_questions(result.preset_id, result.questions)
        except ValueError:
            raise ApiError(503, "FORM_STORE_INVALID", "The stored form definition is inconsistent.") from None
        if (result.id, result.institution_id, result.revision) != (row.id, row.institution_id, row.revision):
            raise ApiError(503, "FORM_STORE_INVALID", "The stored form definition is inconsistent.")
        return result

    async def create(self, template):
        async with self.transaction(template.institution_id) as session:
            session.add(FormTemplateDraft(id=template.id, institution_id=template.institution_id,
                revision=template.revision, document=template.model_dump(mode="json")))

    async def get(self, institution, template_id):
        async with self.transaction(institution, read=True) as session:
            row = await session.scalar(select(FormTemplateDraft).where(
                FormTemplateDraft.institution_id == institution, FormTemplateDraft.id == template_id))
            return self.decode(row) if row is not None else None

    async def list(self, institution, offset, limit):
        async with self.transaction(institution, read=True) as session:
            condition = FormTemplateDraft.institution_id == institution
            total = await session.scalar(select(func.count()).select_from(FormTemplateDraft).where(condition))
            rows = await session.scalars(select(FormTemplateDraft).where(condition)
                .order_by(FormTemplateDraft.id).offset(offset).limit(limit))
            return tuple(self.decode(row) for row in rows), total

    async def replace(self, template, expected_revision):
        async with self.transaction(template.institution_id) as session:
            changed = await session.execute(update(FormTemplateDraft).where(
                FormTemplateDraft.id == template.id, FormTemplateDraft.institution_id == template.institution_id,
                FormTemplateDraft.revision == expected_revision).values(
                    revision=template.revision, document=template.model_dump(mode="json")))
            if changed.rowcount != 1:
                raise ApiError(409, "REVISION_CONFLICT", "The template changed; reload it before saving.")

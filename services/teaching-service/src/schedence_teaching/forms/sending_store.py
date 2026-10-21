"""Atomic immutable issue + recipient roster; only first-open tracking may change."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint, Uuid, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column

from schedence_teaching.api.errors import ApiError

from .sending import digest
from .sending_contracts import Distribution, Recipient
from .presets import validate_questions
from .store import FormsBase, FormTemplateDraft


class FormDistribution(FormsBase):
    __tablename__ = "form_distributions"
    __table_args__ = (UniqueConstraint("institution_id", "id"),
        UniqueConstraint("institution_id", "created_by", "idempotency_key"),
        ForeignKeyConstraint(["institution_id", "template_id"],
                             ["teaching.form_template_drafts.institution_id", "teaching.form_template_drafts.id"]),
        {"schema": "teaching"})
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    institution_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    template_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    template_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[dict] = mapped_column(FormTemplateDraft.__table__.c.document.type, nullable=False)


class FormRecipient(FormsBase):
    __tablename__ = "form_distribution_recipients"
    __table_args__ = (ForeignKeyConstraint(["institution_id", "distribution_id"],
        ["teaching.form_distributions.institution_id", "teaching.form_distributions.id"]), {"schema": "teaching"})
    institution_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    distribution_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    staff_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    identity_user_id: Mapped[UUID | None] = mapped_column(Uuid)
    document: Mapped[dict] = mapped_column(FormTemplateDraft.__table__.c.document.type, nullable=False)
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SqlFormSendingStore:
    def __init__(self, templates):
        self.templates = templates

    @asynccontextmanager
    async def transaction(self, institution, *, read=False):
        async with self.templates.transaction(institution, read=read) as session:
            if session.bind.dialect.name == "postgresql":
                server = (await session.execute(text("SELECT inet_server_addr()::text, current_setting('neon.project_id',true)"))).one()
                if server[0] not in {"127.0.0.1/32", "127.0.0.1", "::1/128", "::1"} or server[1] is not None:
                    raise ApiError(503, "FORM_SENDING_DISABLED", "Sending storage is not isolated local PostgreSQL.")
            yield session

    @staticmethod
    def decode(row):
        try:
            result = Distribution.model_validate(row.document)
            # Validate server-owned fields again on reads, including frozen locked questions.
            validate_questions(result.frozen_template.preset_id, result.frozen_template.questions)
            if ((result.id, result.institution_id, result.created_by, result.frozen_template.id,
                 result.frozen_template.institution_id, result.frozen_template.revision) !=
                    (row.id, row.institution_id, row.created_by, row.template_id, row.institution_id, row.template_revision)
                    or result.template_sha256 != digest(result.frozen_template.model_dump(mode="json"))):
                raise ValueError("Inconsistent frozen form")
            return result
        except (ValueError, ApiError):
            raise ApiError(503, "FORM_DISTRIBUTION_INVALID", "The stored form distribution is inconsistent.") from None

    @staticmethod
    def decode_recipient(row):
        try:
            recipient = Recipient.model_validate(row.document | {"first_opened_at": row.first_opened_at})
            if (recipient.staff_id, recipient.identity_user_id) != (row.staff_id, row.identity_user_id):
                raise ValueError("Inconsistent recipient")
            return recipient
        except ValueError:
            raise ApiError(503, "FORM_DISTRIBUTION_INVALID", "The stored recipient is inconsistent.") from None

    async def lookup(self, institution, actor, key, request_hash):
        async with self.transaction(institution, read=True) as session:
            row = await session.scalar(select(FormDistribution).where(FormDistribution.institution_id == institution,
                FormDistribution.created_by == actor, FormDistribution.idempotency_key == key))
            if row is None:
                return None
            if row.request_sha256 != request_hash:
                raise ApiError(409, "IDEMPOTENCY_CONFLICT", "This key was already used for a different sending request.")
            return self.decode(row)

    async def create(self, row, recipients, key, request_hash):
        async with self.transaction(row.institution_id) as session:
            source = await session.scalar(select(FormTemplateDraft).where(
                FormTemplateDraft.institution_id == row.institution_id,
                FormTemplateDraft.id == row.frozen_template.id).with_for_update())
            if source is None or source.revision != row.frozen_template.revision:
                raise ApiError(409, "REVISION_CONFLICT", "The template changed; reload before sending.")
            if digest(source.document) != row.template_sha256:
                raise ApiError(409, "REVISION_CONFLICT", "The template contents changed before sending.")
            if row.deadline <= datetime.now(timezone.utc):
                raise ApiError(422, "INVALID_DEADLINE", "The deadline passed before the form could be sent.")
            dialect_insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
            inserted = await session.scalar(dialect_insert(FormDistribution).values(id=row.id,
                institution_id=row.institution_id, template_id=row.frozen_template.id,
                template_revision=row.frozen_template.revision, created_by=row.created_by,
                idempotency_key=key, request_sha256=request_hash, document=row.model_dump(mode="json"))
                .on_conflict_do_nothing(index_elements=["institution_id", "created_by", "idempotency_key"])
                .returning(FormDistribution.id))
            if inserted is None:
                previous = await session.scalar(select(FormDistribution).where(
                    FormDistribution.institution_id == row.institution_id, FormDistribution.created_by == row.created_by,
                    FormDistribution.idempotency_key == key))
                if previous.request_sha256 != request_hash:
                    raise ApiError(409, "IDEMPOTENCY_CONFLICT", "This key was already used for a different sending request.")
                return self.decode(previous)
            await session.execute(insert(FormRecipient), [dict(institution_id=row.institution_id,
                distribution_id=row.id, staff_id=r.staff_id, identity_user_id=r.identity_user_id,
                document=r.model_dump(mode="json", exclude={"first_opened_at"}), first_opened_at=None) for r in recipients])
        return row

    async def get(self, institution, identifier):
        async with self.transaction(institution, read=True) as session:
            row = await session.scalar(select(FormDistribution).where(
                FormDistribution.institution_id == institution, FormDistribution.id == identifier))
            return self.decode(row) if row else None

    async def list(self, institution, offset, limit):
        async with self.transaction(institution, read=True) as session:
            condition = FormDistribution.institution_id == institution
            total = await session.scalar(select(func.count()).select_from(FormDistribution).where(condition))
            rows = await session.scalars(select(FormDistribution).where(condition).order_by(FormDistribution.id)
                .offset(offset).limit(limit))
            return tuple(self.decode(row) for row in rows), total

    async def recipients(self, institution, identifier, offset, limit):
        async with self.transaction(institution, read=True) as session:
            condition = (FormRecipient.institution_id == institution) & (FormRecipient.distribution_id == identifier)
            total = await session.scalar(select(func.count()).select_from(FormRecipient).where(condition))
            rows = await session.scalars(select(FormRecipient).where(condition).order_by(FormRecipient.staff_id)
                .offset(offset).limit(limit))
            return tuple(self.decode_recipient(row) for row in rows), total

    async def recipient_for(self, institution, identifier, user):
        async with self.transaction(institution, read=True) as session:
            rows = tuple(await session.scalars(select(FormRecipient).where(FormRecipient.institution_id == institution,
                FormRecipient.distribution_id == identifier, FormRecipient.identity_user_id == user)))
            return self.decode_recipient(rows[0]) if len(rows) == 1 else None

    async def inbox(self, institution, user, staff, offset, limit):
        async with self.transaction(institution, read=True) as session:
            join = (FormDistribution.institution_id == FormRecipient.institution_id) & (FormDistribution.id == FormRecipient.distribution_id)
            condition = ((FormDistribution.institution_id == institution) & (FormRecipient.identity_user_id == user)
                         & (FormRecipient.staff_id == staff) & (FormRecipient.document["link_status"].as_string() == "READY"))
            total = await session.scalar(select(func.count()).select_from(FormDistribution).join(FormRecipient, join).where(condition))
            rows = (await session.execute(select(FormDistribution, FormRecipient.first_opened_at)
                .join(FormRecipient, join).where(condition).order_by(FormDistribution.id).offset(offset).limit(limit))).all()
            return tuple((self.decode(row), opened) for row, opened in rows), total

    async def mark_opened(self, institution, identifier, staff, now):
        async with self.transaction(institution) as session:
            row = (await session.execute(update(FormRecipient).where(FormRecipient.institution_id == institution,
                FormRecipient.distribution_id == identifier, FormRecipient.staff_id == staff)
                .values(first_opened_at=func.coalesce(FormRecipient.first_opened_at, now))
                .returning(FormRecipient))).scalar_one()
            return self.decode_recipient(row)

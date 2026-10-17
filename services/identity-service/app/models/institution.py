from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, updated_at_column, uuid_pk

INSTITUTION_ACTIVE = "ACTIVE"
INSTITUTION_DISABLED = "DISABLED"
INSTITUTION_STATUSES = (INSTITUTION_ACTIVE, INSTITUTION_DISABLED)


class Institution(Base):
    """An institution (tenant) registered by a System Admin.

    Institutions are never deleted, only disabled: every role assignment and
    provisioning record references one. While an institution is disabled, roles
    held in it grant no permissions.
    """

    __tablename__ = "institutions"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="status"),
        CheckConstraint("code ~ '^[A-Z][A-Z0-9_-]*$'", name="code_format"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=INSTITUTION_ACTIVE, server_default=INSTITUTION_ACTIVE)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("identity.users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    @property
    def is_active(self) -> bool:
        return self.status == INSTITUTION_ACTIVE

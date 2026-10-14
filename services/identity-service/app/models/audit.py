from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk

ACTOR_USER = "USER"
ACTOR_OPERATOR = "OPERATOR"  # a person running an operator command (e.g. bootstrap)
ACTOR_SYSTEM = "SYSTEM"


class IdentityAuditLog(Base):
    """Append-only record of identity events.

    ``safe_metadata`` holds only non-secret context (role codes, provisioning
    IDs, outcome codes). Never tokens, passwords or raw provider errors.
    """

    __tablename__ = "identity_audit_logs"
    __table_args__ = (
        CheckConstraint("actor_type IN ('USER', 'OPERATOR', 'SYSTEM')", name="actor_type"),
        Index("ix_identity_audit_logs_occurred_at", "occurred_at"),
        Index("ix_identity_audit_logs_target_user_id_occurred_at", "target_user_id", "occurred_at"),
        Index("ix_identity_audit_logs_actor_user_id_occurred_at", "actor_user_id", "occurred_at"),
        Index("ix_identity_audit_logs_institution_id_occurred_at", "institution_id", "occurred_at"),
        Index("ix_identity_audit_logs_action", "action"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("identity.users.id", ondelete="RESTRICT"))
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("identity.users.id", ondelete="RESTRICT"))
    institution_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    request_id: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

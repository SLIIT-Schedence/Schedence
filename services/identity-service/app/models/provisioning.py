from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, updated_at_column, uuid_pk

PROVISIONING_PENDING = "PENDING"  # recorded locally, Cognito not called yet (or outcome unknown)
PROVISIONING_COGNITO_CREATED = "COGNITO_CREATED"  # Cognito identity exists; local user not yet created
PROVISIONING_FAILED = "FAILED"  # last attempt failed; safe to retry
PROVISIONING_COMPLETED = "COMPLETED"
PROVISIONING_CANCELLED = "CANCELLED"
OPEN_PROVISIONING_STATUSES = (PROVISIONING_PENDING, PROVISIONING_COGNITO_CREATED, PROVISIONING_FAILED)


class UserProvisioning(Base):
    """Tracks the two-system workflow Cognito -> Neon so partial failures are visible and retryable.

    At most one *open* (pending, Cognito-created or failed) provisioning per
    e-mail exists, which prevents duplicate concurrent onboarding.
    ``error_category`` holds a fixed code, never raw AWS/database messages.
    """

    __tablename__ = "user_provisioning"
    __table_args__ = (
        Index(
            "uq_user_provisioning_open_email",
            "email",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'COGNITO_CREATED', 'FAILED')"),
        ),
        Index("ix_user_provisioning_status_created_at", "status", "created_at"),
        CheckConstraint(
            "status IN ('PENDING', 'COGNITO_CREATED', 'FAILED', 'COMPLETED', 'CANCELLED')", name="status"
        ),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("status <> 'COMPLETED' OR user_id IS NOT NULL", name="completed_has_user"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("identity.users.id", ondelete="RESTRICT"), index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    requested_role_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("identity.roles.id", ondelete="RESTRICT"), nullable=False)
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    cognito_sub: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(50))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("identity.users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_PROVISIONING_STATUSES

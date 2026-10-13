from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, updated_at_column, uuid_pk

ACCOUNT_ACTIVE = "ACTIVE"
ACCOUNT_DISABLED = "DISABLED"
ACCOUNT_STATUSES = (ACCOUNT_ACTIVE, ACCOUNT_DISABLED)


class User(Base):
    """A Schedence account. Passwords never live here: Cognito owns credentials.

    ``cognito_sub`` is the stable link to the Cognito identity. ``email`` is
    stored lower-cased and kept unique so one person cannot get two accounts.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("account_status IN ('ACTIVE', 'DISABLED')", name="account_status"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    cognito_sub: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_status: Mapped[str] = mapped_column(String(20), nullable=False, default=ACCOUNT_ACTIVE, server_default=ACCOUNT_ACTIVE)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    @property
    def is_active(self) -> bool:
        return self.account_status == ACCOUNT_ACTIVE

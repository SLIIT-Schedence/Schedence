from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, updated_at_column, uuid_pk


class Role(Base):
    """A system access role (SUPER_ADMIN, LECTURER, ...).

    These are application access roles, not academic ranks or appointments.
    """

    __tablename__ = "roles"
    __table_args__ = (CheckConstraint("code ~ '^[A-Z][A-Z0-9_]*$'", name="code_format"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    is_system_role: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (CheckConstraint("code ~ '^[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)+$'", name="code_format"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = created_at_column()


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("identity.roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("identity.permissions.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class UserRole(Base):
    """A role granted to a user within one institution.

    Revocation is a soft delete (``revoked_at``) so the assignment history is
    kept. At most one *active* assignment exists per (user, role, institution).
    ``institution_id`` is an opaque identifier; the institution registry
    belongs to the Academic Data Service, so there is no foreign key.
    """

    __tablename__ = "user_roles"
    __table_args__ = (
        Index(
            "uq_user_roles_active_assignment",
            "user_id",
            "role_id",
            "institution_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_user_roles_institution_id_role_id", "institution_id", "role_id"),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= assigned_at", name="revoked_after_assigned"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("identity.roles.id", ondelete="RESTRICT"), nullable=False)
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("identity.users.id", ondelete="RESTRICT"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("identity.users.id", ondelete="RESTRICT"))

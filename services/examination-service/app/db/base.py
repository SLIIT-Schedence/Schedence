"""Declarative base for every table owned by the Examination Service.

All tables live in the ``examinations`` schema. Statements are always
schema-qualified, so nothing depends on ``search_path`` (pool-safe with Neon's
transaction-mode pooler; see the README "Service-owned schemas" section).
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

SCHEMA = "examinations"

# Deterministic constraint names, so migrations and models always agree.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)

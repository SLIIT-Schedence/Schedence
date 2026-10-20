"""Campus-confirmed venue inventory; availability and bookings are separate."""

from sqlalchemy import (CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer,
    MetaData, String, Table, Text, UniqueConstraint, Uuid, func)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="resource")

source_datasets = Table("source_datasets", metadata,
    Column("id", Uuid, primary_key=True), Column("institution_id", Uuid, nullable=False),
    Column("record_key", Text, nullable=False), Column("filename", Text, nullable=False),
    Column("sha256", String(64), nullable=False), Column("provenance_kind", String(40), nullable=False),
    Column("provenance", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("institution_id", "id"), UniqueConstraint("institution_id", "record_key"),
    CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="source_checksum"))

venues = Table("venues", metadata,
    Column("id", Uuid, primary_key=True), Column("institution_id", Uuid, nullable=False),
    Column("record_key", Text, nullable=False), Column("venue_code", String(60), nullable=False),
    Column("venue_name", Text, nullable=False), Column("venue_type", String(40), nullable=False),
    Column("capacity", Integer, nullable=False), Column("building", Text),
    Column("floor", JSONB), Column("lab_specialization", Text),
    Column("source_id", Uuid, nullable=False), Column("source_row", Integer, nullable=False),
    Column("source_values", JSONB, nullable=False), Column("confirmation", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("institution_id", "id"), UniqueConstraint("institution_id", "record_key"),
    UniqueConstraint("institution_id", "venue_code"), CheckConstraint("capacity > 0", name="positive_capacity"),
    ForeignKeyConstraint(["institution_id", "source_id"],
        ["resource.source_datasets.institution_id", "resource.source_datasets.id"], ondelete="RESTRICT"))

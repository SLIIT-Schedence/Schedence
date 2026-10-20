"""Append-only owner import primitives, invoked explicitly by an operator.

No environment reads, engine creation or master-data ownership live here. The
caller supplies the owning service's tables and restricted transaction. A
different value for an existing identity is a conflict, never an UPDATE.
"""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from .ports import PersistenceConflict
from .snapshots import canonical_json


@dataclass(frozen=True)
class RecordLink:
    table: str
    key: str


@dataclass(frozen=True)
class MasterRecord:
    table: str
    key: str
    values: dict
    references: dict[str, RecordLink] = field(default_factory=dict)


def equivalent_values(existing, values):
    return all(canonical_json(existing[key]) == canonical_json(value) for key, value in values.items())


async def append_master_records(session, tables, institution_id: UUID, records):
    """Topologically ordered, bulk inserts; IDs are issued by the owning schema.

    Caller must serialize imports for this institution and verify role/RLS first.
    Conflicts abort the enclosing transaction, including earlier table batches.
    """
    grouped = {}
    for record in records:
        grouped.setdefault(record.table, []).append(record)
    identifiers, counts = {}, {}
    for name, batch in grouped.items():
        table = tables[name]
        existing = {row["record_key"]: row for row in (await session.execute(
            select(table).where(table.c.institution_id == institution_id))).mappings()}
        pending, seen = [], set()
        inserted = reused = 0
        for record in batch:
            if record.key in seen:
                raise PersistenceConflict(f"duplicate import identity in {name}")
            seen.add(record.key)
            values = dict(record.values)
            for column, reference in record.references.items():
                if (reference.table, reference.key) not in identifiers:
                    raise PersistenceConflict(f"unresolved owner reference in {name}")
                values[column] = identifiers[(reference.table, reference.key)]
            row = existing.get(record.key)
            if row is not None:
                if not equivalent_values(row, values):
                    raise PersistenceConflict(f"existing {name} identity has different values; no record overwritten")
                identifier = row["id"]
                reused += 1
            else:
                identifier = uuid4()
                pending.append(dict(id=identifier, institution_id=institution_id, record_key=record.key, **values))
                inserted += 1
            identifiers[(name, record.key)] = identifier
        for offset in range(0, len(pending), 100):
            await session.execute(insert(table), pending[offset:offset + 100])
        counts[name] = dict(inserted=inserted, reused=reused)
    return identifiers, counts


async def reuse_or_insert_staff(session, staff_table, institution_id, staff_members):
    """Reuse existing staff by original staff number; never overwrite master facts.

    A source administrative appointment title does not assert an academic rank.
    ACTIVE is the pre-existing Academic Service operational default, not an
    imported university employment-status assertion. No email or user is invented.
    """
    existing = {row["staff_number"]: row for row in (await session.execute(
        select(staff_table).where(staff_table.c.institution_id == institution_id))).mappings()}
    pending, identifiers, seen = [], {}, set()
    for member in staff_members:
        if member.staff_id in seen:
            raise PersistenceConflict("duplicate staff number in source")
        seen.add(member.staff_id)
        row = existing.get(member.staff_id)
        if row is not None:
            if row["full_name"] != member.staff_name:
                raise PersistenceConflict("staff number conflicts with an existing Academic Service name")
            identifiers[member.staff_id] = row["id"]
            continue
        identifier = uuid4()
        position = member.position.casefold().strip()
        appointment_only = position in {"dean", "associate dean", "hod"} or position.startswith("head")
        pending.append(dict(id=identifier, institution_id=institution_id, staff_number=member.staff_id,
            full_name=member.staff_name, academic_rank=None if appointment_only or member.staff_type.value == "INSTRUCTOR"
                else member.position, employment_status="ACTIVE", email=None, identity_user_id=None))
        identifiers[member.staff_id] = identifier
    for offset in range(0, len(pending), 100):
        await session.execute(insert(staff_table), pending[offset:offset + 100])
    return identifiers, dict(inserted=len(pending), reused=len(identifiers) - len(pending))

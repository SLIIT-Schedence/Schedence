from uuid import uuid4
from types import SimpleNamespace

import pytest

from schedence_teaching.persistence.master_import import (
    MasterRecord, RecordLink, append_master_records, equivalent_values, reuse_or_insert_staff,
)
from schedence_teaching.persistence.ports import PersistenceConflict
from sqlalchemy import Column, MetaData, String, Table, Uuid
from schedence_db.runtime import run


def table():
    return Table("items", MetaData(), Column("id", Uuid), Column("institution_id", Uuid),
        Column("record_key", String), Column("value", String), Column("parent_id", Uuid))


class Rows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self.rows


class Session:
    def __init__(self, rows=()): self.rows, self.inserted = rows, []
    async def execute(self, statement, parameters=None):
        if parameters is not None:
            self.inserted.extend(parameters)
        return Rows(self.rows)


def test_master_import_preserves_owner_id_and_makes_no_update():
    tenant, identifier = uuid4(), uuid4()
    session = Session([dict(id=identifier, record_key="source:1", value="original")])
    identifiers, counts = run(append_master_records(session, {"items": table()}, tenant,
        [MasterRecord("items", "source:1", {"value": "original"})]))
    assert identifiers[("items", "source:1")] == identifier
    assert counts["items"] == dict(inserted=0, reused=1) and not session.inserted


def test_master_import_refuses_to_overwrite_other_owners_record():
    session = Session([dict(id=uuid4(), record_key="source:1", value="original")])
    with pytest.raises(PersistenceConflict, match="no record overwritten"):
        run(append_master_records(session, {"items": table()}, uuid4(),
            [MasterRecord("items", "source:1", {"value": "changed"})]))
    assert not session.inserted


def test_master_import_resolves_parent_reference_once_without_fabricating_external_ids():
    session = Session()
    identifiers, _ = run(append_master_records(session, {"items": table()}, uuid4(), [
        MasterRecord("items", "parent", {"value": "parent", "parent_id": None}),
        MasterRecord("items", "child", {"value": "child"}, {"parent_id": RecordLink("items", "parent")})]))
    assert session.inserted[1]["parent_id"] == identifiers[("items", "parent")]


def test_master_import_rejects_missing_owner_reference():
    session = Session()
    with pytest.raises(PersistenceConflict, match="unresolved"):
        run(append_master_records(session, {"items": table()}, uuid4(), [
            MasterRecord("items", "child", {}, {"parent_id": RecordLink("items", "missing")})]))
    assert not session.inserted


def test_master_import_rejects_duplicate_source_identity():
    session = Session()
    with pytest.raises(PersistenceConflict, match="duplicate"):
        run(append_master_records(session, {"items": table()}, uuid4(), [
            MasterRecord("items", "same", {}), MasterRecord("items", "same", {})]))
    assert not session.inserted


def test_master_json_comparison_preserves_types_but_ignores_map_order():
    assert equivalent_values({"value": {"b": 2, "a": 1}}, {"value": {"a": 1, "b": 2}})
    assert not equivalent_values({"value": 9}, {"value": "9"})


def staff_table():
    return Table("staff", MetaData(), Column("id", Uuid), Column("institution_id", Uuid),
        Column("staff_number", String))


@pytest.mark.parametrize("position,staff_type,expected_rank", [
    ("Professor", "ACADEMIC", "Professor"), ("Assistant Professor", "ACADEMIC", "Assistant Professor"),
    ("Dean", "ACADEMIC", None), ("Head - IT", "ACADEMIC", None),
    ("Associate Dean", "ACADEMIC", None), ("Instructor", "INSTRUCTOR", None),
])
def test_new_staff_preserves_rank_appointment_and_missing_identity_distinctions(position, staff_type, expected_rank):
    member = SimpleNamespace(staff_id="TEST001", staff_name="Synthetic staff", position=position,
        staff_type=SimpleNamespace(value=staff_type))
    session = Session()
    run(reuse_or_insert_staff(session, staff_table(), uuid4(), [member]))
    row, = session.inserted
    assert row["staff_number"] == "TEST001" and row["academic_rank"] == expected_rank
    assert row["email"] is None and row["identity_user_id"] is None


def test_reused_staff_keeps_existing_rank_email_and_owner_id():
    identifier = uuid4()
    session = Session([dict(id=identifier, staff_number="TEST001", full_name="Synthetic staff",
        academic_rank="Existing owner rank", email="synthetic@example.invalid")])
    member = SimpleNamespace(staff_id="TEST001", staff_name="Synthetic staff")
    identifiers, counts = run(reuse_or_insert_staff(session, staff_table(), uuid4(), [member]))
    assert identifiers["TEST001"] == identifier and counts == dict(inserted=0, reused=1)
    assert not session.inserted


def test_existing_staff_name_conflict_is_not_overwritten():
    session = Session([dict(id=uuid4(), staff_number="TEST001", full_name="Existing different person")])
    member = SimpleNamespace(staff_id="TEST001", staff_name="Synthetic staff")
    with pytest.raises(PersistenceConflict):
        run(reuse_or_insert_staff(session, staff_table(), uuid4(), [member]))
    assert not session.inserted

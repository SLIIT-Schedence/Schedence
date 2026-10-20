"""Seeder planning: inserts, updates, idempotency, and no deletes (no database)."""

from __future__ import annotations

import dataclasses

from app.seed.generator import generate_dataset
from app.seed.seeder import TABLE_SPECS, plan_table_change

EXPECTED_TABLES = [
    "seed_modules",
    "seed_module_specializations",
    "seed_students",
    "seed_registrations",
    "seed_venues",
    "seed_venue_blocks",
    "seed_exam_periods",
    "seed_exam_period_exclusions",
    "seed_exam_period_modules",
]


class FakeStore:
    """Applies planned changes the way the seeder does: insert / update, never delete."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[tuple, dict]] = {spec.table: {} for spec in TABLE_SPECS}

    def seed(self, data):
        changes = []
        for spec in TABLE_SPECS:
            rows = self.tables[spec.table]
            desired = [dataclasses.asdict(r) for r in spec.records(data)]
            change = plan_table_change(spec, rows.values(), desired)
            for row in change.inserts:
                rows[tuple(row[k] for k in spec.key)] = dict(row)
            for key, values in change.updates:
                rows[key].update(values)
            changes.append(change)
        return changes


def test_tables_are_seeded_parents_first() -> None:
    assert [spec.table for spec in TABLE_SPECS] == EXPECTED_TABLES


def test_identity_and_legacy_columns_are_not_written() -> None:
    specs = {s.table: s for s in TABLE_SPECS}
    assert "block_id" not in specs["seed_venue_blocks"].columns
    assert "specialization" not in specs["seed_modules"].columns
    assert "has_extra_reading_time" in specs["seed_modules"].columns


def test_first_run_inserts_every_record() -> None:
    data = generate_dataset()
    changes = {c.table: c for c in FakeStore().seed(data)}
    assert len(changes["seed_modules"].inserts) == len(data.modules)
    assert len(changes["seed_module_specializations"].inserts) == len(data.module_specializations)
    assert len(changes["seed_students"].inserts) == len(data.students)
    assert len(changes["seed_registrations"].inserts) == len(data.registrations)
    assert len(changes["seed_venues"].inserts) == len(data.venues)
    assert len(changes["seed_venue_blocks"].inserts) == len(data.venue_blocks)
    assert len(changes["seed_exam_periods"].inserts) == 1
    assert all(not c.updates and c.unchanged == 0 for c in changes.values())


def test_second_run_is_idempotent() -> None:
    data = generate_dataset()
    store = FakeStore()
    store.seed(data)
    sizes = {t: len(rows) for t, rows in store.tables.items()}

    second = store.seed(data)

    assert all(not c.has_writes for c in second)
    assert sum(c.unchanged for c in second) == sum(sizes.values())
    assert {t: len(rows) for t, rows in store.tables.items()} == sizes


def test_changed_record_is_updated_not_duplicated() -> None:
    data = generate_dataset()
    store = FakeStore()
    store.seed(data)
    renamed = dataclasses.replace(data.venues[0], venue_name="Renamed Hall")
    changed = dataclasses.replace(data, venues=(renamed,) + data.venues[1:])

    venues = {c.table: c for c in store.seed(changed)}["seed_venues"]

    assert venues.inserts == []
    assert venues.updates == [((renamed.venue_id,), {"venue_name": "Renamed Hall"})]
    assert len(store.tables["seed_venues"]) == len(data.venues)


def test_rows_missing_from_dataset_are_reported_not_deleted() -> None:
    data = generate_dataset()
    store = FakeStore()
    store.seed(data)
    smaller = dataclasses.replace(data, venue_blocks=data.venue_blocks[1:])

    blocks = {c.table: c for c in store.seed(smaller)}["seed_venue_blocks"]

    assert blocks.extra == 1
    assert len(store.tables["seed_venue_blocks"]) == len(data.venue_blocks)

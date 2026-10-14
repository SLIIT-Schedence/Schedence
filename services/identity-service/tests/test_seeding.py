"""Role and permission seeding (mocked repositories)."""

from __future__ import annotations

import uuid

from app.models import Permission
from app.rbac.catalog import PERMISSIONS, ROLE_CODES, ROLES
from app.services.seeding import RbacSeeder
from tests.fakes import FakeStore, FakeUnitOfWork

EXPECTED_ROLES = {"SUPER_ADMIN", "ACADEMIC_ADMIN", "EXAMINATION_ADMIN", "VENUE_MANAGER", "LECTURER", "STUDENT"}


async def test_default_roles_are_seeded_correctly() -> None:
    store = FakeStore()
    report = await RbacSeeder(FakeUnitOfWork(store)).seed()

    assert {r.code for r in store.roles.values()} == EXPECTED_ROLES == ROLE_CODES
    assert all(r.is_system_role for r in store.roles.values())
    assert sorted(report.roles_created) == sorted(EXPECTED_ROLES)
    assert {p.code for p in store.permissions.values()} == set(PERMISSIONS)

    grants = await FakeUnitOfWork(store).roles.role_permission_codes()
    assert {p for p in grants["SUPER_ADMIN"] if p.startswith("identity:")} == {p for p in PERMISSIONS if p.startswith("identity:")}
    assert "academic:positions:create" in grants["SUPER_ADMIN"]
    assert grants["LECTURER"] == frozenset() and grants["STUDENT"] == frozenset()
    assert not any(p.startswith("identity:") for p in grants["ACADEMIC_ADMIN"])
    assert {"venues:read", "venues:manage"} == grants["VENUE_MANAGER"]


async def test_role_seeding_is_idempotent() -> None:
    store = FakeStore()
    await RbacSeeder(FakeUnitOfWork(store)).seed()
    counts = (len(store.roles), len(store.permissions), len(store.role_permissions))
    commits = store.commits

    second = await RbacSeeder(FakeUnitOfWork(store)).seed()

    assert not second.changed
    assert (len(store.roles), len(store.permissions), len(store.role_permissions)) == counts
    assert store.commits == commits  # nothing to write, nothing committed


async def test_seeding_restores_drifted_definitions_without_duplicates() -> None:
    store = FakeStore()
    await RbacSeeder(FakeUnitOfWork(store)).seed()
    store.role("LECTURER").name = "Renamed by hand"
    store.snapshot()

    report = await RbacSeeder(FakeUnitOfWork(store)).seed()

    assert report.roles_updated == ["LECTURER"]
    assert store.role("LECTURER").name == "Lecturer"
    assert len([r for r in store.roles.values() if r.code == "LECTURER"]) == 1


async def test_extra_grants_are_kept_unless_pruned() -> None:
    store = FakeStore()
    uow = FakeUnitOfWork(store)
    await RbacSeeder(uow).seed()
    extra = Permission(id=uuid.uuid4(), code="legacy:thing:do", description="")
    store.permissions[extra.id] = extra
    store.role_permissions.add((store.role("STUDENT").id, extra.id))
    store.snapshot()

    kept = await RbacSeeder(FakeUnitOfWork(store)).seed()
    assert kept.extra_grants_kept == ["STUDENT -> legacy:thing:do"]

    pruned = await RbacSeeder(FakeUnitOfWork(store)).seed(prune=True)
    assert pruned.grants_removed == ["STUDENT -> legacy:thing:do"]


def test_catalog_has_no_academic_appointment_roles() -> None:
    """HOD / Program Coordinator / Dean are academic appointments, never login roles."""
    codes = {r.code for r in ROLES}
    for appointment in ("HOD", "HEAD_OF_DEPARTMENT", "PROGRAM_COORDINATOR", "DEAN", "FACULTY_COORDINATOR"):
        assert appointment not in codes

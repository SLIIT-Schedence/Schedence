"""Operator-only Super Admin bootstrap (mocked Cognito + repositories)."""

from __future__ import annotations

import pytest

from app.integrations.cognito import STATUS_FORCE_CHANGE_PASSWORD
from app.models import ACTOR_OPERATOR
from app.services.bootstrap import ALREADY_BOOTSTRAPPED, BOOTSTRAPPED, INVITATION_SENT, BootstrapError, SuperAdminBootstrapService
from tests.conftest import INSTITUTION
from tests.fakes import FakeStore, FakeUnitOfWork


def service(store, cognito) -> SuperAdminBootstrapService:
    return SuperAdminBootstrapService(FakeUnitOfWork(store), cognito)


async def test_bootstrap_links_verified_identity_and_assigns_super_admin(store, cognito) -> None:
    identity = cognito.add_user("Admin@SLIIT.example", name="First Admin")

    result = await service(store, cognito).bootstrap(email="admin@sliit.example", institution_id=INSTITUTION, operator="ops-laptop\\dumindu")

    assert result.status == BOOTSTRAPPED
    user = store.users[result.user_id]
    assert (user.cognito_sub, user.email, user.full_name) == (identity.sub, "admin@sliit.example", "First Admin")
    assert store.active_role_codes(user.id, INSTITUTION) == {"SUPER_ADMIN"}
    entry = next(e for e in store.audit if e.action == "superadmin.bootstrapped")
    assert entry.actor_type == ACTOR_OPERATOR and entry.target_user_id == user.id
    assert entry.safe_metadata["operator"] == "ops-laptop\\dumindu"
    assert store.locks == 1


async def test_rerunning_for_the_same_person_is_a_safe_no_op(store, cognito) -> None:
    cognito.add_user("admin@sliit.example")
    first = await service(store, cognito).bootstrap(email="admin@sliit.example", institution_id=INSTITUTION, operator="op")
    commits = store.commits

    again = await service(store, cognito).bootstrap(email="admin@sliit.example", institution_id=INSTITUTION, operator="op")

    assert again.status == ALREADY_BOOTSTRAPPED and again.user_id == first.user_id
    assert store.commits == commits
    assert len(store.users) == 1 and len(store.user_roles) == 1


async def test_duplicate_bootstrap_for_another_person_is_refused(store, cognito) -> None:
    cognito.add_user("admin@sliit.example")
    cognito.add_user("intruder@sliit.example")
    await service(store, cognito).bootstrap(email="admin@sliit.example", institution_id=INSTITUTION, operator="op")

    with pytest.raises(BootstrapError) as info:
        await service(store, cognito).bootstrap(email="intruder@sliit.example", institution_id=INSTITUTION, operator="op")
    assert info.value.code == "ALREADY_BOOTSTRAPPED"
    assert len(store.users) == 1


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"status": STATUS_FORCE_CHANGE_PASSWORD}, "IDENTITY_NOT_CONFIRMED"),
        ({"email_verified": False}, "EMAIL_NOT_VERIFIED"),
        ({"enabled": False}, "IDENTITY_DISABLED"),
    ],
)
async def test_unverified_or_unusable_identities_are_refused(store, cognito, kwargs, code) -> None:
    cognito.add_user("admin@sliit.example", **kwargs)
    with pytest.raises(BootstrapError) as info:
        await service(store, cognito).bootstrap(email="admin@sliit.example", institution_id=INSTITUTION, operator="op")
    assert info.value.code == code
    assert not store.users and store.commits == 1  # only the seeding commit from the fixture


async def test_arbitrary_email_without_cognito_identity_is_refused(store, cognito) -> None:
    with pytest.raises(BootstrapError) as info:
        await service(store, cognito).bootstrap(email="nobody@sliit.example", institution_id=INSTITUTION, operator="op")
    assert info.value.code == "IDENTITY_NOT_FOUND"
    assert cognito.count("create_user") == 0


async def test_provision_flag_sends_invitation_but_does_not_grant_access_yet(store, cognito) -> None:
    result = await service(store, cognito).bootstrap(email="admin@sliit.example", institution_id=INSTITUTION, operator="op",
                                                     provision=True, full_name="First Admin")
    assert result.status == INVITATION_SENT
    assert cognito.count("create_user") == 1
    assert not store.users and not store.user_roles

    # Before first sign-in the identity is FORCE_CHANGE_PASSWORD, so a re-run is still refused.
    with pytest.raises(BootstrapError) as info:
        await service(store, cognito).bootstrap(email="admin@sliit.example", institution_id=INSTITUTION, operator="op")
    assert info.value.code == "IDENTITY_NOT_CONFIRMED"


async def test_bootstrap_requires_seeded_roles(cognito) -> None:
    cognito.add_user("admin@sliit.example")
    with pytest.raises(BootstrapError) as info:
        await SuperAdminBootstrapService(FakeUnitOfWork(FakeStore()), cognito).bootstrap(
            email="admin@sliit.example", institution_id=INSTITUTION, operator="op")
    assert info.value.code == "ROLES_NOT_SEEDED"

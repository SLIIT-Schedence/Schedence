"""Operator-only System Admin bootstrap (mocked Cognito + repositories)."""

from __future__ import annotations

import pytest

from app.integrations.cognito import STATUS_FORCE_CHANGE_PASSWORD
from app.models import ACTOR_OPERATOR
from app.services.bootstrap import ALREADY_BOOTSTRAPPED, BOOTSTRAPPED, INVITATION_SENT, BootstrapError, SystemAdminBootstrapService
from tests.fakes import FakeStore, FakeUnitOfWork


def service(store, cognito) -> SystemAdminBootstrapService:
    return SystemAdminBootstrapService(FakeUnitOfWork(store), cognito)


async def test_bootstrap_links_verified_identity_and_grants_platform_role_only(store, cognito) -> None:
    identity = cognito.add_user("Owner@Schedence.example", name="Product Owner")

    result = await service(store, cognito).bootstrap(email="owner@schedence.example", operator="ops-laptop\\dumindu")

    assert result.status == BOOTSTRAPPED
    user = store.users[result.user_id]
    assert (user.cognito_sub, user.email, user.full_name) == (identity.sub, "owner@schedence.example", "Product Owner")
    assert store.platform_role_codes(user.id) == {"SYSTEM_ADMIN"}
    assert store.active_role_codes(user.id) == set()  # no role inside any institution
    entry = next(e for e in store.audit if e.action == "platform.system_admin_bootstrapped")
    assert entry.actor_type == ACTOR_OPERATOR and entry.target_user_id == user.id and entry.institution_id is None
    assert entry.safe_metadata["operator"] == "ops-laptop\\dumindu"
    assert store.locks == 1


async def test_rerunning_for_the_same_person_is_a_safe_no_op(store, cognito) -> None:
    cognito.add_user("owner@schedence.example")
    first = await service(store, cognito).bootstrap(email="owner@schedence.example", operator="op")
    commits = store.commits

    again = await service(store, cognito).bootstrap(email="owner@schedence.example", operator="op")

    assert again.status == ALREADY_BOOTSTRAPPED and again.user_id == first.user_id
    assert store.commits == commits
    assert len(store.users) == 1 and len(store.platform_assignments) == 1


async def test_each_product_owner_can_be_bootstrapped(store, cognito) -> None:
    cognito.add_user("owner1@schedence.example")
    cognito.add_user("owner2@schedence.example")
    first = await service(store, cognito).bootstrap(email="owner1@schedence.example", operator="op")
    second = await service(store, cognito).bootstrap(email="owner2@schedence.example", operator="op")

    assert first.status == second.status == BOOTSTRAPPED
    assert {store.users[r.user_id].email for r in (first, second)} == {"owner1@schedence.example", "owner2@schedence.example"}


async def test_account_with_institution_roles_is_refused(store, cognito, make_account) -> None:
    """Platform and institution access never share an account."""
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")

    with pytest.raises(BootstrapError) as info:
        await service(store, cognito).bootstrap(email="admin@sliit.example", operator="op")

    assert info.value.code == "INSTITUTION_ACCOUNT"
    assert store.platform_role_codes(admin.user.id) == set()


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"status": STATUS_FORCE_CHANGE_PASSWORD}, "IDENTITY_NOT_CONFIRMED"),
        ({"email_verified": False}, "EMAIL_NOT_VERIFIED"),
        ({"enabled": False}, "IDENTITY_DISABLED"),
    ],
)
async def test_unverified_or_unusable_identities_are_refused(store, cognito, kwargs, code) -> None:
    cognito.add_user("owner@schedence.example", **kwargs)
    commits = store.commits
    with pytest.raises(BootstrapError) as info:
        await service(store, cognito).bootstrap(email="owner@schedence.example", operator="op")
    assert info.value.code == code
    assert not store.users and store.commits == commits


async def test_arbitrary_email_without_cognito_identity_is_refused(store, cognito) -> None:
    with pytest.raises(BootstrapError) as info:
        await service(store, cognito).bootstrap(email="nobody@schedence.example", operator="op")
    assert info.value.code == "IDENTITY_NOT_FOUND"
    assert cognito.count("create_user") == 0


async def test_provision_flag_sends_invitation_but_does_not_grant_access_yet(store, cognito) -> None:
    result = await service(store, cognito).bootstrap(email="owner@schedence.example", operator="op", provision=True,
                                                     full_name="Product Owner")
    assert result.status == INVITATION_SENT
    assert cognito.count("create_user") == 1
    assert not store.users and not store.platform_assignments
    assert "platform.system_admin_invited" in store.actions()

    # Before first sign-in the identity is FORCE_CHANGE_PASSWORD, so a re-run is still refused.
    with pytest.raises(BootstrapError) as info:
        await service(store, cognito).bootstrap(email="owner@schedence.example", operator="op")
    assert info.value.code == "IDENTITY_NOT_CONFIRMED"


async def test_bootstrap_requires_seeded_roles(cognito) -> None:
    cognito.add_user("owner@schedence.example")
    with pytest.raises(BootstrapError) as info:
        await SystemAdminBootstrapService(FakeUnitOfWork(FakeStore()), cognito).bootstrap(email="owner@schedence.example", operator="op")
    assert info.value.code == "ROLES_NOT_SEEDED"

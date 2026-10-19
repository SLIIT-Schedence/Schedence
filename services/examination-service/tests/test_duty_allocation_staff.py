"""Eligibility policy configuration and import of Academic Data Service staff records.

Rank names below are test fixtures, not SLIIT policy.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.duty_allocation import DutyRole, EligibilityPolicy, RoleRule, StaffRestriction, import_staff, page_items
from app.duty_allocation.staff_source import STAFF_RECORD
from app.scheduling.contracts import SchedulingValidationError

RANKS = ["Rank One", "Rank Two", "Rank Three"]  # most senior first


def sid(n: int) -> str:
    return str(uuid.UUID(int=n))


def policy(supervisor_min="Rank Two", invigilator_min=None, supervisor_unranked=False, invigilator_unranked=True,
           restrictions=()) -> EligibilityPolicy:
    return EligibilityPolicy(tuple(RANKS), (RoleRule(DutyRole.SUPERVISOR, supervisor_min, supervisor_unranked),
                                            RoleRule(DutyRole.INVIGILATOR, invigilator_min, invigilator_unranked)),
                             0, "test config", tuple(restrictions))


def record(n: int, rank="Rank One", status="ACTIVE", **extra) -> dict:
    # Same fields as academic-data StaffOut; extra fields are ignored like the real response.
    return {"id": sid(n), "institution_id": sid(999), "staff_number": f"EMP{n:03}", "full_name": f"Staff {n}",
            "email": None, "academic_rank": rank, "employment_status": status, "identity_user_id": None,
            "created_at": "2026-10-01T00:00:00Z"} | extra


# --- Rank rules -----------------------------------------------------------------------------

def test_rank_decisions_follow_configured_order_case_insensitively():
    p = policy()
    assert p.seniority("rank one") == 0 and p.seniority(" Rank  Three ") == 2 and p.seniority("Other") is None
    assert p.roles_for_rank("Rank One")[0] == (DutyRole.SUPERVISOR, DutyRole.INVIGILATOR)
    assert p.roles_for_rank("RANK TWO")[0] == (DutyRole.SUPERVISOR, DutyRole.INVIGILATOR)
    roles, reasons = p.roles_for_rank("Rank Three")
    assert roles == (DutyRole.INVIGILATOR,) and "below the SUPERVISOR minimum" in reasons[0]


def test_unranked_and_unknown_ranks_are_not_guessed():
    p = policy()
    roles, reasons = p.roles_for_rank(None)
    assert roles == (DutyRole.INVIGILATOR,) and "no academic rank" in reasons[0]
    roles, reasons = p.roles_for_rank("Visiting Something")
    assert roles == (DutyRole.INVIGILATOR,) and "not in the configured rank order" in reasons[0]
    assert policy(invigilator_unranked=False).roles_for_rank(None) == ((), (
        "no academic rank; SUPERVISOR requires one", "no academic rank; INVIGILATOR requires one"))


def test_no_minimum_rank_means_rank_is_not_checked():
    assert policy(supervisor_min=None).roles_for_rank("Visiting Something")[0] == tuple(DutyRole)


@pytest.mark.parametrize("build", [
    lambda: EligibilityPolicy(("A", "a"), policy().role_rules, 0, "t"),
    lambda: EligibilityPolicy(("A", " "), policy().role_rules, 0, "t"),
    lambda: EligibilityPolicy(tuple(RANKS), (RoleRule(DutyRole.SUPERVISOR, None, True),), 0, "t"),
    lambda: EligibilityPolicy(tuple(RANKS), (RoleRule(DutyRole.SUPERVISOR, None, True),) * 2, 0, "t"),
    lambda: policy(supervisor_min="Not A Rank"),
    lambda: EligibilityPolicy(tuple(RANKS), policy().role_rules, -1, "t"),
    lambda: EligibilityPolicy(tuple(RANKS), policy().role_rules, 0, ""),
    lambda: RoleRule(DutyRole.SUPERVISOR, None, "yes"),
    lambda: StaffRestriction("EMP1", "reason"),
    lambda: StaffRestriction(sid(1), ""),
])
def test_invalid_policy_is_rejected(build):
    with pytest.raises(SchedulingValidationError):
        build()


def test_policy_from_config():
    config = json.loads(json.dumps({
        "source": "institution config v1", "rank_order": RANKS, "minimum_gap_minutes": 30,
        "roles": {"SUPERVISOR": {"minimum_rank": "Rank Two", "allow_unranked": False},
                  "INVIGILATOR": {"minimum_rank": None, "allow_unranked": True}},
        "restrictions": [{"staff_id": sid(1), "reason": "module lecturer", "module_code": "IT1010"}],
    }))
    p = EligibilityPolicy.from_mapping(config)
    assert p.minimum_gap_minutes == 30 and p.source == "institution config v1"
    assert p.rule(DutyRole.SUPERVISOR).minimum_rank == "Rank Two"
    assert p.restriction_for(sid(1), DutyRole.INVIGILATOR, "X1", "IT1010").reason == "module lecturer"
    assert p.restriction_for(sid(1), DutyRole.INVIGILATOR, "X1", "IT2020") is None


@pytest.mark.parametrize("change", [
    lambda c: c.pop("roles"),
    lambda c: c.update(extra=1),
    lambda c: c["roles"].pop("INVIGILATOR"),
    lambda c: c["roles"]["SUPERVISOR"].pop("allow_unranked"),
    lambda c: c.update(rank_order="Rank One"),
    lambda c: c.update(restrictions=[{"staff_id": sid(1)}]),
    lambda c: c.update(restrictions=[{"staff_id": sid(1), "reason": "r", "role": "DEAN"}]),
])
def test_invalid_config_is_rejected(change):
    config = {"source": "t", "rank_order": list(RANKS), "minimum_gap_minutes": 0,
              "roles": {"SUPERVISOR": {"minimum_rank": None, "allow_unranked": True},
                        "INVIGILATOR": {"minimum_rank": None, "allow_unranked": True}}}
    change(config)
    with pytest.raises(SchedulingValidationError):
        EligibilityPolicy.from_mapping(config)


def test_restriction_matching_scope():
    r = StaffRestriction(sid(1), "only supervising is blocked", role=DutyRole.SUPERVISOR)
    assert r.applies(sid(1), DutyRole.SUPERVISOR, "X1", "M1") and r.applies(sid(1), DutyRole.SUPERVISOR, "X2", "M2")
    assert not r.applies(sid(1), DutyRole.INVIGILATOR, "X1", "M1") and not r.applies(sid(2), DutyRole.SUPERVISOR, "X1", "M1")
    assert StaffRestriction(sid(1), "one session", session_id="X1").applies(sid(1), DutyRole.INVIGILATOR, "X1", "M1")


# --- Importing staff records ----------------------------------------------------------------

def test_import_maps_staff_out_records_and_assigns_roles_from_rank():
    result = import_staff([record(1, "Rank One"), record(2, "Rank Three"), record(3, None)], policy())
    assert result.is_clean and not result.excluded
    by_id = {s.staff_id: s for s in result.staff}
    assert by_id[sid(1)].staff_number == "EMP001" and by_id[sid(1)].full_name == "Staff 1"
    assert by_id[sid(1)].eligible_roles == (DutyRole.SUPERVISOR, DutyRole.INVIGILATOR)
    assert by_id[sid(2)].eligible_roles == (DutyRole.INVIGILATOR,)
    assert by_id[sid(3)].academic_rank is None and by_id[sid(3)].eligible_roles == (DutyRole.INVIGILATOR,)


def test_import_accepts_uuid_objects_and_page_responses():
    page = {"items": [record(1, id=uuid.UUID(int=1))], "total": 1, "page": 1, "page_size": 50}
    assert import_staff(page_items(page), policy()).staff[0].staff_id == sid(1)
    with pytest.raises(SchedulingValidationError):
        page_items({"results": []})


def test_inactive_staff_are_excluded_with_reason():
    result = import_staff([record(1), record(2, status="INACTIVE")], policy())
    assert [s.staff_id for s in result.staff] == [sid(1)]
    assert result.excluded[0].staff_id == sid(2) and result.excluded[0].staff_number == "EMP002"
    assert "INACTIVE" in result.excluded[0].reason


def test_staff_with_no_eligible_role_are_excluded_with_reasons():
    result = import_staff([record(1, None)], policy(invigilator_unranked=False))
    assert result.staff == () and "SUPERVISOR requires one" in result.excluded[0].reason
    blank = import_staff([record(2, "   ")], policy())
    assert blank.staff[0].academic_rank is None


@pytest.mark.parametrize("bad", [
    "not a mapping",
    {"id": sid(1), "staff_number": "E", "full_name": "N"},  # employment_status missing
    record(1, id="EMP001"),
    record(1, status="ON_LEAVE"),
    record(1, rank=5),
    record(1, full_name=""),
])
def test_unreadable_records_are_reported_not_guessed(bad):
    result = import_staff([bad, record(7)], policy())
    assert [v.constraint for v in result.violations] == [STAFF_RECORD]
    assert [s.staff_id for s in result.staff] == [sid(7)]


def test_duplicate_staff_ids_are_reported():
    result = import_staff([record(1), record(1, staff_number="EMP999")], policy())
    assert len(result.staff) == 1 and "repeats staff" in result.violations[0].message


def test_import_requires_policy():
    with pytest.raises(SchedulingValidationError):
        import_staff([record(1)], {"rank_order": RANKS})

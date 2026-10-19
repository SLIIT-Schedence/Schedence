"""Independent roster validator (D1-D10) and duty limit policy. No OR-Tools needed."""

from __future__ import annotations

import pytest

from app.duty_allocation import (
    DutyAssignment, DutyLimitPolicy, DutyRole, StaffAvailability, StaffRestriction, StaffUnavailability,
    validate_allocation,
)
from app.scheduling.contracts import SchedulingValidationError
from duty_allocation_support import (
    INV, at, limits, make_input, member, open_policy, session, sid, team,
)

S, I = DutyRole.SUPERVISOR, DutyRole.INVIGILATOR


def duty(session_id, venue, n, role) -> DutyAssignment:
    return DutyAssignment(session_id, venue, sid(n), role)


def codes(violations) -> list[str]:
    return sorted({v.constraint for v in violations})


ONE_ROOM = [session("X1", at(8, 30), at(10, 30), "R1")]
GOOD = (duty("X1", "R1", 1, S), duty("X1", "R1", 2, I))


def check(sessions, staff, roster, policy=None, limit=None, **kwargs):
    return validate_allocation(make_input(sessions, staff, **kwargs), policy or open_policy(), limit or limits(), tuple(roster))


def test_valid_roster_passes():
    assert check(ONE_ROOM, team(2), GOOD) == ()


@pytest.mark.parametrize("roster", [GOOD[:1], GOOD + (duty("X1", "R1", 3, I),)])
def test_d1_positions_must_be_filled_exactly(roster):
    assert codes(check(ONE_ROOM, team(3), roster)) == ["D1"]


def test_d2_overlapping_sessions():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(10), at(12), "R2")]
    roster = GOOD + (duty("X2", "R2", 1, S), duty("X2", "R2", 3, I))
    assert codes(check(sessions, team(3), roster)) == ["D2"]


def test_d2_minimum_break_between_duties():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(10, 45), at(12), "R2")]
    roster = GOOD + (duty("X2", "R2", 1, S), duty("X2", "R2", 3, I))
    assert check(sessions, team(3), roster, open_policy(gap=15)) == ()
    assert codes(check(sessions, team(3), roster, open_policy(gap=16))) == ["D2"]


def test_d3_two_roles_or_two_rooms_in_one_session():
    assert codes(check(ONE_ROOM, team(1), (duty("X1", "R1", 1, S), duty("X1", "R1", 1, I)))) == ["D3"]
    two_rooms = [session("X1", at(8, 30), at(10, 30), "R1", "R2")]
    roster = GOOD + (duty("X1", "R2", 1, S), duty("X1", "R2", 3, I))
    assert codes(check(two_rooms, team(3), roster)) == ["D3"]


def test_d4_outside_declared_availability():
    window = StaffAvailability(sid(1), at(13), at(17))
    assert codes(check(ONE_ROOM, team(2), GOOD, availability=[window])) == ["D4"]


def test_d5_during_unavailability():
    off = StaffUnavailability(sid(2), at(9), at(9, 30), "appointment")
    assert codes(check(ONE_ROOM, team(2), GOOD, unavailability=[off])) == ["D5"]


def test_d6_role_and_rank_eligibility():
    assert codes(check(ONE_ROOM, [member(1, INV), member(2)], GOOD)) == ["D6"]
    ranked = [member(1, rank="Rank B"), member(2, rank="Rank B")]
    policy = open_policy(rank_order=("Rank A", "Rank B"), supervisor_min="Rank A")
    assert codes(check(ONE_ROOM, ranked, GOOD, policy)) == ["D6"]


def test_d7_configured_restriction():
    policy = open_policy(restrictions=[StaffRestriction(sid(2), "module lecturer", module_code="IT1010")])
    assert codes(check(ONE_ROOM, team(2), GOOD, policy)) == ["D7"]


def test_d8_and_d9_duty_limits():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(13), at(15), "R2"),
                session("X3", at(9, day=1), at(11, day=1), "R3")]
    roster = GOOD + (duty("X2", "R2", 1, S), duty("X2", "R2", 2, I), duty("X3", "R3", 1, S), duty("X3", "R3", 2, I))
    assert check(sessions, team(2), roster, limit=limits(per_day=2, per_period=3)) == ()
    assert codes(check(sessions, team(2), roster, limit=limits(per_day=1))) == ["D8"]
    assert codes(check(sessions, team(2), roster, limit=limits(per_period=2))) == ["D9"]


def test_d10_unknown_rooms_staff_and_duplicates():
    assert "D10" in codes(check(ONE_ROOM, team(2), GOOD + (duty("X9", "R1", 1, S),)))
    assert "D10" in codes(check(ONE_ROOM, team(2), (duty("X1", "R1", 7, S), duty("X1", "R1", 2, I))))
    assert "D10" in codes(check(ONE_ROOM, team(2), GOOD + (GOOD[0],)))
    assert "D10" in codes(check(ONE_ROOM, team(2), GOOD + ("not an assignment",)))


@pytest.mark.parametrize("args", [(0, None, "t"), (None, -1, "t"), (1.5, None, "t"), (None, None, "")])
def test_invalid_limit_policy_is_rejected(args):
    with pytest.raises(SchedulingValidationError):
        DutyLimitPolicy(*args)


def test_limit_policy_from_config():
    policy = DutyLimitPolicy.from_mapping({"source": "cfg", "max_duties_per_day": 2, "max_duties_per_period": None})
    assert (policy.max_duties_per_day, policy.max_duties_per_period) == (2, None)
    with pytest.raises(SchedulingValidationError):
        DutyLimitPolicy.from_mapping({"source": "cfg", "max_duties_per_day": 2})

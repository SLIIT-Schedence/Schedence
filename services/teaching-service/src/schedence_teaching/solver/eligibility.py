"""Offering candidate pools derived only from validated teaching-team memberships."""

from schedence_teaching.schemas import TeachingTimetableInput


def candidate_pools(inputs: TeachingTimetableInput) -> dict[str, tuple[str, ...]]:
    """Return stable unique choices without changing source memberships or roles.

    References and academic-only LIC/Co-LIC roles are checked by the input
    contracts. No session-type, rank, department or availability policy is added.
    Multiple memberships for one person remain source records, not extra choices.
    """
    pools = {offering.offering_id: set() for offering in inputs.module_offerings}
    for member in inputs.teaching_team_members:
        pools[member.offering_id].add(member.staff_id)
    return {offering_id: tuple(sorted(staff_ids)) for offering_id, staff_ids in pools.items()}

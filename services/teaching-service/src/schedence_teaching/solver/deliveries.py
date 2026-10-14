"""Validated alternatives and explicit operational bounds, independent of CP-SAT."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import Field, ValidationError

from schedence_teaching.preparation import PreparedGroupCombinations, PreparationError, UnscheduledSession
from schedence_teaching.preparation.combinations import prepare_group_combinations
from schedence_teaching.preparation.combination_population import population_proofs
from schedence_teaching.schemas import PopulationRelationship
from schedence_teaching.schemas.models import InputContract

from .eligibility import candidate_pools
from .results import SolverInputError


class DeliveryObjective(StrEnum):
    DELIVERIES_THEN_MINUTES = "DELIVERIES_THEN_MINUTES"
    DELIVERIES = "DELIVERIES"
    CONTACT_MINUTES = "CONTACT_MINUTES"
    FEASIBILITY = "FEASIBILITY"


Limit = Annotated[int, Field(strict=True, gt=0)]


class CombinationSolverConfig(InputContract):
    """Operational limits are not academic policy and never truncate candidates."""

    objective: DeliveryObjective = DeliveryObjective.DELIVERIES_THEN_MINUTES
    max_candidates: Limit = 1_000
    max_delivery_alternatives: Limit = 10_000
    max_resource_choices: Limit = 100_000
    max_model_variables: Limit = 200_000
    max_model_constraints: Limit = 200_000


@dataclass(frozen=True)
class DeliveryAlternative:
    delivery_id: str
    covered_sessions: tuple[UnscheduledSession, ...]
    student_count: int
    eligible_staff_ids: tuple[str, ...]

    @property
    def is_combined(self):
        return len(self.covered_sessions) > 1

    @property
    def group_ids(self):
        return tuple(session.group_id for session in self.covered_sessions)

    @property
    def duration_minutes(self):
        return self.covered_sessions[0].duration_minutes


def checked_combinations(prepared, combinations, config):
    if not isinstance(combinations, PreparedGroupCombinations):
        raise SolverInputError("Combination input must be prepared with prepare_group_combinations().")
    if config is None:
        config = CombinationSolverConfig()
    if not isinstance(config, CombinationSolverConfig):
        raise SolverInputError("Combination config must be CombinationSolverConfig.")
    try:
        config = CombinationSolverConfig.model_validate(config.model_dump(round_trip=True))
        if len(combinations.candidates) > config.max_candidates:
            raise SolverInputError("Combination candidate limit exceeded; increase the explicit limit or narrow input policy.")
        canonical = prepare_group_combinations(combinations.input_snapshot, max_candidates=config.max_candidates)
    except (PreparationError, ValidationError) as error:
        raise SolverInputError(str(error)) from None
    if (canonical.candidates != combinations.candidates
            or canonical.prepared.sessions != combinations.prepared.sessions
            or canonical.prepared.input_snapshot != combinations.prepared.input_snapshot
            or canonical.prepared.input_snapshot != prepared.input_snapshot):
        raise SolverInputError("Stale or incompatible combination preparation; regenerate from the same teaching input.")
    count = len(prepared.sessions) + sum(len(row.occurrences) for row in canonical.candidates)
    if count > config.max_delivery_alternatives:
        raise SolverInputError("Delivery alternative limit exceeded; no alternatives were dropped.")
    return canonical, config


def delivery_alternatives(prepared, combinations=None, *, pools=None):
    groups = {row.group_id: row for row in prepared.input_snapshot.student_groups}
    pools = candidate_pools(prepared.input_snapshot) if pools is None else pools
    separate = tuple(DeliveryAlternative(row.occurrence_id, (row,), groups[row.group_id].student_count,
                                         pools[row.offering_id]) for row in prepared.sessions)
    combined = tuple(DeliveryAlternative(row.occurrence_id, row.covered_sessions, row.combined_student_count,
                                         row.eligible_staff_ids)
                     for candidate in combinations.candidates for row in candidate.occurrences) if combinations else ()
    return separate + combined


def known_overlap_pairs(combinations):
    """Only asserted/roster-proven overlap; propagate attendance through ancestors."""
    if combinations is None:
        return set()
    proofs = population_proofs(combinations.input_snapshot, combinations.prepared.hierarchy)
    return {pair for pair, proof in proofs.items() if proof.relationship is PopulationRelationship.OVERLAPPING}


def attendance_conflicts(hierarchy, first, second, overlaps):
    if hierarchy.conflicts(first, second):
        return True
    # An ancestor session contains every child's students. Child subsets of two
    # overlapping groups are NOT necessarily overlapping, so never propagate down.
    def contains(ancestor, child):
        while child is not None:
            if child == ancestor:
                return True
            child = hierarchy.parents[child]
        return False

    return any((contains(first, left) and contains(second, right))
               or (contains(first, right) and contains(second, left))
               for left, right in overlaps)


def objective_cost(mode, delivery_count, minutes, original_minutes):
    """Lexicographic priority, not two additive rewards for the same saving."""
    if mode is DeliveryObjective.DELIVERIES_THEN_MINUTES:
        return delivery_count * (original_minutes + 1) + minutes
    if mode is DeliveryObjective.DELIVERIES:
        return delivery_count
    if mode is DeliveryObjective.CONTACT_MINUTES:
        return minutes
    return None

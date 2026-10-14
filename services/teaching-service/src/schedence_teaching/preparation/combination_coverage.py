"""Exactly-once fulfillment for hypothetical combined/separate delivery choices."""

from collections import Counter

from pydantic import ValidationError

from schedence_teaching.schemas import DeliverySelection

from .combination_results import DeliveryCoverageSummary, PreparedGroupCombinations
from .combinations import prepare_group_combinations
from .errors import PreparationError, PreparationIssue


def validate_delivery_selection(
    prepared: PreparedGroupCombinations, selection: DeliverySelection,
) -> DeliveryCoverageSummary:
    """Check coverage and count each shared teaching delivery once, without scheduling."""
    if not isinstance(prepared, PreparedGroupCombinations) or not isinstance(selection, DeliverySelection):
        raise PreparationError([PreparationIssue("invalid_input", (), "Coverage requires PreparedGroupCombinations and DeliverySelection.")])
    canonical = prepare_group_combinations(prepared.input_snapshot, max_candidates=prepared.max_candidates)
    if (canonical.candidates != prepared.candidates or canonical.prepared.sessions != prepared.prepared.sessions
            or canonical.prepared.input_snapshot != prepared.prepared.input_snapshot):
        raise PreparationError([PreparationIssue("stale_combination_preparation", (),
                                                "Prepared alternatives no longer match validated inputs; prepare them again.")])
    try:
        selection = DeliverySelection.model_validate(selection.model_dump(round_trip=True))
    except ValidationError as error:
        raise PreparationError([PreparationIssue(detail["type"], tuple(detail["loc"]), detail["msg"])
                                for detail in error.errors(include_input=False, include_context=False, include_url=False)]) from None
    separate = {session.occurrence_id: session for session in canonical.separate_sessions}
    combined = {occurrence.occurrence_id: occurrence for candidate in canonical.candidates for occurrence in candidate.occurrences}
    counts = Counter()
    issues, minutes = [], 0
    for index, identity in enumerate(selection.separate_occurrence_ids):
        if identity not in separate:
            issues.append(PreparationIssue("unknown_separate_occurrence", ("separate_occurrence_ids", index),
                                           "Selected separate occurrence is not original prepared demand."))
        else:
            counts[identity] += 1
            minutes += separate[identity].duration_minutes
    for index, identity in enumerate(selection.combined_occurrence_ids):
        if identity not in combined:
            issues.append(PreparationIssue("unknown_combined_occurrence", ("combined_occurrence_ids", index),
                                           "Selected combined occurrence is not a generated eligible alternative."))
        else:
            occurrence = combined[identity]
            counts.update(session.occurrence_id for session in occurrence.covered_sessions)
            minutes += occurrence.duration_minutes  # One delivery, not one booking per covered group.
    for identity in separate:
        if counts[identity] != 1:
            code = "missing_fulfillment" if counts[identity] == 0 else "duplicate_fulfillment"
            issues.append(PreparationIssue(code, ("coverage", identity), "Every original occurrence must be fulfilled exactly once."))
    if issues:
        raise PreparationError(issues)
    combined_count, separate_count = len(selection.combined_occurrence_ids), len(selection.separate_occurrence_ids)
    return DeliveryCoverageSummary(len(separate), combined_count, separate_count,
                                   combined_count + separate_count, minutes)

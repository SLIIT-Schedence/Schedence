"""Date/stream-aware teaching sequences over existing physical delivery records."""

from collections import defaultdict

from schedence_teaching.schemas import SessionType
from schedence_teaching.solver.results import ScheduledDelivery, ScheduledSession

from .contracts import ConsecutiveCriterion
from .results import DayStreamSequence, SessionGap, TeachingRun


def delivery_id(row):
    return row.delivery_id if isinstance(row, ScheduledDelivery) else row.session.occurrence_id


def delivery_stream(row):
    return row.covered_sessions[0].stream if isinstance(row, ScheduledDelivery) else row.session.stream


def _is_lecture(row):
    return (row.covered_sessions[0] if isinstance(row, ScheduledDelivery) else row.session).session_type is SessionType.LECTURE


def missing_scoring_configuration(policy):
    missing = []
    if ConsecutiveCriterion.BACK_TO_BACK in policy.soft_criteria and policy.back_to_back_penalty is None:
        missing.append("missing_back_to_back_penalty")
    if ConsecutiveCriterion.SHORT_BREAK in policy.soft_criteria:
        if policy.preferred_minimum_break_minutes is None:
            missing.append("missing_preferred_minimum_break_minutes")
        if policy.short_break_minute_penalty is None:
            missing.append("missing_short_break_minute_penalty")
    if ConsecutiveCriterion.EXCESS_CONTINUOUS_MINUTES in policy.soft_criteria:
        if policy.maximum_preferred_continuous_minutes is None:
            missing.append("missing_maximum_preferred_continuous_minutes")
        if policy.excess_continuous_minute_penalty is None:
            missing.append("missing_excess_continuous_minute_penalty")
    return tuple(missing)


def policy_applies(policy, on_date):
    return ((policy.effective_from is None or policy.effective_from <= on_date)
        and (policy.effective_until is None or on_date <= policy.effective_until))


def scoring_requested(policy):
    return bool(policy.soft_criteria) or policy.idle_gap_minute_penalty is not None


def detect_consecutive_sequences(deliveries, calendar, policy, *, apply_confirmed_hard_break=False):
    """Return per-date/stream sequences and the number of unscored stream changes.

    Callers must first validate original timetable coverage and all hard rules.
    These local guards additionally reject duplicate bookings and overlaps, not
    score them as consecutive teaching. No new time/delivery model is created.
    """
    by_day, seen = defaultdict(list), set()
    for row in deliveries:
        if not isinstance(row, (ScheduledSession, ScheduledDelivery)):
            raise ValueError("sequence detection requires existing scheduled delivery/session records")
        identity = delivery_id(row)
        if identity in seen:
            raise ValueError("duplicate physical delivery in one attendee timeline")
        seen.add(identity)
        if (row.day not in calendar.day_dates or type(row.start_minute) is not int or type(row.end_minute) is not int
                or not 0 <= row.start_minute < row.end_minute <= 1440):
            raise ValueError("sequence detection requires mapped dates and valid integer time intervals")
        by_day[row.day].append(row)
    segments, transitions = defaultdict(list), 0
    for day, rows in by_day.items():
        ordered = sorted(rows, key=lambda row: (row.start_minute, row.end_minute, delivery_id(row)))
        for first, second in zip(ordered, ordered[1:]):
            if first.end_minute > second.start_minute:
                raise ValueError("overlapping teaching is invalid; it is not a back-to-back pair")
        previous_stream = None
        for row in ordered:
            stream = delivery_stream(row)
            if stream != previous_stream:
                transitions += int(previous_stream is not None)
                segments[(day, stream)].append([])
            segments[(day, stream)][-1].append(row)
            previous_stream = stream
    sequences = []
    for (day, stream), parts in sorted(segments.items(), key=lambda item: (calendar.day_dates[item[0][0]], item[0][1])):
        gaps, runs, records = [], [], []
        for part in parts:
            records.extend(part)
            current = []
            for row in part:
                if current:
                    previous = current[-1]
                    gap = row.start_minute - previous.end_minute
                    gaps.append(SessionGap(delivery_id(previous), delivery_id(row), previous.end_minute,
                        row.start_minute, gap, _is_lecture(previous) and _is_lecture(row)))
                    if gap > 0:
                        runs.append(_run(current))
                        current = []
                current.append(row)
            if current:
                runs.append(_run(current))
        preferred = policy.preferred_minimum_break_minutes
        maximum = policy.maximum_preferred_continuous_minutes
        applicable = policy_applies(policy, calendar.day_dates[day])
        hard = policy.confirmed_hard_minimum_break if apply_confirmed_hard_break and applicable else None
        sequences.append(DayStreamSequence(day, calendar.day_dates[day], stream,
            tuple(delivery_id(row) for row in records), sum(row.end_minute - row.start_minute for row in records),
            tuple(gaps), tuple(runs), sum(row.gap_minutes == 0 for row in gaps),
            sum(row.gap_minutes == 0 and row.is_lecture_pair for row in gaps),
            max((row.session_count for row in runs), default=0), max((row.teaching_minutes for row in runs), default=0),
            sum(max(preferred - row.gap_minutes, 0) for row in gaps) if preferred is not None else None,
            sum(max(row.teaching_minutes - maximum, 0) for row in runs) if maximum is not None else None,
            tuple(row for row in gaps if row.gap_minutes < hard.minutes) if hard is not None else (), applicable))
    return tuple(sequences), transitions


def _run(rows):
    return TeachingRun(tuple(delivery_id(row) for row in rows), rows[0].start_minute, rows[-1].end_minute,
                       sum(row.end_minute - row.start_minute for row in rows), len(rows))


def score_sequences(sequences, policy, *, scoring_enabled):
    """Score only explicitly selected criteria; excess is charged once per maximal run."""
    if not scoring_enabled or not scoring_requested(policy):
        return (), 0, None, None
    sequences = tuple(row for row in sequences if row.policy_applicable)
    criteria = {}
    if ConsecutiveCriterion.BACK_TO_BACK in policy.soft_criteria:
        criteria[ConsecutiveCriterion.BACK_TO_BACK.value] = (sum(row.back_to_back_pairs for row in sequences)
            * policy.back_to_back_penalty if policy.back_to_back_penalty is not None else None)
    if ConsecutiveCriterion.SHORT_BREAK in policy.soft_criteria:
        criteria[ConsecutiveCriterion.SHORT_BREAK.value] = (sum(row.preferred_break_shortfall_minutes for row in sequences)
            * policy.short_break_minute_penalty if policy.preferred_minimum_break_minutes is not None
            and policy.short_break_minute_penalty is not None else None)
    if ConsecutiveCriterion.EXCESS_CONTINUOUS_MINUTES in policy.soft_criteria:
        criteria[ConsecutiveCriterion.EXCESS_CONTINUOUS_MINUTES.value] = (sum(row.excess_continuous_minutes for row in sequences)
            * policy.excess_continuous_minute_penalty if policy.maximum_preferred_continuous_minutes is not None
            and policy.excess_continuous_minute_penalty is not None else None)
    if policy.idle_gap_minute_penalty is not None:
        criteria["IDLE_GAP_MINUTES"] = sum(gap.gap_minutes for row in sequences for gap in row.gaps) * policy.idle_gap_minute_penalty
    known = sum(value for value in criteria.values() if value is not None)
    complete = not missing_scoring_configuration(policy)
    return tuple(sorted(criteria.items())), known, known if complete else None, complete

"""How many examination sessions a module needs, from seating capacity and room availability.

No solver imports. For each permitted start of the module, one sitting can seat
at most the effective exam capacities (configurable seating percentage, with
per-venue overrides) of its compatible venues that are free for the whole exam
(duration plus reading time; venue blocks respected), limited to
``max_rooms_per_exam`` rooms. A module gets exactly one session when some start
can seat every candidate, as before. Otherwise it gets the *smallest* number of
sessions at different, non-overlapping starts that could together seat everyone;
never more. Other modules competing for the same rooms are not considered here:
the solver handles that (and reports infeasibility when it cannot).
"""

from __future__ import annotations

from app.scheduling.availability import allowed_starts, merged_blocks
from app.scheduling.types import InputSnapshot
from app.sources.types import Module


def slot_capacities(snapshot: InputSnapshot, module: Module) -> list[tuple[int, int]]:
    """(start minute, seats one sitting could use) for every permitted start, in time order."""
    allowed = snapshot.policy.allowed_venue_types(module.exam_type, module.module_code)
    venues = [v for v in snapshot.capacities.available_venues() if v.venue_type in allowed]
    blocks = merged_blocks(snapshot)
    duration = module.duration_minutes + module.reading_time_minutes
    result = []
    for start in allowed_starts(snapshot, module):
        end = start + duration
        free = sorted((v.exam_capacity for v in venues
                       if not any(b_start < end and start < b_end for b_start, b_end in blocks.get(v.venue_id, ()))),
                      reverse=True)
        if snapshot.policy.max_rooms_per_exam is not None:
            free = free[:snapshot.policy.max_rooms_per_exam]
        result.append((start, sum(free)))
    return result


def sitting_capacity(snapshot: InputSnapshot, module: Module) -> int:
    """Most candidates one sitting could seat, at its best permitted start."""
    return max((seats for _, seats in slot_capacities(snapshot, module)), default=0)


def fewest_sittings(slots: list[tuple[int, int]], duration: int, candidates: int) -> int | None:
    """Fewest non-overlapping slots whose seats add up to ``candidates``; None if no number is enough.

    Exact: best[i][k] is the most seats at most k non-overlapping sittings among
    the first i slots can offer (weighted interval scheduling, slots sorted by start).
    """
    starts = [start for start, _ in slots]
    size = len(slots)
    best = [[0] * (size + 1) for _ in range(size + 1)]
    for i, (start, seats) in enumerate(slots, 1):
        previous = sum(1 for s in starts[:i - 1] if s + duration <= start)  # earlier slots that end in time
        for k in range(1, size + 1):
            best[i][k] = max(best[i - 1][k], best[previous][k - 1] + seats)
    for k in range(1, len(slots) + 1):
        if best[len(slots)][k] >= candidates:
            return k
    return None


def required_sessions(snapshot: InputSnapshot, module: Module) -> int:
    """1 when one sitting is enough, or when no sitting has any usable seat (the solver then reports infeasibility).

    When even every permitted start together is not enough, the lower bound
    ceil(candidates / best sitting) is returned, so the session limit message or
    the solver reports the problem.
    """
    slots = slot_capacities(snapshot, module)
    best = max((seats for _, seats in slots), default=0)
    if best <= 0 or module.candidate_count <= best:
        return 1
    needed = fewest_sittings(slots, module.duration_minutes + module.reading_time_minutes, module.candidate_count)
    return needed if needed is not None else -(-module.candidate_count // best)


def session_counts(snapshot: InputSnapshot) -> dict[str, int]:
    return {m.module_code: required_sessions(snapshot, m) for m in snapshot.modules}

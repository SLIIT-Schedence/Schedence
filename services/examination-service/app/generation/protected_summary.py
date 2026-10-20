"""The cross-cycle summary of a generation: what was protected, what it occupies, and why nothing fits.

Built from the frozen snapshot and the validator's X1 / X2 findings. Counts and venue slots only.
"""

from __future__ import annotations

from app.scheduling.protected import shared_student_count
from app.scheduling.types import InputSnapshot, SolveStatus, Violation

MAX_SLOTS = 100
MAX_ISSUES = 10


def protected_summary(snapshot: InputSnapshot, status: SolveStatus, returned: bool,
                      violations: tuple[Violation, ...]) -> dict | None:
    """``None`` when the loader did not consider other cycles at all (library and command line)."""
    protected = snapshot.protected
    if protected is None:
        return None
    slots = sorted(({"timetable_id": t.timetable_id, "period_code": t.period_code, "module_code": s.module_code,
                     "venue_id": venue, "date": s.start.date(), "start_time": s.start.time(), "end_time": s.end.time()}
                    for t, s in protected.sessions for venue in s.venue_ids),
                   key=lambda x: (x["date"], x["start_time"], x["venue_id"], x["timetable_id"]))
    clashes = [v for v in violations if v.constraint in ("X1", "X2")]
    venue_conflicts = sum(v.constraint == "X1" for v in clashes)
    student_conflicts = sum(v.constraint == "X2" for v in clashes)
    issues = [f"{v.constraint}: {v.message}" for v in clashes[:MAX_ISSUES]]
    if protected.timetables and not returned and not clashes:
        count = len(protected.timetables)
        if status == SolveStatus.INFEASIBLE:
            issues.append(f"No timetable satisfies H1-H11 together with the {count} protected timetable(s) of other cycles. "
                          "They are fixed constraints and were not ignored, and they may be the cause. Choose different "
                          "dates, hours or excluded dates for this cycle, or regenerate after the protected timetable is replaced.")
        elif status == SolveStatus.UNKNOWN:
            issues.append(f"The search stopped before it found a timetable that fits around the {count} protected "
                          "timetable(s) of other cycles. Try a longer time limit or a wider calendar.")
    return {
        "protected_timetables": [{
            "timetable_id": t.timetable_id, "period_code": t.period_code, "period_name": t.period_name,
            "status": t.status, "draft_number": t.draft_number, "start_date": t.start_date, "end_date": t.end_date,
            "sessions_in_range": len(t.sessions), "occupied_venue_slots": sum(len(s.venue_ids) for s in t.sessions),
        } for t in protected.timetables],
        "occupied_slots": slots[:MAX_SLOTS], "occupied_slot_total": len(slots),
        "shared_students": shared_student_count(protected),
        "student_conflict_status": ("none" if not protected.timetables
                                    else "blocked" if clashes or not returned else "clear"),
        "venue_conflicts": venue_conflicts, "student_conflicts": student_conflicts,
        "overlapping_drafts": protected.overlapping_drafts, "protected_fingerprint": protected.fingerprint,
        "blocking_issues": issues,
    }

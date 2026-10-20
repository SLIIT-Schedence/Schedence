"""Module examination eligibility (MID / FINAL) and delivery mode rules.

Pure functions shared by seed validation and the scheduler input checks. They
work on any module record with the eligibility fields (``app.seed.records`` or
``app.sources.types``). No official classification is assumed: every module is
FINAL-only and regular unless its data says otherwise.
"""

from __future__ import annotations

from app.db.seed_models import BLOCK_CATEGORIES, CANDIDATE_SCOPES, DELIVERY_MODES


def exam_duration_minutes(module, exam_kind: str) -> int | None:
    """Writing minutes of the module's examination of this kind; None when it has none."""
    if exam_kind == "FINAL":
        return module.duration_minutes if module.has_final_exam is True else None
    if exam_kind == "MID":
        return module.mid_duration_minutes if module.has_mid_exam is True else None
    return None


def module_exam_errors(module) -> list[str]:
    """Problems with a module's eligibility and delivery fields (empty when valid)."""
    errors = []
    if type(module.has_final_exam) is not bool or type(module.has_mid_exam) is not bool:
        errors.append("has_final_exam and has_mid_exam must be booleans")
    mid = module.mid_duration_minutes
    if module.has_mid_exam is True:
        if type(mid) is not int or not 0 < mid <= 32767:
            errors.append("a MID examination needs a positive whole mid_duration_minutes")
    elif mid is not None:
        errors.append("mid_duration_minutes must be empty when the module has no MID examination")
    if module.delivery_mode not in DELIVERY_MODES:
        errors.append(f"delivery_mode must be one of {', '.join(DELIVERY_MODES)}")
    elif module.delivery_mode == "block" and module.block_category not in BLOCK_CATEGORIES:
        errors.append(f"a block module needs a block_category: {', '.join(BLOCK_CATEGORIES)}")
    elif module.delivery_mode == "regular" and module.block_category is not None:
        errors.append("block_category must be empty for a regular module")
    return errors


def cycle_member_errors(period, module, candidate_scope: str) -> list[str]:
    """Why a module cannot be examined in this cycle (period) with this scope (empty when it can).

    A regular module may be a full member only of a cycle of its own semester;
    from another semester it may only be re-examined for repeat candidates.
    Block Mode modules are not tied to a teaching semester.
    """
    errors = []
    if candidate_scope not in CANDIDATE_SCOPES:
        errors.append(f"candidate_scope must be one of {', '.join(CANDIDATE_SCOPES)}")
    if exam_duration_minutes(module, period.exam_kind) is None:
        errors.append(f"has no {period.exam_kind} examination")
    if candidate_scope == "all" and module.delivery_mode == "regular" and module.semester != period.semester:
        errors.append(f"is a semester {module.semester} module; a semester {period.semester} cycle may include it "
                      "only as repeat_only")
    return errors


def cycle_candidate(registration, candidate_scope: str) -> bool:
    """Whether this registration sits the module in a cycle with this scope."""
    return candidate_scope == "all" or (candidate_scope == "repeat_only" and registration.registration_type == "repeat")

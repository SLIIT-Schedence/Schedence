"""Lifecycle vocabulary and review gates; no status changes or publication."""

from enum import StrEnum

from .ports import PersistenceConflict


class TimetableStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"


def academic_limitations(document):
    blocked = [row.model_dump(mode="json") for row in document.scopes if row.scope_status != "SUPPORTED"]
    issues = [row.model_dump(mode="json") for row in document.issues]
    return dict(blocked_scopes=blocked, issues=issues,
        research_pending=any(row.issue_code == "RESEARCH_PENDING_SESSION_MAPPING" for row in document.issues),
        experimental_calendar=document.calendar_label == "PP1_EXPERIMENTAL_CALENDAR",
        testing_inputs=document.calendar.synthetic_data or any(row.provenance_kind.value in
            {"SYNTHETIC_PP1", "APPROVED_PP1_ESTIMATE", "EXPERIMENTAL_CONFIGURATION"} for row in document.sources),
        academic_complete=False, academic_completion_evidence="NOT_ESTABLISHED_BY_KNOWN_SESSION_COVERAGE")


def check_transition(current, target, *, academic_complete, limitations, approval_authorized=False):
    """Pure future transition guard, deliberately separate from database mutation.

    The deployed append-only table accepts only DRAFT_VALIDATED. Supporting real
    approval/publication transitions will require a separately reviewed design.
    """
    current, target = TimetableStatus(current), TimetableStatus(target)
    allowed = {TimetableStatus.DRAFT: TimetableStatus.VALIDATED,
        TimetableStatus.VALIDATED: TimetableStatus.PENDING_APPROVAL,
        TimetableStatus.PENDING_APPROVAL: TimetableStatus.APPROVED,
        TimetableStatus.APPROVED: TimetableStatus.PUBLISHED,
        TimetableStatus.PUBLISHED: TimetableStatus.SUPERSEDED}
    if allowed.get(current) != target:
        raise PersistenceConflict("unsupported timetable lifecycle transition")
    if target in {TimetableStatus.PENDING_APPROVAL, TimetableStatus.APPROVED, TimetableStatus.PUBLISHED}:
        if not academic_complete or limitations.get("research_pending") or limitations.get("blocked_scopes"):
            raise PersistenceConflict("unresolved academic requirements prevent approval or publication")
        if limitations.get("experimental_calendar") or limitations.get("testing_inputs"):
            raise PersistenceConflict("experimental or testing timetables cannot enter publication workflow")
    if target in {TimetableStatus.APPROVED, TimetableStatus.PUBLISHED, TimetableStatus.SUPERSEDED} and not approval_authorized:
        raise PersistenceConflict("an independently authorized lifecycle action is required")
    return target


def draft_status(value):
    status = TimetableStatus(value)
    if status not in {TimetableStatus.DRAFT, TimetableStatus.VALIDATED}:
        raise PersistenceConflict("this persistence service only saves DRAFT or VALIDATED previews")
    return status

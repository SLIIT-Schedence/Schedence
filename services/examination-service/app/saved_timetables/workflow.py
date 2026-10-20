"""Approval and publication of saved timetables: DRAFT -> APPROVED -> PUBLISHED.

Before every status change, in one transaction with the timetable row locked:

1. the status and the caller's ``expected_revision`` must match (else 409);
2. the stored sessions, rooms and seats must still match the content fingerprint
   taken when the draft was saved (nothing was changed behind the API);
3. the inputs must not be stale: the active scheduling settings, the seating
   capacities and a freshly loaded input snapshot must equal those the timetable
   was generated from, and the independent H1-H11 validator must accept the
   stored timetable against the *current* inputs.

A stale timetable is never approved or published: the answer lists what changed
and the admin regenerates a new draft. At most one timetable per cycle is
PUBLISHED; publishing another needs ``replace_published`` and archives the old one
in the same transaction. Every change is written to the history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.exam_periods.rules import saved_calendar
from app.generation.service import TimetableGenerator
from app.generation.preferences import checked_saved_preferences, snapshot_document
from app.saved_timetables.content import content_fingerprint, rebuild_timetable
from app.saved_timetables.errors import SavedTimetableNotFoundError, TimetableWorkflowError
from app.saved_timetables.store import Row, TimetableStore
from app.scheduling.contracts import Violation
from app.scheduling.inputs import SnapshotError
from app.scheduling.validation import validate_timetable

HARD_RULES = [f"H{i}" for i in range(1, 12)]
STALE_MESSAGE = ("This timetable no longer matches the current inputs or settings and was not changed. "
                 "Regenerate it as a new draft (POST /timetables/{timetable_id}/regenerate) and approve that.")
REQUIRED_STATUS = {"approve": "DRAFT", "publish": "APPROVED"}
STATUS_MESSAGES = {
    ("approve", "APPROVED"): "This timetable is already approved.",
    ("approve", "PUBLISHED"): "This timetable is already published.",
    ("approve", "ARCHIVED"): "An archived timetable cannot be approved. Regenerate it as a new draft.",
    ("publish", "DRAFT"): "A DRAFT timetable must be approved before it can be published.",
    ("publish", "PUBLISHED"): "This timetable is already published.",
    ("publish", "ARCHIVED"): "An archived timetable cannot be published again. Regenerate it as a new draft.",
}


@dataclass(frozen=True)
class StatusChange:
    timetable: Row
    history: Row
    archived: Row | None = None  # the previously published timetable, when it was replaced


def _problem(rule: str, message: str) -> dict[str, str]:
    return {"rule": rule, "message": message}


async def current_problems(generator: TimetableGenerator, header: Row, sessions: list[Row], rooms: list[Row],
                           seats: list[Row]) -> list[dict[str, str]]:
    """Why the saved timetable does not match the current settings and inputs (empty = it matches)."""
    module_codes = None if header["module_codes"] is None else tuple(header["module_codes"])
    problems = []
    try:
        # A timetable generated with a selected calendar is checked against that calendar, never the saved period's.
        calendar = saved_calendar(header.get("inputs"))
        preferences = checked_saved_preferences(header)
    except ValueError:
        return [_problem("STALE_INPUTS", "The stored exam calendar or scheduling preferences cannot be verified.")]
    try:
        extra = {} if preferences is None else {
            "configuration": preferences[0],
            "seating_preferences": {"default_percentage": preferences[1]["seating"]["default_percentage"]},
        }
        configuration, version, snapshot = await generator.current_inputs(header["period_code"], module_codes, calendar, **extra)
    except SnapshotError as exc:
        return [_problem("STALE_INPUTS", "The inputs of this timetable can no longer be loaded."),
                *(_problem(v.constraint, v.message) for v in exc.violations)]
    if preferences is None and (version != header["settings_version"] or configuration.fingerprint != header["settings_fingerprint"]):
        problems.append(_problem("STALE_SETTINGS", f"The scheduling settings changed (version "
                                                   f"{header['settings_version']} -> {version})."))
    capacities = snapshot.capacities
    if preferences is not None and snapshot_document(configuration, capacities)["fingerprint"] != preferences[1]["fingerprint"]:
        problems.append(_problem("STALE_CAPACITIES", "The source seating rules, venue capacities or effective seating configuration changed."))
    if (capacities.ruleset_version != header["seating_ruleset_version"]
            or capacities.fingerprint != header["capacity_fingerprint"]):
        problems.append(_problem("STALE_CAPACITIES", f"The exam seating capacities changed (rule version "
                                                     f"{header['seating_ruleset_version']} -> "
                                                     f"{capacities.ruleset_version})."))
    if snapshot.fingerprint != header["input_fingerprint"]:
        problems.append(_problem("STALE_INPUTS", "The examination inputs changed (period, modules, registrations, "
                                                 "venues, availability or cycle mapping)."))
    violations: tuple[Violation, ...] = validate_timetable(snapshot, rebuild_timetable(header, sessions, rooms, seats))
    problems.extend(_problem(v.constraint, v.message) for v in violations)
    return problems


async def _checked(store: TimetableStore, generator: TimetableGenerator, institution_id: UUID, timetable_id: int,
                   action: str, expected_revision: int) -> tuple[Row, dict[str, Any]]:
    header = await store.lock(institution_id, timetable_id)
    if header is None:
        raise SavedTimetableNotFoundError()
    required = REQUIRED_STATUS[action]
    if header["status"] != required:
        raise TimetableWorkflowError("STATUS", STATUS_MESSAGES[action, header["status"]],
                                     [_problem("STATUS", f"Current status: {header['status']}; "
                                                         f"{action} needs {required}.")])
    if header["revision"] != expected_revision:
        raise TimetableWorkflowError("REVISION", f"The timetable changed since you read it (current revision "
                                                 f"{header['revision']}). Reload it and try again.")
    sessions, rooms, seats = await store.allocations(timetable_id)
    stored = content_fingerprint(sessions, rooms, seats)
    if header["content_fingerprint"] is None:
        raise TimetableWorkflowError("INTEGRITY", "This timetable was saved before content checks existed. "
                                                  "Regenerate it as a new draft.")
    if stored != header["content_fingerprint"]:
        raise TimetableWorkflowError("INTEGRITY", "The stored sessions, rooms or seats no longer match what was "
                                                  "saved. The timetable cannot be " + f"{action}d.")
    problems = await current_problems(generator, header, sessions, rooms, seats)
    if problems:
        raise TimetableWorkflowError("STALE", STALE_MESSAGE, problems)
    details = {"rules_checked": HARD_RULES, "content_fingerprint": stored,
               **{k: header[k] for k in ("input_fingerprint", "settings_version", "settings_fingerprint",
                                         "seating_ruleset_version", "capacity_fingerprint")}}
    return header, details


async def _change(store: TimetableStore, institution_id: UUID, header: Row, to_status: str, *, user_id: UUID,
                  reason: str, details: dict[str, Any]) -> tuple[Row, Row]:
    changed = await store.change_status(institution_id, header["timetable_id"], from_status=header["status"],
                                        to_status=to_status, expected_revision=header["revision"],
                                        user_id=user_id, reason=reason, details=details)
    if changed is None:  # cannot happen while the row is locked; kept as a safe answer
        raise TimetableWorkflowError("REVISION", "The timetable changed at the same time. Reload it and try again.")
    return changed


async def approve(store: TimetableStore, generator: TimetableGenerator, institution_id: UUID, timetable_id: int,
                  *, expected_revision: int, user_id: UUID, reason: str) -> StatusChange:
    header, details = await _checked(store, generator, institution_id, timetable_id, "approve", expected_revision)
    timetable, history = await _change(store, institution_id, header, "APPROVED", user_id=user_id, reason=reason,
                                       details=details)
    await store.commit()
    return StatusChange(timetable, history)


async def publish(store: TimetableStore, generator: TimetableGenerator, institution_id: UUID, timetable_id: int,
                  *, expected_revision: int, user_id: UUID, reason: str, replace_published: bool) -> StatusChange:
    header, details = await _checked(store, generator, institution_id, timetable_id, "publish", expected_revision)
    await store.lock_cycle(institution_id, header["period_code"])
    current = await store.published(institution_id, header["period_code"])
    archived = None
    if current is not None:
        if not replace_published:
            raise TimetableWorkflowError(
                "ALREADY_PUBLISHED",
                f"Timetable {current['timetable_id']} (draft {current['draft_number']}) is already published for "
                "this cycle. Send replace_published=true to archive it and publish this one.")
        archived, _ = await _change(
            store, institution_id, current, "ARCHIVED", user_id=user_id,
            reason=f"Replaced by timetable {timetable_id}: {reason}"[:500],
            details={"replaced_by_timetable_id": timetable_id})
        details = {**details, "replaced_timetable_id": current["timetable_id"]}
    timetable, history = await _change(store, institution_id, header, "PUBLISHED", user_id=user_id, reason=reason,
                                       details=details)
    await store.commit()
    return StatusChange(timetable, history, archived)

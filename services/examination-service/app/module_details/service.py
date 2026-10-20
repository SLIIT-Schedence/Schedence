"""Update an existing module's examination details.

Safety rules:

* Every field is validated, with the same MID/FINAL eligibility and delivery rules
  as the seed data and the scheduler inputs (422, every problem listed).
* An update must send the ``fingerprint`` it last read (409 ``VERSION`` if it changed).
* Every cycle mapping that contains the module must stay valid: the module keeps
  the examination of the cycle's kind, a regular module stays in its semester, and
  the sitting still fits in the exam day (422 ``CYCLE_MAPPING``). Mappings are never changed.
* Once a saved timetable that depends on the module (its cycle maps the module, or
  it has a session of it) has left DRAFT, the fields of that timetable's examination
  kind are locked (409 ``MODULE_LOCKED``). A published FINAL timetable does not lock
  the MID details, and the other way round.
* Saved DRAFT timetables are not changed; after a change they are stale and the
  approval workflow refuses them until they are regenerated.
* ``dry_run`` validates and previews without writing (and without locking).

There is no module history table (that would need a migration): every change is
written to the service log with the user ID.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from app.module_details.errors import conflict, invalid, not_found
from app.module_details.rules import (
    KIND_FIELDS,
    ExamDetails,
    apply_details,
    changed_fields,
    cycle_problems,
    details_fingerprint,
    details_of,
    details_problems,
)
from app.module_details.store import ModuleDetailsStore
from app.sources.types import Module

logger = logging.getLogger(__name__)

STALE_DRAFTS_NOTICE = ("{count} saved draft timetable(s) use this module and were generated from its previous "
                       "details. They can no longer be approved; regenerate them.")


@dataclass(frozen=True)
class DetailsChange:
    dry_run: bool
    changed: bool
    changed_fields: tuple[str, ...]
    module: Module
    draft_timetables: int  # saved DRAFT timetables that depend on the module (stale after a change)
    notices: tuple[str, ...]


class ModuleDetailsService:
    def __init__(self, store: ModuleDetailsStore):
        self._store = store

    async def update(self, module_code: str, details: ExamDetails, *, expected_fingerprint: str, user_id: UUID,
                     dry_run: bool = False) -> DetailsChange:
        try:
            current = await self._store.get(module_code, lock=not dry_run)
            if current is None:
                raise not_found(module_code)
            current_fingerprint = details_fingerprint(module_code, details_of(current))
            if current_fingerprint != expected_fingerprint:
                raise conflict("VERSION", f"The module changed since you read it (current fingerprint "
                                          f"{current_fingerprint}). Reload it and try again.")
            problems = details_problems(details)
            if problems:
                raise invalid(problems)
            changed = changed_fields(details_of(current), details)
            refs = await self._store.timetables(module_code, lock=not dry_run)
            locked = _locked(changed, refs)
            if locked:
                raise conflict("MODULE_LOCKED", "Saved timetables that use this module have left DRAFT; "
                                                "the details they depend on cannot change.", locked)
            updated = apply_details(current, details)
            problems = cycle_problems(updated, await self._store.cycles(module_code)) if changed else []
            if problems:
                raise invalid(problems)
            drafts = sum(1 for r in refs if r.status == "DRAFT")
            notices = (STALE_DRAFTS_NOTICE.format(count=drafts),) if changed and drafts else ()
            if dry_run or not changed:
                await self._store.rollback()
                return DetailsChange(dry_run, bool(changed), tuple(changed), updated, drafts, notices)
            await self._store.update(module_code, details)
            await self._store.commit()
        except BaseException:
            await self._store.rollback()
            raise
        logger.info("Examination details of module %s updated by %s: %s", module_code, user_id, ", ".join(changed))
        saved = await self._store.get(module_code)
        if saved is None:  # cannot happen right after a successful commit
            raise not_found(module_code)
        return DetailsChange(False, True, tuple(changed), saved, drafts, notices)


def _locked(changed: list[str], refs) -> list[dict[str, str]]:
    """One problem per changed field that a released (non-DRAFT) timetable depends on."""
    problems = []
    released = sorted({(r.exam_kind, r.status, r.period_code) for r in refs if r.status != "DRAFT"})
    for name in changed:
        users = [f"{kind} {status} timetable of {period}" for kind, status, period in released
                 if name in KIND_FIELDS.get(kind, KIND_FIELDS["FINAL"] | KIND_FIELDS["MID"])]
        if users:
            problems.append({"rule": "MODULE_LOCKED", "message": f"{name} cannot change: used by {', '.join(users)}."})
    return problems

"""Read and change the examination seating rules.

Every change:

* needs the version the caller last saw (``expected_version``); a different
  stored version means someone else changed the rule -> 409, nothing saved;
* locks the rule row (``SELECT ... FOR UPDATE``) and updates it only if the
  version still matches, so two admins can never overwrite each other;
* writes the rule and its history row in **one** transaction: if either fails,
  both are rolled back;
* with ``dry_run`` returns the same preview but writes nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from app.seating.errors import SeatingConflictError, SeatingNotConfiguredError, SeatingNotFoundError
from app.seating.rules import Action, HistoryEntry, Scope, SeatingPolicy, SeatingRule, VenueCapacity, check_percentage
from app.seating.store import SeatingRuleStore
from app.sources import ExamDataSource, Venue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VenueChange:
    venue_id: str
    normal_capacity: int
    old_percentage: int
    new_percentage: int
    old_exam_capacity: int
    new_exam_capacity: int

    @property
    def available_for_exams(self) -> bool:
        return self.new_exam_capacity > 0


@dataclass(frozen=True)
class SeatingChange:
    dry_run: bool
    changed: bool
    action: Action | None  # None = nothing to change
    scope: Scope
    venue_id: str | None
    old_percentage: int | None
    new_percentage: int | None
    version: int | None  # rule version after the change (dry run: the version it would get)
    history_id: int | None  # None for dry runs and no-ops
    changed_by: UUID | None
    affected_venues: tuple[VenueChange, ...]
    total_exam_capacity_before: int
    total_exam_capacity_after: int

    @property
    def newly_unavailable_venues(self) -> tuple[str, ...]:
        return tuple(v.venue_id for v in self.affected_venues if v.old_exam_capacity > 0 and v.new_exam_capacity == 0)


@dataclass(frozen=True)
class SeatingOverview:
    policy: SeatingPolicy
    ruleset_version: int
    venues: tuple[tuple[Venue, VenueCapacity], ...]
    history: tuple[HistoryEntry, ...]


class SeatingService:
    def __init__(self, store: SeatingRuleStore, source: ExamDataSource):
        self.store = store
        self.source = source

    # --- Reads ------------------------------------------------------------------------------

    async def policy(self) -> SeatingPolicy:
        policy = SeatingPolicy.from_rules(await self.store.list_rules())
        if policy is None:
            raise SeatingNotConfiguredError()
        return policy

    async def venues(self, *, venue_type: str | None = None) -> list[tuple[Venue, VenueCapacity]]:
        policy = await self.policy()
        venues = await self.source.list_venues(venue_type=venue_type)
        return [(v, policy.capacity(v.venue_id, v.normal_capacity)) for v in venues]

    async def overview(self, *, history_limit: int) -> SeatingOverview:
        policy = await self.policy()
        venues = await self.source.list_venues()
        history = await self.store.list_history(limit=history_limit) if history_limit else []
        return SeatingOverview(
            policy=policy,
            ruleset_version=await self.store.ruleset_version(),
            venues=tuple((v, policy.capacity(v.venue_id, v.normal_capacity)) for v in venues),
            history=tuple(history),
        )

    # --- Changes ----------------------------------------------------------------------------

    async def set_default(
        self, *, percentage: int, reason: str, expected_version: int, user_id: UUID, dry_run: bool
    ) -> SeatingChange:
        check_percentage(percentage)
        return await self._in_transaction(
            dry_run, self._set_rule(None, percentage, reason, expected_version, user_id, dry_run)
        )

    async def set_venue_override(
        self, venue_id: str, *, percentage: int, reason: str, expected_version: int | None, user_id: UUID,
        dry_run: bool,
    ) -> SeatingChange:
        """``expected_version=None`` creates a new override (fails if one exists)."""
        check_percentage(percentage)
        return await self._in_transaction(
            dry_run, self._set_rule(venue_id, percentage, reason, expected_version, user_id, dry_run)
        )

    async def delete_venue_override(
        self, venue_id: str, *, reason: str, expected_version: int, user_id: UUID, dry_run: bool
    ) -> SeatingChange:
        return await self._in_transaction(
            dry_run, self._delete_override(venue_id, reason, expected_version, user_id, dry_run)
        )

    async def _in_transaction(self, dry_run: bool, work) -> SeatingChange:
        try:
            change = await work
            if dry_run or not change.changed:
                await self.store.rollback()
            else:
                await self.store.commit()
        except BaseException:
            await self.store.rollback()
            raise
        if change.changed and not dry_run:
            logger.info(
                "Exam seating rule %s: scope=%s venue=%s %s%% -> %s%% version=%s history=%s user=%s",
                change.action, change.scope, change.venue_id, change.old_percentage, change.new_percentage,
                change.version, change.history_id, change.changed_by,
            )
        return change

    async def _venue(self, venue_id: str) -> Venue:
        venue = next((v for v in await self.source.list_venues() if v.venue_id == venue_id), None)
        if venue is None:
            raise SeatingNotFoundError(f"Venue {venue_id} not found")
        return venue

    async def _set_rule(
        self, venue_id: str | None, percentage: int, reason: str, expected_version: int | None, user_id: UUID,
        dry_run: bool,
    ) -> SeatingChange:
        scope: Scope = "default" if venue_id is None else "venue"
        if venue_id is not None:
            await self._venue(venue_id)
        current = await self.store.get_rule(venue_id, lock=not dry_run)
        if venue_id is None and current is None:
            raise SeatingNotConfiguredError()
        if current is None and expected_version is not None:
            raise SeatingConflictError(f"Venue {venue_id} has no seating override any more.")
        if current is not None and expected_version is None:
            raise SeatingConflictError(f"Venue {venue_id} already has a seating override.", current.version)
        if current is not None and current.version != expected_version:
            raise SeatingConflictError("The seating rule was changed by someone else.", current.version)

        before = await self.policy()
        after = before.with_default(percentage) if venue_id is None else before.with_override(venue_id, percentage)
        old_percentage = current.percentage if current else None
        if current is not None and current.percentage == percentage:
            return await self._result(before, after, dry_run=dry_run, changed=False, action=None, scope=scope,
                                      venue_id=venue_id, old=old_percentage, new=percentage, version=current.version)

        action: Action = "update" if current else "create"
        version = current.version + 1 if current else await self.store.last_version(venue_id) + 1
        history_id = None
        if not dry_run:
            saved = await self._save(current, scope, venue_id, percentage, version, reason, user_id)
            entry = await self.store.add_history(
                rule_id=saved.rule_id, action=action, scope=scope, venue_id=venue_id, old_percentage=old_percentage,
                new_percentage=percentage, version=saved.version, changed_by=user_id, reason=reason,
            )
            version, history_id = saved.version, entry.history_id
        return await self._result(before, after, dry_run=dry_run, changed=True, action=action, scope=scope,
                                  venue_id=venue_id, old=old_percentage, new=percentage, version=version,
                                  history_id=history_id, user_id=user_id)

    async def _save(
        self, current: SeatingRule | None, scope: Scope, venue_id: str | None, percentage: int, version: int,
        reason: str, user_id: UUID,
    ) -> SeatingRule:
        if current is None:
            return await self.store.insert_rule(scope=scope, venue_id=venue_id, percentage=percentage,
                                                version=version, reason=reason, updated_by=user_id)
        saved = await self.store.update_rule(current.rule_id, expected_version=current.version,
                                             percentage=percentage, reason=reason, updated_by=user_id)
        if saved is None:  # only possible without the row lock
            raise SeatingConflictError("The seating rule was changed by someone else.")
        return saved

    async def _delete_override(
        self, venue_id: str, reason: str, expected_version: int, user_id: UUID, dry_run: bool
    ) -> SeatingChange:
        await self._venue(venue_id)
        current = await self.store.get_rule(venue_id, lock=not dry_run)
        if current is None:
            raise SeatingNotFoundError(f"Venue {venue_id} has no seating override")
        if current.version != expected_version:
            raise SeatingConflictError("The seating rule was changed by someone else.", current.version)
        before = await self.policy()
        after = before.with_override(venue_id, None)
        history_id = None
        if not dry_run:
            if not await self.store.delete_rule(current.rule_id, expected_version=current.version):
                raise SeatingConflictError("The seating rule was changed by someone else.")
            entry = await self.store.add_history(
                rule_id=current.rule_id, action="delete", scope="venue", venue_id=venue_id,
                old_percentage=current.percentage, new_percentage=None, version=current.version,
                changed_by=user_id, reason=reason,
            )
            history_id = entry.history_id
        return await self._result(before, after, dry_run=dry_run, changed=True, action="delete", scope="venue",
                                  venue_id=venue_id, old=current.percentage, new=None, version=current.version,
                                  history_id=history_id, user_id=user_id)

    async def _result(
        self, before: SeatingPolicy, after: SeatingPolicy, *, dry_run: bool, changed: bool, action: Action | None,
        scope: Scope, venue_id: str | None, old: int | None, new: int | None, version: int | None,
        history_id: int | None = None, user_id: UUID | None = None,
    ) -> SeatingChange:
        venues = await self.source.list_venues()
        old_caps = [before.capacity(v.venue_id, v.normal_capacity) for v in venues]
        new_caps = [after.capacity(v.venue_id, v.normal_capacity) for v in venues]
        affected = tuple(
            VenueChange(o.venue_id, o.normal_capacity, o.percentage, n.percentage, o.exam_capacity, n.exam_capacity)
            for o, n in zip(old_caps, new_caps, strict=True)
            if changed and (o.percentage != n.percentage or o.rule_scope != n.rule_scope)
        )
        change = SeatingChange(
            dry_run=dry_run, changed=changed, action=action, scope=scope, venue_id=venue_id, old_percentage=old,
            new_percentage=new, version=version, history_id=history_id, changed_by=user_id, affected_venues=affected,
            total_exam_capacity_before=sum(c.exam_capacity for c in old_caps),
            total_exam_capacity_after=sum(c.exam_capacity for c in (new_caps if changed else old_caps)),
        )
        return change

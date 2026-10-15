"""In-memory ``SeatingRuleStore`` for tests and offline tools.

Behaves like the database store, including transactions: changes are visible
only to this store until :meth:`commit`; :meth:`rollback` throws them away.
"""

from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass, field, replace
from uuid import UUID

from app.seating.errors import SeatingConflictError
from app.seating.rules import INITIAL_DEFAULT_PERCENTAGE, Action, HistoryEntry, Scope, SeatingRule


@dataclass
class _State:
    rules: dict[int, SeatingRule] = field(default_factory=dict)
    history: list[HistoryEntry] = field(default_factory=list)
    next_rule_id: int = 1
    next_history_id: int = 1


class MemorySeatingStore:
    def __init__(self, default_percentage: int = INITIAL_DEFAULT_PERCENTAGE, *, venue_ids: set[str] | None = None,
                 clock=lambda: dt.datetime.now(dt.UTC)):
        """Starts like migration 0005: one default rule and its history row.

        ``venue_ids``: venues that exist (like the foreign key); ``None`` = any.
        """
        self._clock = clock
        self._venue_ids = venue_ids
        self._committed = _State()
        self._work = self._committed
        self._begin()
        rule = self._new_rule(scope="default", venue_id=None, percentage=default_percentage, version=1,
                              reason="Initial default (memory store)", updated_by=None)
        self._new_history(rule_id=rule.rule_id, action="create", scope="default", venue_id=None, old_percentage=None,
                          new_percentage=default_percentage, version=1, changed_by=None, reason=rule.reason)
        self._committed = self._work
        self._begin()
        self.commits = 0
        self.fail_on: str | None = None  # name of a method that raises, to test rollback

    def _begin(self) -> None:
        self._work = copy.deepcopy(self._committed)

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"simulated failure in {name}")

    def _new_rule(self, **values) -> SeatingRule:
        rule = SeatingRule(rule_id=self._work.next_rule_id, updated_at=self._clock(), **values)
        self._work.next_rule_id += 1
        self._work.rules[rule.rule_id] = rule
        return rule

    def _new_history(self, **values) -> HistoryEntry:
        entry = HistoryEntry(history_id=self._work.next_history_id, changed_at=self._clock(), **values)
        self._work.next_history_id += 1
        self._work.history.append(entry)
        return entry

    # --- SeatingRuleStore ------------------------------------------------------------------

    async def list_rules(self) -> list[SeatingRule]:
        return sorted(self._work.rules.values(), key=lambda r: (r.scope, r.venue_id or ""))

    async def get_rule(self, venue_id: str | None, *, lock: bool = False) -> SeatingRule | None:
        scope = "default" if venue_id is None else "venue"
        return next((r for r in self._work.rules.values() if r.scope == scope and r.venue_id == venue_id), None)

    async def last_version(self, venue_id: str | None) -> int:
        scope = "default" if venue_id is None else "venue"
        return max((h.version for h in self._work.history if h.scope == scope and h.venue_id == venue_id), default=0)

    async def insert_rule(self, *, scope: Scope, venue_id: str | None, percentage: int, version: int, reason: str,
                          updated_by: UUID) -> SeatingRule:
        self._maybe_fail("insert_rule")
        if await self.get_rule(venue_id) is not None or (
            venue_id is not None and self._venue_ids is not None and venue_id not in self._venue_ids
        ):
            raise SeatingConflictError("The seating rule was created or changed by someone else.")
        return self._new_rule(scope=scope, venue_id=venue_id, percentage=percentage, version=version, reason=reason,
                              updated_by=updated_by)

    async def update_rule(self, rule_id: int, *, expected_version: int, percentage: int, reason: str,
                          updated_by: UUID) -> SeatingRule | None:
        self._maybe_fail("update_rule")
        rule = self._work.rules.get(rule_id)
        if rule is None or rule.version != expected_version:
            return None
        rule = replace(rule, percentage=percentage, version=expected_version + 1, reason=reason,
                       updated_by=updated_by, updated_at=self._clock())
        self._work.rules[rule_id] = rule
        return rule

    async def delete_rule(self, rule_id: int, *, expected_version: int) -> bool:
        self._maybe_fail("delete_rule")
        rule = self._work.rules.get(rule_id)
        if rule is None or rule.version != expected_version or rule.scope != "venue":
            return False
        del self._work.rules[rule_id]
        return True

    async def add_history(self, *, rule_id: int, action: Action, scope: Scope, venue_id: str | None,
                          old_percentage: int | None, new_percentage: int | None, version: int, changed_by: UUID,
                          reason: str) -> HistoryEntry:
        self._maybe_fail("add_history")
        return self._new_history(rule_id=rule_id, action=action, scope=scope, venue_id=venue_id,
                                 old_percentage=old_percentage, new_percentage=new_percentage, version=version,
                                 changed_by=changed_by, reason=reason)

    async def list_history(self, *, limit: int, venue_id: str | None = None) -> list[HistoryEntry]:
        rows = [h for h in reversed(self._work.history) if venue_id is None or h.venue_id == venue_id]
        return rows[:limit]

    async def ruleset_version(self) -> int:
        return max((h.history_id for h in self._work.history), default=0)

    async def commit(self) -> None:
        self._maybe_fail("commit")
        self._committed = self._work
        self.commits += 1
        self._begin()

    async def rollback(self) -> None:
        self._begin()

    # --- Test helpers --------------------------------------------------------------------------

    @property
    def committed_rules(self) -> list[SeatingRule]:
        return sorted(self._committed.rules.values(), key=lambda r: r.rule_id)

    @property
    def committed_history(self) -> list[HistoryEntry]:
        return list(self._committed.history)

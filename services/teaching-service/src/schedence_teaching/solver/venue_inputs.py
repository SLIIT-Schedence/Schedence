"""Explicit venue scheduling evidence; absent calendars never imply availability."""

from typing import Annotated

from pydantic import Field, model_validator

from schedence_teaching.preparation import PreparedTimetableInput
from schedence_teaching.schemas import Venue, VenueType, VenueVerification
from schedence_teaching.schemas.models import InputContract, RequiredText

from .config import SolverConfig, TimeWindow
from .results import SolverInputError


class VenueDayInterval(TimeWindow):
    day: RequiredText


class VenueCalendar(InputContract):
    venue_id: RequiredText
    available_windows: dict[RequiredText, list[TimeWindow]] | None = None
    # None means unknown. [] explicitly confirms no blocks in the supplied horizon.
    blocked_periods: list[VenueDayInterval] | None = None


class SessionVenueRequirement(InputContract):
    requirement_id: RequiredText
    allowed_venue_types: set[VenueType] | None = None
    required_features: set[RequiredText] = Field(default_factory=set)
    required_lab_specialization: RequiredText | None = None
    eligible_venue_ids: set[RequiredText] | None = None
    excluded_venue_ids: set[RequiredText] = Field(default_factory=set)


class CombinedVenueRelationship(InputContract):
    combined_venue_id: RequiredText
    component_venue_ids: Annotated[tuple[RequiredText, ...], Field(min_length=1)]


class SharedVenueResource(InputContract):
    resource_id: RequiredText
    venue_ids: Annotated[tuple[RequiredText, ...], Field(min_length=2)]


class VenueSchedulingInput(InputContract):
    venues: Annotated[list[Venue], Field(min_length=1)]
    # Required declaration, including an explicit empty set for synthetic independent rooms.
    unresolved_venue_ids: set[RequiredText]
    calendars: list[VenueCalendar] = Field(default_factory=list)
    requirements: list[SessionVenueRequirement] = Field(default_factory=list)
    venue_features: dict[RequiredText, set[RequiredText]] = Field(default_factory=dict)
    combined_venues: list[CombinedVenueRelationship] = Field(default_factory=list)
    shared_resources: list[SharedVenueResource] = Field(default_factory=list)
    source_workbook: RequiredText | None = None
    verification: VenueVerification | None = None

    @model_validator(mode="after")
    def references(self):
        def unique(values, label):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label}")

        unique([venue.venue_id for venue in self.venues], "venue_id")
        unique([calendar.venue_id for calendar in self.calendars], "venue calendar")
        unique([rule.requirement_id for rule in self.requirements], "venue requirement_id")
        unique([relation.combined_venue_id for relation in self.combined_venues], "combined venue relationship")
        unique([resource.resource_id for resource in self.shared_resources], "shared resource_id")
        ids = {venue.venue_id for venue in self.venues}
        referenced = set(self.unresolved_venue_ids) | set(self.venue_features)
        referenced.update(calendar.venue_id for calendar in self.calendars)
        for rule in self.requirements:
            referenced.update(rule.eligible_venue_ids or ())
            referenced.update(rule.excluded_venue_ids)
        graph = {}
        for relation in self.combined_venues:
            unique(relation.component_venue_ids, "component venue_id")
            graph[relation.combined_venue_id] = relation.component_venue_ids
            referenced.add(relation.combined_venue_id)
            referenced.update(relation.component_venue_ids)
        for resource in self.shared_resources:
            unique(resource.venue_ids, "shared-resource venue_id")
            referenced.update(resource.venue_ids)
        if referenced - ids:
            raise ValueError("unknown venue_id in scheduling evidence: " + ", ".join(sorted(referenced - ids)))
        if set(graph) & self.unresolved_venue_ids:
            raise ValueError("a combined venue cannot be both confirmed and unresolved")
        # Iterative traversal permits nested combined spaces without recursion limits.
        done = set()
        for venue_id in graph:
            visiting = set()
            stack = [(venue_id, False)]
            while stack:
                current, exiting = stack.pop()
                if exiting:
                    visiting.remove(current)
                    done.add(current)
                elif current not in done:
                    if current in visiting:
                        raise ValueError("combined venue relationships contain a cycle")
                    visiting.add(current)
                    stack.append((current, True))
                    stack.extend((child, False) for child in graph.get(current, ()))
        return self


def venue_dependencies(inputs: VenueSchedulingInput) -> dict[str, frozenset[str]]:
    graph = {row.combined_venue_id: row.component_venue_ids for row in inputs.combined_venues}
    dependencies = {}
    for venue in inputs.venues:
        seen, pending = set(), [venue.venue_id]
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(graph.get(current, ()))
        dependencies[venue.venue_id] = frozenset(seen)
    return dependencies


def venue_footprints(inputs: VenueSchedulingInput) -> dict[str, frozenset[tuple[str, str]]]:
    dependencies = venue_dependencies(inputs)
    return {
        venue_id: frozenset(
            {("venue", component) for component in components}
            | {("shared", resource.resource_id) for resource in inputs.shared_resources
               if components.intersection(resource.venue_ids)}
        ) for venue_id, components in dependencies.items()
    }


def checked_venue_inputs(
    prepared: PreparedTimetableInput, config: SolverConfig, inputs: VenueSchedulingInput,
) -> VenueSchedulingInput:
    if not isinstance(inputs, VenueSchedulingInput):
        raise SolverInputError("Venue-enabled mode requires VenueSchedulingInput; it never falls back to venue-free mode.")
    inputs = VenueSchedulingInput.model_validate(inputs.model_dump(round_trip=True))
    requirements = {row.requirement_id for row in prepared.input_snapshot.session_requirements}
    for rule in inputs.requirements:
        if rule.requirement_id not in requirements:
            raise SolverInputError(f"Unknown requirement_id in venue requirements: {rule.requirement_id}")
    dependencies = venue_dependencies(inputs)
    footprints = venue_footprints(inputs)
    bookable = {key for key, values in dependencies.items() if not values.intersection(inputs.unresolved_venue_ids)}
    calendars = {calendar.venue_id: calendar for calendar in inputs.calendars}
    days = {day.day for day in config.days}
    for calendar in inputs.calendars:
        referenced_days = set(calendar.available_windows or {}) | {period.day for period in calendar.blocked_periods or ()}
        if referenced_days - days:
            raise SolverInputError(f"Unknown scheduling day in venue calendar: {calendar.venue_id}")
        windows = [window for values in (calendar.available_windows or {}).values() for window in values]
        for window in [*windows, *(calendar.blocked_periods or ())]:
            if window.start_minute % config.granularity_minutes or window.end_minute % config.granularity_minutes:
                raise SolverInputError(f"Venue calendar boundaries must align with scheduling granularity: {calendar.venue_id}")
    for venue_id in sorted(bookable):
        if venue_id not in calendars or calendars[venue_id].available_windows is None:
            raise SolverInputError(f"missing_venue_availability: {venue_id} requires explicit available_windows.")
        missing_days = days - calendars[venue_id].available_windows.keys()
        if missing_days:
            raise SolverInputError(
                f"missing_venue_availability: {venue_id} requires explicit windows or [] for days: {', '.join(sorted(missing_days))}"
            )
    # A block on any explicitly shared space also affects its intersecting resources.
    relevant = {key for key, resources in footprints.items()
                if any(resources.intersection(footprints[other]) for other in bookable)}
    for venue_id in sorted(relevant):
        if venue_id not in calendars or calendars[venue_id].blocked_periods is None:
            raise SolverInputError(f"unknown_venue_blocked_periods: {venue_id}; supply periods or an explicitly confirmed [].")
    from schedence_teaching.policy.resolution import verify_component
    try:
        verify_component(config, 'venue_input', inputs)
    except ValueError as error:
        raise SolverInputError(str(error)) from None
    return inputs

"""Read-only generation input readiness for the Academic Admin wizard, reusing existing authentication.

Requires the same explicit, institution-scoped teaching:generate permission as starting a generation.
It never stores a snapshot, accepts a job or runs the solver.
"""

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, JsonValue

from schedence_teaching.generation.readiness import ReadinessScope
from schedence_teaching.generation.scheduling_rules import SchedulingRules

from .errors import ApiError
from .generations import generation_institution
from .routes import authenticate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/generation-inputs", tags=["teaching generation"])


class CheckOut(BaseModel):
    key: str
    title: str
    status: Literal["READY", "WARNING", "MISSING"]
    detail: str


class BlockedScopeOut(BaseModel):
    academic_year: int
    stream: str
    specialization: str
    student_count: int | None
    scope_status: str


class InputsOut(BaseModel):
    staff: int
    modules: int
    module_offerings: int
    student_groups: int
    venues: int
    teaching_team_entries: int
    lecturer_preferences: int
    session_requirements: int
    expected_sessions: int
    specializations: list[str]
    blocked_scopes: list[BlockedScopeOut]
    unresolved_issue_codes: list[str]
    calendar_label: str
    academic_term_reference: str
    configuration_label: str
    prepared_snapshot_sha256: str


class SchedulingRulesOut(BaseModel):
    selected: SchedulingRules | None
    experimental_defaults: SchedulingRules
    reference: str | None
    connected: dict[str, JsonValue]
    protected: dict[str, JsonValue]
    objective_order: tuple[str, ...]


class ReadinessOut(BaseModel):
    institution_id: UUID
    semester: int
    academic_years: list[int]
    streams: list[str]
    ready: bool
    checks: list[CheckOut]
    inputs: InputsOut | None
    generation_enabled: bool
    snapshot_stored: bool = False
    university_approved: Literal[False] = False
    scheduling_rules: SchedulingRulesOut | None = None
    stored_input: "StoredInputOut | None" = None


class StoredInputOut(BaseModel):
    institution_id: UUID
    snapshot_id: UUID
    revision: int
    semantic_sha256: str
    validated_snapshot_id: UUID | None
    validated_rules_reference: str
    semester: int
    academic_years: list[int]
    streams: list[Literal["WEEKDAY", "WEEKEND"]]
    calendar_label: Literal["PP1_TEST_CALENDAR", "PP1_EXPERIMENTAL_CALENDAR"]
    academic_term_reference: str
    calendar_reference: str
    scheduling_policy_reference: str
    persistence: Literal["ISOLATED_POSTGRESQL"]


ReadinessOut.model_rebuild()


@router.get("", response_model=ReadinessOut, responses={
    403: {"description": "Missing explicit institution-scoped teaching:generate permission"},
    503: {"description": "Readiness is not enabled in this deployment, or an owner service is unavailable"}})
async def readiness(auth: Annotated[tuple, Depends(authenticate)],
                    semester: Annotated[int, Query(ge=1, le=2)],
                    institution_id: Annotated[UUID | None, Query()] = None,
                    academic_year: Annotated[list[Annotated[int, Field(ge=1, le=4)]] | None, Query(max_length=4)] = None,
                    stream: Annotated[list[Literal["WEEKDAY", "WEEKEND"]] | None, Query(max_length=2)] = None):
    """Prepare the institution's inputs for one scope and run the generation pre-solve validation, read-only."""
    return await assess_readiness(auth, semester, institution_id, academic_year, stream)


@router.post("", response_model=ReadinessOut, responses={
    403: {"description": "Missing explicit institution-scoped teaching:generate permission"},
    503: {"description": "Readiness is disabled or an owner service is unavailable"}})
async def validate_scheduling_rules(body: SchedulingRules, auth: Annotated[tuple, Depends(authenticate)],
                    semester: Annotated[int, Query(ge=1, le=2)],
                    institution_id: Annotated[UUID | None, Query()] = None,
                    academic_year: Annotated[list[Annotated[int, Field(ge=1, le=4)]] | None, Query(max_length=4)] = None,
                    stream: Annotated[list[Literal["WEEKDAY", "WEEKEND"]] | None, Query(max_length=2)] = None):
    """Validate experimental rules against owner-verified inputs. Never stores a snapshot or starts a solve."""
    return await assess_readiness(auth, semester, institution_id, academic_year, stream, body)


async def assess_readiness(auth, semester, institution_id, academic_year, stream, rules=None):
    runtime, principal = auth
    institution = generation_institution(principal, institution_id)
    if getattr(runtime, "readiness", None) is None:
        raise ApiError(503, "READINESS_DISABLED", "Generation input readiness is not enabled in this deployment.")
    scope = ReadinessScope(semester=semester, academic_years=frozenset(academic_year or (1, 2, 3, 4)),
                           streams=frozenset(stream or ("WEEKDAY", "WEEKEND")))
    try:
        result = (await runtime.readiness.assess(institution, scope) if rules is None else
                  await runtime.readiness.assess(institution, scope, rules))
    except Exception:
        logger.exception("Generation readiness failed")
        raise ApiError(503, "READINESS_UNAVAILABLE", "The institution's input records could not be read. Try again shortly.") from None
    return ReadinessOut(institution_id=institution, semester=semester, academic_years=sorted(scope.academic_years),
        streams=sorted(scope.streams), ready=result["ready"], checks=result["checks"], inputs=result["inputs"],
        generation_enabled=runtime.generations is not None, scheduling_rules=result.get("scheduling_rules"),
        stored_input=result.get("stored_input"), snapshot_stored=result.get("stored_input") is not None)

"""Privileged generation requests and deliberately small public job projections."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from schedence_teaching.persistence.contracts import Sha256
from schedence_teaching.persistence.generation_inputs import DatabaseGenerationRequest
from schedence_teaching.schemas.models import InputContract, RequiredText


class GenerationRequest(DatabaseGenerationRequest):
    academic_term_reference: Annotated[RequiredText, Field(max_length=200)]
    calendar_reference: Sha256
    scheduling_policy_reference: Sha256
    strategy: Literal["MONOLITHIC", "COORDINATED_COHORTS"] = "MONOLITHIC"
    # Native time limit is per subproblem for coordinated solves. This separate
    # wall limit also covers child startup, input reconstruction and validation.
    job_timeout_seconds: Annotated[float, Field(gt=0, le=3600, allow_inf_nan=False)] = 300
    timetable_series_id: UUID | None = None
    expected_previous_version_id: UUID | None = None

    @model_validator(mode="after")
    def bounded_solver(self):
        if self.snapshot_id.int == 0 or self.max_time_seconds > self.job_timeout_seconds:
            raise ValueError("a nonzero snapshot and bounded native solver timeout are required")
        if self.expected_previous_version_id is not None and self.timetable_series_id is None:
            raise ValueError("a previous version requires its timetable series")
        if any(value is not None and value.int == 0 for value in
               (self.timetable_series_id, self.expected_previous_version_id)):
            raise ValueError("persistence references must be nonzero")
        return self

    def database_request(self):
        return DatabaseGenerationRequest.model_validate({key: getattr(self, key)
            for key in DatabaseGenerationRequest.model_fields})


class JobError(InputContract):
    category: str
    message: str
    retry_with_new_key: bool = False


class GenerationJobOut(InputContract):
    job_id: UUID
    institution_id: UUID
    status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"]
    durability: Literal["DEVELOPMENT_NON_DURABLE", "POSTGRESQL_DURABLE"] = "DEVELOPMENT_NON_DURABLE"
    request: GenerationRequest
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float
    solver_status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"] | None = None
    solver_runtime_seconds: float | None = None
    validation_status: Literal["NOT_ASSESSED", "PASSED", "FAILED"] = "NOT_ASSESSED"
    expected_occurrences: int
    scheduled_occurrences: int = 0
    timetable_version_id: UUID | None = None
    result_snapshot_id: UUID | None = None
    persistence: Literal["DISABLED", "ISOLATED_POSTGRESQL"]
    scope_completion: Literal["NOT_COMPLETE", "ALL_SELECTED_KNOWN_OCCURRENCES_VALIDATED"] = "NOT_COMPLETE"
    academic_complete: Literal[False] = False
    calendar_label: str
    university_calendar_approved: Literal[False] = False
    separate_semester_alternative: Literal[True] = True
    unresolved_requirement_codes: tuple[str, ...]
    blocked_student_count: int
    attempt_number: int = 0
    max_attempts: int = 1
    last_heartbeat_at: datetime | None = None
    error: JobError | None = None

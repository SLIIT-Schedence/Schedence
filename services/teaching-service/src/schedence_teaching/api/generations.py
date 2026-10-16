"""Privileged asynchronous generation routes, reusing existing authentication."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from schedence_teaching.generation.contracts import GenerationJobOut, GenerationRequest

from .errors import ApiError
from .routes import authenticate

router = APIRouter(prefix="/generations", tags=["teaching generation"])
ERROR_RESPONSES = {400: {"description": "Invalid input reference or configuration"},
    401: {"description": "Missing, invalid or expired Cognito access token"},
    403: {"description": "Missing explicit institution-scoped teaching:generate permission"},
    404: {"description": "Job not found in the authorized institution"},
    409: {"description": "Idempotency, active-job quota or version conflict"},
    503: {"description": "Execution disabled or required infrastructure unavailable"}}


def generation_institution(principal, institution_id):
    eligible = {key for key, grant in principal.grants.items() if "teaching:generate" in grant.permissions}
    if institution_id is not None:
        if institution_id not in eligible:
            raise ApiError(403, "GENERATION_FORBIDDEN", "Explicit Teaching generation permission is required for this institution.")
        return institution_id
    if not eligible:
        raise ApiError(403, "GENERATION_FORBIDDEN", "Explicit Teaching generation permission is required.")
    if len(eligible) != 1:
        raise ApiError(400, "INSTITUTION_REQUIRED", "Select an institution with explicit Teaching generation permission.")
    return next(iter(eligible))


def jobs_for(runtime):
    if runtime.generations is None:
        raise ApiError(503, "GENERATION_DISABLED", "No generation worker is enabled in this deployment.")
    return runtime.generations


@router.post("", status_code=202, response_model=GenerationJobOut, response_model_exclude_none=True, responses=ERROR_RESPONSES)
async def generate(body: GenerationRequest, response: Response,
                   auth: Annotated[tuple, Depends(authenticate)],
                   idempotency_key: Annotated[str | None, Header(max_length=128)] = None):
    runtime, principal = auth
    generation_institution(principal, body.institution_id)
    job = await jobs_for(runtime).submit(body, idempotency_key, principal.user_id)
    response.headers["Location"] = f"/api/v1/teaching/generations/{job.job_id}?institution_id={job.institution_id}"
    response.headers["Retry-After"] = "2"
    return job


@router.get("/{job_id}", response_model=GenerationJobOut, response_model_exclude_none=True, responses=ERROR_RESPONSES)
async def generation(job_id: UUID, auth: Annotated[tuple, Depends(authenticate)],
                     institution_id: Annotated[UUID | None, Query()] = None):
    runtime, principal = auth
    institution = generation_institution(principal, institution_id)
    return await jobs_for(runtime).get(institution, job_id)

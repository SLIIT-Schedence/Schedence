"""Authenticated sending and recipient access. No response/submission endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request

from schedence_teaching.api.errors import ApiError

from .routes import Auth, authorize
from .sending_contracts import DistributionOut, DistributionPage, EligibleLecturer, LecturerAssignments, LecturerForm, RecipientPage, SendForm

router = APIRouter(tags=["form sending"])
Page = Annotated[int, Query(ge=1, le=100000)]
PageSize = Annotated[int, Query(ge=1, le=100)]
SendKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]


def sending(runtime):
    if runtime.form_sending is None:
        raise ApiError(503, "FORM_SENDING_UNAVAILABLE", "Form sending is unavailable in this deployment.")
    return runtime.form_sending


@router.get("/form-recipients", response_model=tuple[EligibleLecturer, ...])
async def eligible(request: Request, auth: Auth, institution_id: UUID):
    runtime, _ = authorize(auth, institution_id)
    return await sending(runtime).lecturers(institution_id, request.state.teaching_bearer_token)


@router.post("/form-templates/{template_id}/send", response_model=DistributionOut, status_code=201)
async def send(request: Request, template_id: UUID, body: SendForm, auth: Auth, idempotency_key: SendKey):
    runtime, principal = authorize(auth, body.institution_id)
    return await sending(runtime).send(template_id, body, principal.user_id, idempotency_key,
        bearer_token=request.state.teaching_bearer_token)


@router.get("/form-distributions", response_model=DistributionPage)
async def listing(auth: Auth, institution_id: UUID, page: Page = 1, page_size: PageSize = 20):
    runtime, _ = authorize(auth, institution_id)
    service = sending(runtime)
    rows, total = await service.store.list(institution_id, (page - 1) * page_size, page_size)
    return DistributionPage(items=tuple(service.present(row) for row in rows), total=total, page=page, page_size=page_size)


@router.get("/form-distributions/{identifier}", response_model=DistributionOut)
async def detail(identifier: UUID, auth: Auth, institution_id: UUID):
    runtime, _ = authorize(auth, institution_id)
    service = sending(runtime)
    return service.present(await service.get(institution_id, identifier))


@router.get("/form-distributions/{identifier}/recipients", response_model=RecipientPage)
async def recipients(identifier: UUID, auth: Auth, institution_id: UUID, page: Page = 1, page_size: PageSize = 20):
    runtime, _ = authorize(auth, institution_id)
    service = sending(runtime)
    await service.get(institution_id, identifier)
    rows, total = await service.store.recipients(institution_id, identifier, (page - 1) * page_size, page_size)
    return RecipientPage(items=rows, total=total, page=page, page_size=page_size)


@router.get("/lecturer/form-distributions", response_model=LecturerAssignments)
async def inbox(request: Request, auth: Auth, institution_id: UUID, page: Page = 1, page_size: PageSize = 20):
    runtime, principal = auth
    rows, total = await sending(runtime).inbox(institution_id, principal, (page - 1) * page_size, page_size,
        bearer_token=request.state.teaching_bearer_token)
    return LecturerAssignments(items=rows, total=total, page=page, page_size=page_size)


@router.get("/lecturer/form-distributions/{identifier}", response_model=LecturerForm)
async def lecturer_form(request: Request, identifier: UUID, auth: Auth, institution_id: UUID):
    runtime, principal = auth
    return await sending(runtime).lecturer_access(institution_id, identifier, principal,
        bearer_token=request.state.teaching_bearer_token)


@router.post("/lecturer/form-distributions/{identifier}/opened", response_model=LecturerForm)
async def opened(request: Request, identifier: UUID, auth: Auth, institution_id: UUID):
    runtime, principal = auth
    return await sending(runtime).lecturer_access(institution_id, identifier, principal, opened=True,
        bearer_token=request.state.teaching_bearer_token)

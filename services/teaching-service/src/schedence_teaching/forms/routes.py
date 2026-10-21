"""Authenticated, institution-scoped draft builder; no send, publish or submission routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from schedence_teaching.api.errors import ApiError
from schedence_teaching.api.routes import authenticate

from .contracts import CreateTemplate, DuplicateTemplate, FormTemplate, Preset, SaveTemplate, TemplatePage
from .presets import teaching_preferences
from .service import FormTemplates

router = APIRouter(prefix="/form-templates", tags=["draft form templates"])
Auth = Annotated[tuple, Depends(authenticate)]


def authorize(auth, institution):
    runtime, principal = auth
    if institution.int == 0:
        raise ApiError(422, "INVALID_INSTITUTION", "Select a valid institution.")
    grant = principal.grants.get(institution)
    # Existing verified institution permission, held by Academic Admin by default.
    # Appointment titles, raw JWT role claims and platform grants are not authority.
    if grant is None or "teaching:generate" not in grant.permissions:
        raise ApiError(403, "FORM_BUILDER_FORBIDDEN", "Teaching administrative permission is required for this institution.")
    return runtime, principal


def service(runtime):
    if runtime.forms is None:
        raise ApiError(503, "FORM_STORE_UNAVAILABLE", "Draft form persistence is not configured.")
    return FormTemplates(runtime.forms)


@router.get("/presets", response_model=tuple[Preset, ...])
async def presets(auth: Auth, institution_id: UUID):
    authorize(auth, institution_id)
    return (teaching_preferences(),)


@router.post("", response_model=FormTemplate, status_code=201)
async def create(body: CreateTemplate, auth: Auth):
    runtime, principal = authorize(auth, body.institution_id)
    return await service(runtime).create(body, principal.user_id)


@router.get("", response_model=TemplatePage)
async def listing(auth: Auth, institution_id: UUID,
                  page: Annotated[int, Query(ge=1, le=100_000)] = 1,
                  page_size: Annotated[int, Query(ge=1, le=100)] = 20):
    runtime, _ = authorize(auth, institution_id)
    items, total = await service(runtime).store.list(institution_id, (page - 1) * page_size, page_size)
    return TemplatePage(items=items, total=total, page=page, page_size=page_size)


@router.get("/{template_id}", response_model=FormTemplate)
async def detail(template_id: UUID, auth: Auth, institution_id: UUID):
    runtime, _ = authorize(auth, institution_id)
    return await service(runtime).get(institution_id, template_id)


@router.put("/{template_id}", response_model=FormTemplate)
async def save(template_id: UUID, body: SaveTemplate, auth: Auth):
    runtime, principal = authorize(auth, body.institution_id)
    return await service(runtime).save(template_id, body, principal.user_id)


@router.post("/{template_id}/duplicate", response_model=FormTemplate, status_code=201)
async def duplicate(template_id: UUID, body: DuplicateTemplate, auth: Auth):
    runtime, principal = authorize(auth, body.institution_id)
    return await service(runtime).duplicate(template_id, body, principal.user_id)

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, EmailStr, Field, model_validator

from app.schemas.common import ApiModel, RequestModel

INSTITUTION_CODE_HELP = "Short unique code, e.g. SLIIT. Letters, digits, '_' and '-'; stored upper-case."
InstitutionCode = Field(min_length=2, max_length=30, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", description=INSTITUTION_CODE_HELP)


class InstitutionOut(ApiModel):
    id: UUID
    code: str
    name: str
    status: Literal["ACTIVE", "DISABLED"]
    created_at: datetime
    updated_at: datetime


class InstitutionSummaryOut(ApiModel):
    id: UUID
    code: str
    name: str
    status: Literal["ACTIVE", "DISABLED"]


class CreateInstitutionIn(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"code": "SLIIT", "name": "Sri Lanka Institute of Information Technology"}]})

    code: str = InstitutionCode
    name: str = Field(min_length=1, max_length=200)


class UpdateInstitutionIn(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "Sri Lanka Institute of Information Technology"}]})

    code: str | None = Field(default=None, min_length=2, max_length=30, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$",
                             description=INSTITUTION_CODE_HELP)
    name: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _something_to_change(self) -> UpdateInstitutionIn:
        if self.code is None and self.name is None:
            raise ValueError("give 'code', 'name', or both")
        return self


class CreateInstitutionAdminIn(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"email": "admin@sliit.example", "full_name": "Institution Admin"}]})

    email: EmailStr = Field(max_length=320, description="Becomes the admin's sign-in e-mail; Cognito sends the invitation here.")
    full_name: str = Field(min_length=1, max_length=200)

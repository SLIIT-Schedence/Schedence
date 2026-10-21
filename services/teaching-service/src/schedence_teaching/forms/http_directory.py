"""Read recipient identities through the Academic owner's authenticated APIs."""
from collections import Counter

from pydantic import ValidationError

from schedence_teaching.api.errors import ApiError
from .sending_contracts import EligibleLecturer


class ApiLecturerDirectory:
    requires_bearer_token = True

    def __init__(self, owners, roster_path, self_path):
        self.owners, self.roster_path, self.self_path = owners, roster_path, self_path

    async def eligible(self, institution, *, bearer_token, for_user=None):
        payload = await self.owners.get("academic", self.self_path if for_user else self.roster_path,
            institution, bearer_token, error_code="FORM_SENDING_UNAVAILABLE")
        try:
            if payload["complete"] is not True:
                raise ValueError("partial recipient export")
            rows = tuple(EligibleLecturer.model_validate(row) for row in payload["items"])
            ids = [row.staff_id for row in rows]
            links = Counter(row.identity_user_id for row in rows if row.identity_user_id is not None)
            if len(set(ids)) != len(ids) or any(row.link_status == "READY" and links[row.identity_user_id] != 1 for row in rows):
                raise ValueError("ambiguous recipient identity")
            if for_user and (len(rows) > 1 or any(row.identity_user_id != for_user for row in rows)):
                raise ValueError("self endpoint returned another identity")
            return rows
        except (ValueError, KeyError, TypeError, ValidationError):
            raise ApiError(503, "FORM_SENDING_UNAVAILABLE", "Academic returned inconsistent recipient identity data.") from None

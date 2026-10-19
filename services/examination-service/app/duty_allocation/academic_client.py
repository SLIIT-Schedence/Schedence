"""Read-only client for the Academic Data Service staff endpoints (Member 3).

Same caller-token pattern as app.auth.identity_client: requests carry the *caller's own*
verified access token, so the Academic Data Service applies that user's permissions
(academic:staff:read, academic:workload:read) and institution scope itself. There is no
service account. Tokens are never logged; only status codes and exception type names are.

Endpoints (services/academic-data-service/app/api/v1/staff.py, prefix /api/v1/academic):
    GET /staff?institution_id=&page=&page_size=      Page[StaffOut]  (page_size <= 100)
    GET /staff/{staff_id}                             StaffOut
    GET /staff/{staff_id}/administrative-workload     AdministrativeWorkloadOut (?as_of=)

Every request names the institution, and every returned record must belong to it; a
record from any other institution fails the whole call (fail closed). Responses with an
unexpected shape fail rather than being guessed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

ACADEMIC_PREFIX = "/api/v1/academic"
MAX_PAGE_SIZE = 100  # the Academic Data Service rejects larger page_size values


class AcademicDataError(Exception):
    """Base error; ``code`` is stable for callers, ``status`` is the HTTP status if any."""
    code = "ACADEMIC_DATA_ERROR"

    def __init__(self, message: str, *, status: int | None = None, remote_code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.remote_code = remote_code  # the Academic Data Service error.code, when it sent one


class AcademicDataUnauthenticated(AcademicDataError):
    code = "ACADEMIC_DATA_UNAUTHENTICATED"


class AcademicDataForbidden(AcademicDataError):
    code = "ACADEMIC_DATA_FORBIDDEN"


class AcademicDataNotFound(AcademicDataError):
    code = "ACADEMIC_DATA_NOT_FOUND"


class AcademicDataUnavailable(AcademicDataError):
    code = "ACADEMIC_DATA_UNAVAILABLE"


class AcademicDataMalformed(AcademicDataError):
    code = "ACADEMIC_DATA_MALFORMED"


class AcademicDataInstitutionMismatch(AcademicDataError):
    code = "ACADEMIC_DATA_INSTITUTION_MISMATCH"


@dataclass(frozen=True)
class MissingWorkload:
    staff_id: str
    reason: str


@dataclass(frozen=True)
class WorkloadFetch:
    records: tuple[dict, ...]  # raw AdministrativeWorkloadOut rows, institution-checked
    missing: tuple[MissingWorkload, ...]  # staff the service returned 404 for


def _uuid(value: object, label: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise AcademicDataMalformed(f"{label} is not a UUID.") from None


def _remote_code(response: httpx.Response) -> str | None:
    try:
        code = response.json().get("error", {}).get("code")
        return code if isinstance(code, str) else None
    except (ValueError, AttributeError):
        return None


class AcademicDataClient:
    def __init__(self, base_url: str, *, http: httpx.AsyncClient, timeout_seconds: float = 5.0,
                 page_size: int = MAX_PAGE_SIZE, max_pages: int = 1_000, max_concurrency: int = 5) -> None:
        if type(base_url) is not str or not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an http(s) URL.")
        if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}.")
        if type(max_pages) is not int or max_pages < 1 or type(max_concurrency) is not int or max_concurrency < 1:
            raise ValueError("max_pages and max_concurrency must be positive ints.")
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.timeout = timeout_seconds
        self.page_size = page_size
        self.max_pages = max_pages
        self.max_concurrency = max_concurrency

    async def _get(self, token: str, path: str, params: Mapping | None = None) -> object:
        if type(token) is not str or not token.strip():
            raise AcademicDataUnauthenticated("A caller access token is required.")
        try:
            response = await self.http.get(f"{self.base_url}{ACADEMIC_PREFIX}{path}", params=params,
                                           timeout=self.timeout,
                                           headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        except httpx.TimeoutException as exc:
            logger.warning("Academic Data Service timed out (%s)", type(exc).__name__)
            raise AcademicDataUnavailable("The Academic Data Service did not respond in time.") from None
        except httpx.HTTPError as exc:
            logger.warning("Academic Data Service unreachable (%s)", type(exc).__name__)
            raise AcademicDataUnavailable("The Academic Data Service could not be reached.") from None

        status = response.status_code
        if status == 200:
            try:
                return response.json()
            except ValueError:
                raise AcademicDataMalformed("The Academic Data Service returned invalid JSON.", status=200) from None
        remote = _remote_code(response)
        if status == 401:
            raise AcademicDataUnauthenticated("The access token was rejected.", status=401, remote_code=remote)
        if status == 403:
            raise AcademicDataForbidden("The caller lacks permission for this staff data.", status=403, remote_code=remote)
        if status == 404:
            raise AcademicDataNotFound("The requested staff data was not found.", status=404, remote_code=remote)
        logger.warning("Academic Data Service returned HTTP %s", status)
        raise AcademicDataUnavailable(f"The Academic Data Service returned HTTP {status}.", status=status, remote_code=remote)

    @staticmethod
    def _check_staff(row: object, institution: uuid.UUID, label: str) -> dict:
        if not isinstance(row, Mapping):
            raise AcademicDataMalformed(f"{label} is not an object.")
        _uuid(row.get("id"), f"{label}.id")
        if _uuid(row.get("institution_id"), f"{label}.institution_id") != institution:
            raise AcademicDataInstitutionMismatch(f"{label} belongs to another institution.")
        return dict(row)

    async def list_staff(self, token: str, *, institution_id: uuid.UUID) -> tuple[dict, ...]:
        """Every staff record of the institution, all pages, in service order."""
        institution = _uuid(institution_id, "institution_id")
        rows: list[dict] = []
        seen: set[uuid.UUID] = set()
        expected_total: int | None = None
        for page in range(1, self.max_pages + 1):
            data = await self._get(token, "/staff", {"institution_id": str(institution), "page": page,
                                                     "page_size": self.page_size})
            if not isinstance(data, Mapping) or not isinstance(data.get("items"), list) \
                    or type(data.get("total")) is not int or data["total"] < 0 or data.get("page") != page:
                raise AcademicDataMalformed(f"Staff page {page} does not have the Page[StaffOut] shape.")
            if expected_total is None:
                expected_total = data["total"]
            elif data["total"] != expected_total:
                raise AcademicDataUnavailable("The staff list changed while it was being read; retry.")
            for index, item in enumerate(data["items"]):
                row = self._check_staff(item, institution, f"staff page {page} item {index}")
                staff_id = _uuid(row["id"], "id")
                if staff_id in seen:
                    raise AcademicDataUnavailable("The staff list changed while it was being read; retry.")
                seen.add(staff_id)
                rows.append(row)
            if len(rows) >= expected_total or not data["items"]:
                break
        else:
            raise AcademicDataMalformed(f"Staff list exceeded {self.max_pages} pages.")
        if len(rows) != expected_total:
            raise AcademicDataUnavailable(f"Expected {expected_total} staff records, received {len(rows)}; retry.")
        return tuple(rows)

    async def get_staff(self, token: str, staff_id: uuid.UUID | str, *, institution_id: uuid.UUID) -> dict:
        staff = _uuid(staff_id, "staff_id")
        row = self._check_staff(await self._get(token, f"/staff/{staff}"), _uuid(institution_id, "institution_id"),
                                "staff record")
        if _uuid(row["id"], "id") != staff:
            raise AcademicDataMalformed("The service returned a different staff record.")
        return row

    async def get_administrative_workload(self, token: str, staff_id: uuid.UUID | str, *, institution_id: uuid.UUID,
                                          as_of: dt.date | None = None) -> dict:
        staff, institution = _uuid(staff_id, "staff_id"), _uuid(institution_id, "institution_id")
        params = {"as_of": as_of.isoformat()} if as_of is not None else None
        data = await self._get(token, f"/staff/{staff}/administrative-workload", params)
        if not isinstance(data, Mapping) or not isinstance(data.get("aggregate"), Mapping):
            raise AcademicDataMalformed("Workload response does not have the AdministrativeWorkloadOut shape.")
        if _uuid(data.get("staff_id"), "workload.staff_id") != staff:
            raise AcademicDataMalformed("The service returned workload for a different staff member.")
        if _uuid(data.get("institution_id"), "workload.institution_id") != institution:
            raise AcademicDataInstitutionMismatch("Workload belongs to another institution.")
        if data["aggregate"].get("status") not in ("COMPUTED", "NOT_COMPUTED"):
            raise AcademicDataMalformed("Workload aggregate.status is not COMPUTED or NOT_COMPUTED.")
        return dict(data)

    async def get_administrative_workloads(self, token: str, staff_ids: Iterable[uuid.UUID | str], *,
                                           institution_id: uuid.UUID, as_of: dt.date | None = None) -> WorkloadFetch:
        """Workload for each staff member. A 404 is recorded as missing; any other failure
        (auth, permission, outage, malformed or foreign data) fails the whole call."""
        ids = list(dict.fromkeys(str(_uuid(s, "staff_id")) for s in staff_ids))
        gate = asyncio.Semaphore(self.max_concurrency)

        async def one(staff_id: str):
            async with gate:
                try:
                    return await self.get_administrative_workload(token, staff_id, institution_id=institution_id,
                                                                  as_of=as_of)
                except AcademicDataNotFound:
                    return MissingWorkload(staff_id, "Academic Data Service returned 404 for this staff member.")

        results = await asyncio.gather(*(one(s) for s in ids), return_exceptions=True)
        for result in results:  # every request has finished; report the first failure
            if isinstance(result, BaseException):
                raise result
        return WorkloadFetch(tuple(r for r in results if isinstance(r, dict)),
                             tuple(r for r in results if isinstance(r, MissingWorkload)))

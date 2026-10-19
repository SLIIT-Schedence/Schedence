"""Turn Academic Data Service responses into Member 3 models, reusing the existing adapters.

    staff records      -> import_staff(records, eligibility policy)    (staff_source.py)
    workload records   -> admin_workload_from_records(records)         (fairness_policy.py)

Rank-based roles come only from the supplied EligibilityPolicy: there is no default that
makes everyone a Supervisor. Inactive staff are excluded by import_staff. Workload keeps
three cases apart: COMPUTED (hours), NOT_COMPUTED (hours None, with the service's reason)
and missing (no record at all). Neither of the last two becomes 0 here; Step 5's
missing_admin_hours policy decides later (REJECT, or ZERO with a reported diagnostic).
Availability, preferences and duty history do not exist in the Academic Data Service and
are left empty.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from app.duty_allocation.academic_client import AcademicDataClient, AcademicDataMalformed, MissingWorkload
from app.duty_allocation.eligibility_policy import EligibilityPolicy
from app.duty_allocation.fairness_policy import AdminWorkload, FairnessInputs, admin_workload_from_records
from app.duty_allocation.staff_source import StaffImport, import_staff
from app.scheduling.contracts import SchedulingValidationError


@dataclass(frozen=True)
class AcademicStaffData:
    staff: StaffImport  # active, role-eligible staff + excluded staff + unreadable records
    admin: tuple[AdminWorkload, ...]  # COMPUTED (hours) and NOT_COMPUTED (None) rows
    missing_workload: tuple[MissingWorkload, ...]  # staff without any workload record

    @property
    def not_computed(self) -> tuple[AdminWorkload, ...]:
        return tuple(a for a in self.admin if a.hours_per_week is None)

    def fairness_inputs(self) -> FairnessInputs:
        """Admin workload only. History and preferences have no source yet, so they stay empty."""
        return FairnessInputs(admin=self.admin)


async def load_staff(client: AcademicDataClient, token: str, *, institution_id: uuid.UUID,
                     eligibility: EligibilityPolicy) -> StaffImport:
    return import_staff(await client.list_staff(token, institution_id=institution_id), eligibility)


async def load_academic_staff_data(client: AcademicDataClient, token: str, *, institution_id: uuid.UUID,
                                   eligibility: EligibilityPolicy, include_workload: bool = True,
                                   as_of: dt.date | None = None) -> AcademicStaffData:
    """Staff (all pages) and, optionally, admin workload for the staff that were imported."""
    staff = await load_staff(client, token, institution_id=institution_id, eligibility=eligibility)
    if not include_workload:
        return AcademicStaffData(staff, (), ())
    fetch = await client.get_administrative_workloads(token, (m.staff_id for m in staff.staff),
                                                      institution_id=institution_id, as_of=as_of)
    try:
        admin = admin_workload_from_records(fetch.records)
    except SchedulingValidationError as exc:
        raise AcademicDataMalformed("Workload response values are invalid: "
                                    + "; ".join(v.message for v in exc.violations)) from None
    return AcademicStaffData(staff, admin, fetch.missing)

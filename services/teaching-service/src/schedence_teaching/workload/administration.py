"""Step 9A weekly administrative allocation/reservation assessment, without booking."""

from dataclasses import asdict, dataclass

from .contracts import AdministrativeReservationStatus


@dataclass(frozen=True)
class AdministrativeAllocationAssessment:
    allocation_minutes: int | None
    proposed_reservation_minutes: int
    approved_reservation_minutes: int
    unreserved_allocation_minutes: int | None
    reservation_status: str
    verified_completed_minutes: int
    completion_status: str
    fulfilled: bool | None
    completion_shortfall_minutes: int | None

    def to_dict(self):
        return {**asdict(self), "calendar_slots_assigned": False, "hard_blocks_generated": False}


def assess_administrative_allocation(allocation_minutes, reservations, verified_minutes, ledger_complete):
    proposed = sum(row.duration_minutes for row in reservations if row.status is AdministrativeReservationStatus.PROPOSED)
    approved = sum(row.duration_minutes for row in reservations if row.status is AdministrativeReservationStatus.APPROVED)
    if allocation_minutes is None:
        reservation_status, status, fulfilled, shortfall, unreserved = "UNSPECIFIED", "UNSPECIFIED", None, None, None
    else:
        unreserved = max(allocation_minutes - approved, 0)
        reservation_status = ("OVER_RESERVED" if approved > allocation_minutes else
                              "FULLY_RESERVED" if approved == allocation_minutes else
                              "PARTIALLY_RESERVED" if approved else "NOT_RESERVED")
        if allocation_minutes == 0:
            status, fulfilled, shortfall = "NOT_REQUIRED", True, 0
        elif verified_minutes >= allocation_minutes:
            status = "MET" if verified_minutes == allocation_minutes else "EXCEEDED"
            fulfilled, shortfall = True, 0
        elif ledger_complete:
            status, fulfilled, shortfall = "BELOW_TARGET", False, allocation_minutes - verified_minutes
        else:
            status, fulfilled, shortfall = "NOT_VERIFIED", None, None
    return AdministrativeAllocationAssessment(allocation_minutes, proposed, approved, unreserved, reservation_status,
                                               verified_minutes, status, fulfilled, shortfall)

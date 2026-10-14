"""Bridge validated Step 7A import results without manufacturing calendar evidence."""

from schedence_teaching.excel_import import VenueImportResult

from .results import SolverInputError
from .venue_inputs import VenueSchedulingInput


def venues_from_import(result: VenueImportResult, **scheduling_evidence) -> VenueSchedulingInput:
    """Retain campus provenance and known unresolved rows; calendars remain required.

    Only an explicit combined/component relationship resolves a combined-room
    warning. Names are not parsed here to invent constituent rooms or features.
    """
    if not isinstance(result, VenueImportResult) or not result.valid:
        raise SolverInputError("Venue scheduling requires a valid Step 7A import result.")
    if (result.verification is not None and
            (not result.verification_applied or result.verification.workbook_sha256 != result.source_sha256
             or result.verification.record_count != len(result.venues))):
        raise SolverInputError("Venue import confirmation does not match the validated dataset.")
    protected = {"venues", "source_workbook", "verification"} & scheduling_evidence.keys()
    if protected:
        raise SolverInputError("Imported venue records and provenance cannot be overridden in the bridge.")
    payload = {
        "venues": [venue.model_dump(round_trip=True) for venue in result.venues],
        "source_workbook": result.source_venues[0].location.workbook if result.source_venues else None,
        "verification": result.verification.model_dump(round_trip=True) if result.verification else None,
        "unresolved_venue_ids": [], **scheduling_evidence,
    }
    # Let Pydantic validate explicit relationship inputs before consulting them.
    inputs = VenueSchedulingInput.model_validate(payload)
    unresolved_rows = {(issue.workbook, issue.worksheet, issue.row) for issue in result.issues
                       if issue.code == "combined_room_booking_unknown"}
    unresolved = {venue.venue_id for row, venue in zip(result.source_venues, result.venues, strict=True)
                  if (row.location.workbook, row.location.worksheet, row.location.row) in unresolved_rows}
    resolved = {relation.combined_venue_id for relation in inputs.combined_venues}
    payload["unresolved_venue_ids"] = inputs.unresolved_venue_ids | (unresolved - resolved)
    return VenueSchedulingInput.model_validate(payload)

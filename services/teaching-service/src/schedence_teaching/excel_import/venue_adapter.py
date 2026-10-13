"""Read-only venue import with source-bound, separately supplied confirmation."""

from collections import defaultdict
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from schedence_teaching.schemas import Venue, VenueVerification

from .records import ImportIssue
from .source_reader import read_source
from .venue_config import VenueImportConfig
from .venue_records import VenueImportResult


def import_venue_excel(config: VenueImportConfig, *, base_dir: Path = Path(".")) -> VenueImportResult:
    """Preserve all raw rows; never infer availability or split combined room IDs."""
    config = VenueImportConfig.model_validate(config.model_dump(round_trip=True))
    result = VenueImportResult()
    source = config.venues
    path = source.workbook if source.workbook.is_absolute() else base_dir / source.workbook

    def issue(code, message, *, severity="error", column=None):
        result.issues.append(ImportIssue(
            severity, code, source.workbook.name, source.worksheet, None, column, message,
        ))

    try:
        result.source_sha256 = sha256(path.read_bytes()).hexdigest()
    except OSError:
        issue("unreadable_workbook", "Source workbook could not be read.")
        return result
    headers = set(config.columns.values()) | set(config.source_metadata_columns)
    result.source_venues = read_source(source, headers, result, base_dir)
    # Detect a source edit during the read before applying confirmation evidence.
    try:
        unchanged = sha256(path.read_bytes()).hexdigest() == result.source_sha256
    except OSError:
        unchanged = False
    if not unchanged:
        issue("source_changed", "Workbook changed during import; no records or verification are accepted.")
        return result

    by_id = defaultdict(list)
    parsed_rows = []
    for row in result.source_venues:
        identity = row.source_values.get(config.columns["venue_id"])
        if isinstance(identity, str) and identity.strip():
            by_id[identity].append(row)
        if row.invalid_cells:
            continue
        payload = {field: row.source_values.get(header) for field, header in config.columns.items()}
        for field in ("building", "floor", "lab_specialization"):
            value = payload[field]
            if value is None or (isinstance(value, str) and not value.strip()):
                payload[field] = None
            elif field in ("building", "floor") and value in config.unknown_location_values:
                payload[field] = None  # Raw placeholder remains unchanged in source_values.
        try:
            venue = Venue.model_validate(payload)
        except ValidationError as error:
            for detail in error.errors(include_input=False, include_url=False, include_context=False):
                field = detail["loc"][0]
                row.location.issue(result, "invalid_venue", config.columns.get(field), detail["msg"])
            continue
        parsed_rows.append((row, venue))

    duplicates = {identity for identity, rows in by_id.items() if len(rows) > 1}
    for identity in duplicates:
        for row in by_id[identity]:
            row.location.issue(result, "duplicate_venue_id", config.columns["venue_id"],
                               "Venue ID occurs more than once; all conflicting records remain in source staging only.")
    for row, venue in parsed_rows:
        if venue.venue_id in duplicates:
            continue
        result.venues.append(venue)
        for field in ("building", "floor"):
            if getattr(venue, field) is None:
                row.location.issue(result, "missing_location", config.columns[field],
                                   "No actual location value supplied; raw source value is preserved without inference.",
                                   severity="warning")
        if "+" in venue.venue_id:
            row.location.issue(result, "combined_room_booking_unknown", config.columns["venue_id"],
                               "Listed combined venue is preserved as one record; constituent-room booking relationships are not supplied.",
                               severity="warning")

    if not result.source_venues and result.valid:
        issue("empty_table", "Venue worksheet contains no data rows.", severity="warning")
    if config.verification_file:
        verification_path = (config.verification_file if config.verification_file.is_absolute()
                             else base_dir / config.verification_file)
        try:
            result.verification = VenueVerification.model_validate_json(verification_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValidationError):
            issue("invalid_verification", "Separate verification metadata could not be read or validated.")
        if result.verification:
            if result.verification.workbook_sha256 != result.source_sha256:
                issue("verification_source_mismatch", "Confirmation applies to different workbook bytes; it has not been applied.")
            elif result.verification.record_count != len(result.source_venues):
                issue("verification_count_mismatch", "Confirmation record count differs from the source row count.")
            else:
                result.verification_applied = result.valid
    else:
        issue("verification_not_supplied", "No separate campus confirmation metadata supplied.", severity="warning")

    for code, message in (
        ("venue_availability_not_supplied", "Venue availability was not supplied; no available hours are invented."),
        ("blocked_periods_not_supplied", "Venue blocked periods were not supplied; no empty blocking policy is assumed."),
    ):
        issue(code, message, severity="warning")
    return result

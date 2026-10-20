"""Venue source traceability and aggregate dry-run output."""

from collections import Counter
from dataclasses import asdict, dataclass, field

from schedence_teaching.schemas import Venue, VenueVerification

from .records import ImportIssue
from .staging import SourceRecord


@dataclass
class VenueImportResult:
    venues: list[Venue] = field(default_factory=list)
    source_venues: list[SourceRecord] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)
    verification: VenueVerification | None = None
    verification_applied: bool = False
    source_sha256: str | None = None

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def summary(self) -> dict:
        missing_locations = sum(venue.building is None or venue.floor is None for venue in self.venues)
        outstanding = [
            "Explicit venue availability; no all-day availability inferred.",
            "Explicit blocked periods; none inferred from missing input.",
            "Combined-room booking relationships and mutually exclusive resources.",
        ]
        if missing_locations:
            outstanding.append("Missing building/floor details where the source gives no actual location.")
        outstanding.append("Explicit session-to-venue eligibility policy, including lab specialization needs.")
        return {
            "valid": self.valid,
            "data_status": "campus_confirmed_development" if self.verification_applied else "development_unverified",
            "rows_read": len(self.source_venues),
            "imported_counts": {"venues": len(self.venues)},
            "venue_type_counts": dict(Counter(venue.venue_type.value for venue in self.venues)),
            "capacity_counts": dict(Counter(str(venue.capacity) for venue in self.venues)),
            "missing_location_count": missing_locations,
            "duplicate_record_count": sum(issue.code == "duplicate_venue_id" for issue in self.issues),
            "source_sha256": self.source_sha256,
            "verification_applied": self.verification_applied,
            "updated_verification": self.verification.model_dump(mode="json") if self.verification else None,
            "source_values_preserved": True,
            "issue_counts": dict(Counter(issue.code for issue in self.issues)),
            "errors": [asdict(issue) for issue in self.issues if issue.severity == "error"],
            "warnings": [asdict(issue) for issue in self.issues if issue.severity == "warning"],
            "outstanding_requirements": outstanding,
            "ready_for_scheduling": False,
            "university_ready": False,
        }

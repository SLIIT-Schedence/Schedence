"""Configurable, read-only Excel dataset import and validation."""

from .config import ImportConfig, PreferenceField, TableMapping
from .importer import import_excel
from .records import ImportIssue, ImportResult, LecturerPreferenceRecord
from .wide_adapter import import_wide_excel
from .wide_config import WideImportConfig
from .venue_adapter import import_venue_excel
from .venue_config import VenueImportConfig
from .venue_records import VenueImportResult

__all__ = [
    "ImportConfig", "ImportIssue", "ImportResult", "LecturerPreferenceRecord",
    "PreferenceField", "TableMapping", "import_excel",
    "WideImportConfig", "import_wide_excel",
    "VenueImportConfig", "VenueImportResult", "import_venue_excel",
]

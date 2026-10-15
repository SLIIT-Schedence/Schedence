"""Campus data collection, explicit CSV mapping and one-week readiness checks."""

from .templates import write_collection_pack
from .validation import PilotValidationResult, validate_collection_pack

__all__ = ["write_collection_pack", "validate_collection_pack", "PilotValidationResult"]

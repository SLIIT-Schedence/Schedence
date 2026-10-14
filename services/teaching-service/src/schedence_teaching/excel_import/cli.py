"""Dry-run command: stdout contains counts and issues, never source records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from .config import ImportConfig
from .importer import import_excel
from .records import ImportIssue, ImportResult
from .wide_adapter import import_wide_excel
from .wide_config import WideImportConfig
from .venue_adapter import import_venue_excel
from .venue_config import VenueImportConfig
from .preference_adapter import map_lecturer_preferences
from .preference_config import LecturerPreferenceMapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run .xlsx validation; no database writes.")
    parser.add_argument("--config", type=Path, required=True, help="JSON mapping file; workbook paths are relative to it.")
    parser.add_argument("--preference-config", type=Path, help="Optional soft-preference semantic mapping for wide teaching staging.")
    args = parser.parse_args(argv)
    try:
        config = TypeAdapter(ImportConfig | WideImportConfig | VenueImportConfig).validate_json(
            args.config.read_text(encoding="utf-8-sig")
        )
    except (OSError, UnicodeError, ValidationError) as error:
        result = ImportResult()
        if isinstance(error, ValidationError):
            for detail in error.errors(include_input=False, include_url=False):
                location = ".".join(str(part) for part in detail["loc"])
                result.issues.append(ImportIssue(
                    "error", "invalid_config", args.config.name, None, None, location or None,
                    detail["msg"],
                ))
        else:
            result.issues.append(ImportIssue(
                "error", "unreadable_config", args.config.name, None, None, None,
                "Mapping file could not be read as UTF-8 JSON.",
            ))
        print(json.dumps(result.summary(), indent=2))
        return 2
    if isinstance(config, VenueImportConfig):
        importer = import_venue_excel
    else:
        importer = import_wide_excel if isinstance(config, WideImportConfig) else import_excel
    result = importer(config, base_dir=args.config.resolve().parent)
    summary = result.summary()
    valid = result.valid
    if args.preference_config is not None:
        if not isinstance(config, WideImportConfig):
            parser.error("--preference-config requires the existing wide-teaching-v1 workbook layout")
        try:
            mapping = LecturerPreferenceMapping.model_validate_json(args.preference_config.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValidationError):
            summary["preference_mapping"] = {"valid": False, "error": "Preference mapping could not be read as valid UTF-8 JSON configuration."}
            print(json.dumps(summary, indent=2))
            return 2
        preferences = map_lecturer_preferences(result, mapping)
        summary["preference_mapping"] = preferences.summary()
        valid = valid and preferences.valid
    print(json.dumps(summary, indent=2))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())

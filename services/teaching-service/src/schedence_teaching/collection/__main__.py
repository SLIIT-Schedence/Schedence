"""Local CSV pack commands. No database, solver execution or network access."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from schedence_teaching.excel_import import WideImportConfig, VenueImportConfig, import_wide_excel, import_venue_excel
from schedence_teaching.integration import SchedulingSubmission

from .templates import write_collection_pack
from .validation import validate_collection_pack


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="Create a new blank pack or private source-prefilled review copy")
    create.add_argument("directory", type=Path)
    create.add_argument("--local-data", type=Path, help="Optional accepted source folder; private output must remain inside it")
    validate = subparsers.add_parser("validate", help="Read completed files and apply the existing Step 12A readiness gate")
    validate.add_argument("directory", type=Path)
    validate.add_argument("--base-submission", type=Path, required=True)
    validate.add_argument("--institution-id", type=UUID, required=True)
    validate.add_argument("--local-data", type=Path, help="Bind offering IDs to exact original module rows and preserve teams")
    validate.add_argument("--synthetic", action="store_true", help="Explicit synthetic-only fixture validation")
    args = parser.parse_args(argv)
    try:
        academic, venues = None, None
        if args.local_data:
            if args.command == "create" and not args.directory.resolve().is_relative_to(args.local_data.resolve()):
                raise ValueError("Private prefill must remain inside the supplied local-data folder.")
            academic = import_wide_excel(WideImportConfig.model_validate_json(
                (args.local_data / "department-aligned.mapping.json").read_text(encoding="utf-8")), base_dir=args.local_data)
            if not academic.valid:
                raise ValueError("Source import is invalid.")
            if args.command == "create":
                venues = import_venue_excel(VenueImportConfig.model_validate_json(
                    (args.local_data / "venues.mapping.json").read_text(encoding="utf-8")), base_dir=args.local_data)
                if not venues.valid:
                    raise ValueError("Venue import is invalid.")
        if args.command == "create":
            counts = write_collection_pack(args.directory, academic_source=academic, venue_source=venues)
            print(json.dumps(dict(created=True, prefilled=academic is not None, row_counts=counts), indent=2))
            return 0
        base = SchedulingSubmission.model_validate_json(args.base_submission.read_text(encoding="utf-8-sig"))
        result = validate_collection_pack(args.directory, base, expected_institution_id=args.institution_id,
            actual_campus=not args.synthetic, academic_source=academic)
        report = result.readiness.to_dict()
        report["diagnostics"] = [row for row in report["diagnostics"] if row["status"] != "CONFIRMED"]
        print(json.dumps(report, indent=2))
        return 0 if result.readiness.ready_for_generation else 1
    except (OSError, ValueError, ValidationError):
        # Do not echo exceptions containing source records or private file content.
        print(json.dumps(dict(ready_for_generation=False, error="Collection command failed: check file access, CSV/base contract, source import validity and output-directory restrictions. No existing files were overwritten.")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

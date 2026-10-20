"""Validate synthetic completed templates in a temporary folder; never run CP."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pilot_collection_fixture import write_synthetic_pilot
from schedence_teaching.collection import validate_collection_pack


def main():
    with TemporaryDirectory(prefix="schedence-synthetic-pilot-") as temporary:
        directory = Path(temporary)
        base, institution = write_synthetic_pilot(directory)
        result = validate_collection_pack(directory, base, expected_institution_id=institution, actual_campus=False)
        report = result.readiness.to_dict()
        report["diagnostics"] = [row for row in report["diagnostics"] if row["status"] != "CONFIRMED"]
        print(json.dumps(dict(example="synthetic-only-pilot-collection", solver_called=False, readiness=report), indent=2))
        if not result.readiness.ready_for_generation:
            raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Read-only-source semester comparisons using reviewed elective registrations."""

import argparse
from pathlib import Path

from .__main__ import LOCAL_DATA, SERVICE_ROOT
from .semester_inputs import load_registered_snapshot
from .semester_scenarios import SemesterScenarioSettings, run_semester_scenarios


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=LOCAL_DATA / "pilot-previews/step12a33-resumed-reviewed")
    parser.add_argument("--config", type=Path, default=SERVICE_ROOT / "examples/pp1-semester-scenarios.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.output.resolve().is_relative_to(LOCAL_DATA.resolve()) or args.output.exists():
        parser.error("Use a new output directory inside ignored Teaching Service local-data.")
    try:
        settings = SemesterScenarioSettings.model_validate_json(args.config.read_text(encoding="utf-8-sig"))
        snapshot = load_registered_snapshot(args.snapshot)
        results = run_semester_scenarios(snapshot, settings, args.output)
        return 0 if all(row["known_session_scope_status"]=="COMPLETE" for row in results) else 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Scenario input/export failed ({type(error).__name__}); no source workbooks were modified.", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

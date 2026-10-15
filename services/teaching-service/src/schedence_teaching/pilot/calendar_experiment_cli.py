"""Test only the explicitly authorized 18:30 Weekend Monday online window."""

import argparse
from pathlib import Path

from .__main__ import LOCAL_DATA, SERVICE_ROOT
from .calendar_experiment import CalendarExperiment, run_experiment
from .semester_inputs import load_registered_snapshot


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot",type=Path,default=LOCAL_DATA/"pilot-previews/step12a33-resumed-reviewed")
    parser.add_argument("--config",type=Path,default=SERVICE_ROOT/"examples/pp1-experimental-calendar.json")
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    if not args.output.resolve().is_relative_to(LOCAL_DATA.resolve()) or args.output.exists():
        parser.error("Use a new private output directory; earlier artifacts are preserved.")
    try:
        settings=CalendarExperiment.model_validate_json(args.config.read_text(encoding="utf-8-sig"))
        baseline=load_registered_snapshot(args.snapshot)
        summaries=run_experiment(baseline,settings,args.output)
        return 0 if all(row["supported_scope_status"] in {"COMPLETE_SUPPORTED_SCOPE","COMPLETE_SELECTED_COHORT"} for row in summaries) else 1
    except (OSError,ValueError,KeyError,TypeError) as error:
        print(f"Experiment failed ({type(error).__name__}); no source workbooks or previous outputs were modified.",flush=True)
        return 2


if __name__=="__main__":raise SystemExit(main())

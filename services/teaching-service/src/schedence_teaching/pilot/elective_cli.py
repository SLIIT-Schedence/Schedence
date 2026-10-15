"""Year 4 explicit enrollment and coordinated online/physical PP1 regeneration."""

import argparse
import json
from pathlib import Path
from math import isfinite

from .__main__ import LOCAL_DATA, SERVICE_ROOT, load_reference_sources
from .allocations import import_specialization_allocations
from .elective_config import ElectiveRunConfiguration
from .elective_io import read_classifications, read_selections
from .electives import ElectiveAllocationPolicy, ElectiveInputError, confirmed_year4_candidate_catalog
from .elective_run import run_elective_previews
from .online_calendar import configure_online_days
from .pp1 import PP1Configuration, prepare_pp1
from .students import import_student_counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=SERVICE_ROOT / "examples" / "pp1-test-calendar.json")
    parser.add_argument("--run-config",type=Path,default=SERVICE_ROOT / "examples" / "pp1-elective-test-settings.json")
    parser.add_argument("--classifications", type=Path)
    parser.add_argument("--registrations", type=Path)
    parser.add_argument("--registration-status", choices=("PP1_TEST_ELECTIVE_ALLOCATION", "SUPPLIED_ELECTIVE_REGISTRATIONS"), default="SUPPLIED_ELECTIVE_REGISTRATIONS")
    parser.add_argument("--allocation-policy", type=Path)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--weekday-online-day")
    parser.add_argument("--weekend-online-day")
    parser.add_argument("--seconds-per-scope", type=float)
    args = parser.parse_args(argv)
    if not args.output.resolve().is_relative_to(LOCAL_DATA.resolve()) or args.output.exists():
        parser.error("Use a new output directory inside ignored Teaching Service local-data.")
    if args.seconds_per_scope is not None and (not isfinite(args.seconds_per_scope) or args.seconds_per_scope <= 0):
        parser.error("Scope search budget must be finite and positive.")
    try:
        original = import_student_counts(LOCAL_DATA / "Schedence_Student_Groups_One_Sheet_Editable.xlsx")
        allocations = import_specialization_allocations(LOCAL_DATA / "Schedence_Specialization_Student_Counts_Proportional_Editable.xlsx", original)
        academic, venues = load_reference_sources(LOCAL_DATA)
        settings = ElectiveRunConfiguration.model_validate_json(args.run_config.read_text(encoding="utf-8-sig"))
        if args.weekday_online_day:settings.weekday_online_day=args.weekday_online_day
        if args.weekend_online_day:settings.weekend_online_day=args.weekend_online_day
        if args.seconds_per_scope is not None:
            settings.scope_solve_seconds=args.seconds_per_scope
            settings.global_solve_seconds=args.seconds_per_scope
        physical = PP1Configuration.model_validate_json(args.config.read_text(encoding="utf-8-sig"))
        configuration = configure_online_days(physical, weekday_online_day=settings.weekday_online_day,
            weekend_online_day=settings.weekend_online_day, weekend_online_window=settings.weekend_online_window,
            weekday_online_window=settings.weekday_online_window)
        configuration.solver_config.max_time_seconds = settings.global_solve_seconds
        base = prepare_pp1(allocations, academic, venues, configuration)
        classes = confirmed_year4_candidate_catalog(academic,reference=settings.catalog_classification_reference,
            overrides=read_classifications(args.classifications) if args.classifications else ())
        policy = (ElectiveAllocationPolicy.model_validate_json(args.allocation_policy.read_text(encoding="utf-8-sig"))
                  if args.allocation_policy else settings.allocation_policy)
        if args.seed is not None:policy=policy.model_copy(update=dict(random_seed=args.seed))
        policy=ElectiveAllocationPolicy.model_validate(policy.model_dump())
        settings.allocation_policy=policy
        supplied = read_selections(args.registrations,base.plan.cohorts,label=args.registration_status) if args.registrations else None
        summary=run_elective_previews(base,academic,classes,policy,settings,args.output,selections=supplied,
            allocation_label=args.registration_status if supplied is not None else "PP1_TEST_ELECTIVE_ALLOCATION")
        print(json.dumps({key:summary[key] for key in ("known_supported_occurrences","year4_registered_students","blocked_cohort_student_sum","research_pending_cohort_count","full_global_academic_timetable_generated")}),flush=True)
        return 0 if summary.get("all_supported_known_sessions_scheduled") else 1
    except ElectiveInputError as error:
        print(json.dumps(dict(status="INVALID_ELECTIVE_INPUT",diagnostics=error.diagnostics)),flush=True)
        return 2
    except (OSError,ValueError,KeyError,TypeError) as error:
        print(json.dumps(dict(status="INPUT_OR_PREPARATION_ERROR",error_type=type(error).__name__,source_workbooks_modified=False)),flush=True)
        return 2


if __name__=="__main__":
    raise SystemExit(main())

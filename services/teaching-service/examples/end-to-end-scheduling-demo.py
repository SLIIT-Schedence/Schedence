"""Run all existing scheduling modes together using synthetic inputs only."""

import json
from pathlib import Path
import runpy

from schedence_teaching.lunch import assess_student_lunch, confirmed_student_lunch_policy
from schedence_teaching.solver import SolverStatus


def main():
    fixture = runpy.run_path(str(Path(__file__).with_name("integration_fixture.py")))
    case = fixture["build_case"]()
    result, benchmark = fixture["run_case"](case)
    output = dict(example="synthetic-end-to-end-integration", benchmark=benchmark,
        timetable=fixture["summarize"](result, case))
    if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        calendar = case["inputs"]["consecutive_calendar"]
        lunch_policy = confirmed_student_lunch_policy(policy_reference="synthetic:confirmed-year-specific-lunch-periods",
            week_reference=calendar.week_reference,
            provenance=dict(source="project-owner-confirmed-periods", reference="year1-2:1230-1330;year3-4:1330-1430"),
            applicable_dates=set(calendar.day_dates.values()), applicable_streams={"WEEKDAY", "WEEKEND"},
            applicability_reference="synthetic:example-only-date-stream-scope-not-campus-confirmation")
        lunch = assess_student_lunch(case["prepared"], case["config"], result, calendar, lunch_policy, **case["inputs"])
        output["student_lunch_assessment"] = lunch.to_dict()
        output["student_lunch_note"] = (
            "Fixed periods are confirmed; this example's date/stream scope is synthetic. "
            "Step 11D.1 reports lunch conflicts without rescheduling or enforcing CP-SAT lunch constraints.")
    else:
        output["student_lunch_assessment"] = None
    print(json.dumps(output, indent=2, default=str))
    return output


if __name__ == "__main__":
    main()

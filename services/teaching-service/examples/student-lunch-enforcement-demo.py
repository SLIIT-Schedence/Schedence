"""Before/after hard fixed student lunch, preserving the historical Step 11C fixture."""

import json
from pathlib import Path
import runpy

from schedence_teaching.lunch import assess_student_lunch, confirmed_student_lunch_policy
from schedence_teaching.solver import SolverStatus, StudentLunchEnforcementConfig, validate_student_lunch_result


def main():
    fixture = runpy.run_path(str(Path(__file__).with_name("integration_fixture.py")))
    case = fixture["build_case"](time_limit=5.0)
    original = dict(case["inputs"])
    calendar = original["consecutive_calendar"]
    policy = confirmed_student_lunch_policy(policy_reference="synthetic:confirmed-fixed-student-lunch",
        week_reference=calendar.week_reference,
        provenance=dict(source="project-owner-confirmed-periods", reference="year1-2:1230-1330;year3-4:1330-1430"),
        applicable_dates=set(calendar.day_dates.values()), applicable_streams={"WEEKDAY", "WEEKEND"},
        applicability_reference="synthetic:explicit-example-date-stream-scope")
    lunch = dict(student_lunch_policy=policy, student_lunch_calendar=calendar,
        student_lunch_config=StudentLunchEnforcementConfig(applicability_confirmation="CONFIRMED",
            applicability_confirmation_reference="synthetic:confirmed-example-only-not-real-campus-applicability"))
    before, before_stats = fixture["run_case"](case)
    enabled_case = {**case, "inputs": {**original, **lunch}}
    after, after_stats = fixture["run_case"](enabled_case)
    validate_student_lunch_result(case["prepared"], case["config"], after, **enabled_case["inputs"])
    complete = {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}

    def summary(result, stats, inputs):
        output = dict(benchmark=stats, timetable=fixture["summarize"](result, case), lunch=None)
        if result.status in complete:
            report = assess_student_lunch(case["prepared"], case["config"], result, calendar, policy, **inputs)
            output["lunch"] = report.to_dict(include_group_details=True)
        return output

    baseline, enabled = summary(before, before_stats, original), summary(after, after_stats, enabled_case["inputs"])
    preserved = None
    if before.status in complete and after.status in complete:
        expected = {row.occurrence_id for row in case["prepared"].sessions}
        actual = [row.occurrence_id for delivery in after.schedule for row in delivery.covered_sessions]
        preserved = len(actual) == len(expected) and set(actual) == expected
        if not preserved or enabled["lunch"]["compliant"] is not True:
            raise AssertionError("Enabled timetable failed exact coverage or independent lunch validation.")
    output = dict(example="synthetic-hard-student-lunch-before-after", synthetic=True, university_ready=False,
        applicability_confirmation_is_synthetic=True, historical_fixture_modified=False,
        baseline=baseline, lunch_enabled=enabled, original_occurrences_preserved=preserved,
        model_variable_delta=after_stats["maximum_model_variables"]-before_stats["maximum_model_variables"],
        model_constraint_delta=after_stats["maximum_model_constraints"]-before_stats["maximum_model_constraints"],
        objective_order_unchanged=(before.consecutive_report.priority_order == after.consecutive_report.priority_order)
            if before.consecutive_report and after.consecutive_report else None,
        independently_validated=after.status in complete)
    print(json.dumps(output, indent=2, default=str))
    return output


if __name__ == "__main__":
    main()

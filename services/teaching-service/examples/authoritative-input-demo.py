"""Run with the existing venv: python examples/authoritative-input-demo.py."""

from collections import Counter
import json

from authoritative_input_fixture import INSTITUTION, build_submission
from schedence_teaching.integration import run_scheduling


def main():
    output = run_scheduling(build_submission(), expected_institution_id=INSTITUTION, actual_campus=False)
    result = output.timetable
    readiness = output.readiness.to_dict()
    # Keep the console example concise; the API report retains confirmed scopes.
    readiness["diagnostics"] = [row for row in readiness["diagnostics"] if row["status"] != "CONFIRMED"]
    report = dict(example="synthetic-authoritative-input-integration", university_ready=False,
        readiness=readiness, independently_validated=output.independently_validated,
        solver_status=result.status.value if result else None)
    if result and output.independently_validated:
        coverage = Counter(item.occurrence_id for row in result.schedule for item in row.covered_sessions)
        report.update(original_occurrences=len(coverage), exact_once_coverage=all(count == 1 for count in coverage.values()),
            physical_deliveries=len(result.schedule), combined_deliveries=result.validation.combined_deliveries,
            lecturer_contact_minutes=result.validation.lecturer_contact_minutes,
            objective_order=list(result.consecutive_report.priority_order),
            independent_checks=list(result.validation.checks))
    print(json.dumps(report, indent=2))
    if not output.readiness.ready_for_generation or not output.independently_validated:
        raise SystemExit("Synthetic integration did not produce an independently validated timetable.")
    return report


if __name__ == "__main__":
    main()

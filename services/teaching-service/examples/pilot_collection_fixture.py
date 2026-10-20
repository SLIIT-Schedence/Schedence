"""Explicitly synthetic completed CSVs; never used as a campus fallback."""

import csv
from pathlib import Path
import runpy

from schedence_teaching.collection import write_collection_pack
from schedence_teaching.collection.specification import (
    GROUP_FIELDS, OFFERING_FIELDS, POLICY_KEYS, REQUIREMENT_FIELDS, TABLES, headers,
)


def write_synthetic_pilot(directory):
    fixture = runpy.run_path(str(Path(__file__).with_name("authoritative_input_fixture.py")))
    base = fixture["build_submission"]()
    raw = base.inputs
    teaching = raw["combination_input"]["teaching_input"]
    write_collection_pack(directory)
    rows = {key: [] for key in TABLES}
    for record in teaching["module_offerings"]:
        rows["offerings"].append({field.header: record.get(field.field, "") for field in OFFERING_FIELDS})
    for record in teaching["student_groups"]:
        row = {field.header: record.get(field.field, "") for field in GROUP_FIELDS}
        row["Specialization"] = raw["combination_input"]["group_specializations"][record["group_id"]]
        rows["student-groups"].append(row)
    permissions = {row["requirement_id"]: row for row in raw["combination_input"]["requirement_permissions"]}
    for record in teaching["session_requirements"]:
        record = {**record, **permissions.get(record["requirement_id"], {})}
        rows["session-requirements"].append({field.header: record.get(field.field, "") for field in REQUIREMENT_FIELDS})
    for record in raw["combination_input"]["population_evidence"]:
        rows["population-evidence"].append({"Group IDs": ";".join(record["group_ids"]),
            "Relationship": record["relationship"], "Population Evidence Reference": record["evidence_reference"]})
    for record in raw["staff_input"]["workload_input"]["profiles"]:
        rows["staff-crosswalk"].append({"Staff ID": record["staff_id"],
            "Academic Staff UUID": str(raw["academic_staff_references"][record["staff_id"]]),
            "Workload Band": record["workload_band"], "Appointment": record["appointment"]})

    def time(value):
        return f"{value//60:02}:{value%60:02}"

    def periods(values):
        return ";".join(f"{time(row['start_minute'])}-{time(row['end_minute'])}" for row in values) or "NONE"

    days = [row["day"] for row in raw["solver_config"]["days"]]
    week = raw["calendar"]["week_reference"]
    for record in raw["staff_input"]["availability"]:
        for day in days:
            rows["staff-availability"].append({"Staff ID": record["staff_id"], "Academic Week": week,
                "Day ID": day, "Teaching Allowed": "TRUE" if day in record["allowed_teaching_days"] else "FALSE",
                "Available Windows": periods(record["available_windows"].get(day, [])),
                "Blocked Periods": periods([row for row in record["blocked_periods"] if row["day"] == day])})
    for record in raw["venue_input"]["calendars"]:
        for day in days:
            rows["venue-calendars"].append({"Venue ID": record["venue_id"], "Academic Week": week, "Day ID": day,
                "Available Windows": periods(record["available_windows"][day]),
                "Blocked Periods": periods([row for row in record["blocked_periods"] if row["day"] == day])})
    references = dict(workload=raw["staff_input"]["workload_input"]["policy"]["policy_reference"],
        preferences=raw["preference_policy"]["policy_reference"], fairness=raw["fairness_policy"]["policy_reference"],
        consecutive=raw["consecutive_policy"]["policy_reference"], student_lunch=raw["student_lunch_policy"]["policy_reference"])
    for key in POLICY_KEYS:
        row = {"Policy Key": key, "Decision": "ALLOW" if key == "combination" else "NONE" if key == "venue_relationships" else "APPLY",
               "Policy Reference": references.get(key, f"synthetic:approved-{key}")}
        if key == "student_lunch":
            row.update({"Applicable Dates": ";".join(str(day) for day in sorted(raw["calendar"]["day_dates"].values())),
                        "Applicable Streams": "WEEKDAY;WEEKEND"})
        rows["policy-confirmations"].append(row)
    for table, records in rows.items():
        with (Path(directory) / f"{table}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers(table), lineterminator="\n")
            writer.writeheader()
            for row in records:
                row.update({"Confirmation Status": "CONFIRMED", "Confirmation Reference": "synthetic:approval-only",
                    "Source": "synthetic-example-only", "Source Reference": "synthetic:collection-fixture",
                    "Valid From": str(raw["calendar"]["week_start"]), "Valid Until": str(raw["calendar"]["week_end"])})
                writer.writerow({key: "" if value is None else str(value) for key, value in row.items()})
    return base, fixture["INSTITUTION"]

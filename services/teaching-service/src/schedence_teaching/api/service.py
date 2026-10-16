"""Authorization-aware projections. Never return snapshots, raw reports or rosters."""

from datetime import date

from schedence_teaching.preparation import build_group_hierarchy
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.snapshots import content_digest
from schedence_teaching.solver.attendance_inputs import mode_on_day

from .contracts import CalendarOut, IssueOut, Page, ScopeOut, SessionOut, TimetableOut, ValidationOut
from .errors import ApiError


def effective_filters(access, query):
    result = dict(years=(query.academic_year,) if query.academic_year else (),
        semesters=(query.semester,) if query.semester else (), streams=(query.stream.value,) if query.stream else (),
        specializations=(query.specialization,) if query.specialization else (), groups=(query.student_group,) if query.student_group else (),
        lecturer=query.lecturer, venue=query.venue, lifecycle_status=query.lifecycle_status.value if query.lifecycle_status else None)
    if access.mode == "ADMIN":
        return result
    binding = access.binding
    for name in ("years", "semesters", "streams", "specializations"):
        allowed = getattr(binding, {"years": "academic_years"}.get(name, name))
        if result[name] and allowed and not set(result[name]) <= allowed:
            raise ApiError(403, "ACADEMIC_SCOPE", "The requested academic filter is outside your scope.")
        if not result[name] and allowed:
            result[name] = tuple(sorted(allowed))
    if access.mode == "LECTURER":
        if query.lecturer is not None and query.lecturer != binding.staff_id:
            raise ApiError(403, "PERSONAL_SCOPE", "Only your authorized personal timetable may be requested.")
        result["lecturer"] = binding.staff_id
    else:
        if query.student_group and query.student_group not in binding.group_ids:
            raise ApiError(403, "ACADEMIC_SCOPE", "The requested student group is outside your scope.")
        result["groups"] = (query.student_group,) if query.student_group else tuple(sorted(binding.group_ids))
    if not access.personal_drafts:
        if query.lifecycle_status is not None and query.lifecycle_status.value != "PUBLISHED":
            raise ApiError(403, "UNPUBLISHED_SCOPE", "Unpublished timetable access is not permitted.")
        result["lifecycle_status"] = "PUBLISHED"
    return result


def published(record):
    return (record.version["lifecycle_status"] == "PUBLISHED" and record.logical_status == "PUBLISHED"
        and record.version["academic_complete"] and record.validation["passed"] and record.validation["hard_violations"] == 0
        and not record.limitations["research_pending"] and not record.limitations["blocked_scopes"]
        and not record.limitations["experimental_calendar"] and not record.limitations["testing_inputs"]
        and not any(row.severity == "BLOCKER" for row in record.document.issues))


def selected_bookings(record, access, filters):
    if access.mode != "ADMIN" and not access.personal_drafts and not published(record):
        return ()
    if filters["lifecycle_status"] and record.logical_status != filters["lifecycle_status"]:
        return ()
    hierarchy = build_group_hierarchy(record.document.teaching_input.student_groups)
    allowed_groups = set(hierarchy.parents)
    if filters["groups"]:
        requested = set(filters["groups"]) & hierarchy.parents.keys()
        if access.mode == "STUDENT":
            # Membership in a parent does not identify a student's lab subgroup,
            # and partial population overlap does not prove personal attendance.
            allowed_groups = set(requested)
            for group in requested:
                parent = hierarchy.parents[group]
                while parent is not None:
                    allowed_groups.add(parent)
                    parent = hierarchy.parents[parent]
        else:
            allowed_groups = {candidate for candidate in hierarchy.parents
                if any(hierarchy.conflicts(group, candidate) for group in requested)}
        # Explicit elective populations can establish attendance overlap; unknown
        # sibling overlap is never guessed.
        rosters = {row.group_id: set(row.student_ids) for row in record.document.solver_config.attendance_memberships}
        if access.mode != "STUDENT":
            for group in requested:
                allowed_groups.update(candidate for candidate in hierarchy.parents
                    if rosters.get(group, set()) & rosters.get(candidate, set()))
    result = []
    for booking in record.deliveries:
        if filters["lecturer"] and booking["staff_reference"] != filters["lecturer"]:
            continue
        if filters["venue"] and booking["venue_reference"] != filters["venue"]:
            continue
        coverage = [item for item in booking["coverage"] if item["group_reference"] in allowed_groups
            and all(not filters[key] or item[field] in filters[key] for key, field in (
                ("years", "academic_year"), ("semesters", "semester"), ("streams", "stream"), ("specializations", "specialization")))]
        if coverage:
            result.append(dict(booking, coverage=coverage))
    return tuple(result)


def issues(record):
    return [IssueOut(code=row.issue_code, severity=row.severity, scope_key=row.scope_key) for row in record.document.issues]


def summary(record, access, filters):
    doc, version = record.document, record.version
    from schedence_teaching.persistence.policy import verify_version_policy_metadata
    resolved_policy = verify_version_policy_metadata(doc, record.run['result_metadata'].get('_teaching_persistence', {}))
    bookings = selected_bookings(record, access, filters)
    covered_groups = {row["group_reference"] for booking in bookings for row in booking["coverage"]}
    scopes = []
    for row in doc.scopes:
        if not all(not filters[key] or value in filters[key] for key, value in (("years", row.academic_year),
            ("semesters", row.semester), ("streams", row.stream.value), ("specializations", row.specialization))):
            continue
        groups = sorted(set(row.group_references) & covered_groups)
        if access.mode != "ADMIN" and not groups:
            continue
        if filters["groups"] and not groups:
            continue
        scopes.append(ScopeOut(academic_year=row.academic_year, semester=row.semester, stream=row.stream.value,
            specialization=row.specialization, student_groups=groups, scope_status=row.scope_status))
    registration_source = next((row for row in doc.sources if row.label == "PP1:synthetic-elective-registration-evidence"), None)
    registration = registration_source.provenance if registration_source else {}
    metadata = record.run["result_metadata"].get("_teaching_persistence", {})
    policy_version = (resolved_policy.revision if resolved_policy else
        metadata.get("policy_version") or ("sha256:" + registration_source.sha256 if registration_source else None))
    if resolved_policy is not None and metadata.get('resolved_policy_digest') != resolved_policy.digest:
        raise ValueError('Version resolved policy digest differs from its snapshot.')
    return TimetableOut(institution_id=access.institution_id, version_id=version["id"], series_id=version["series_id"], version_number=version["version_number"],
        lifecycle_status=record.logical_status, storage_status=version["lifecycle_status"], solver_status=record.run["solver_status"],
        validation_status="PASSED" if record.validation["passed"] else "FAILED", academic_complete=version["academic_complete"],
        matched_occurrences=sum(len(row["coverage"]) for row in bookings), scopes=scopes,
        calendar=CalendarOut(label=doc.calendar_label, week_reference=doc.calendar.week_reference,
            week_start=doc.calendar.week_start, week_end=doc.calendar.week_end, timezone=doc.timezone_name,
            synthetic_data=doc.calendar.synthetic_data, sha256=content_digest(doc.calendar),
            teaching_windows=[dict(day=day.day, teaching_date=doc.calendar.day_dates[day.day].isoformat(), stream=stream.value,
                start_minute=window.start_minute, end_minute=window.end_minute,
                delivery_mode=mode_on_day(doc.solver_config, day.day, stream).value)
                for day in doc.solver_config.days for stream, windows in day.windows.items() for window in windows]),
        input_snapshot_id=record.run["snapshot_id"] if access.mode == "ADMIN" else None,
        policy_version=policy_version if access.mode == "ADMIN" else None,
        resolved_policy_digest=resolved_policy.digest if resolved_policy and access.mode == 'ADMIN' else None,
        institution_policy_revision=resolved_policy.revision if resolved_policy and access.mode == 'ADMIN' else None,
        policy_version_kind=("RESOLVED_INSTITUTION_POLICY" if resolved_policy else "STORED_POLICY_VERSION" if metadata.get("policy_version") else
            "SNAPSHOT_REGISTRATION_EVIDENCE_SHA256") if access.mode == "ADMIN" and policy_version else None,
        policy_reference=(resolved_policy.policy_reference if resolved_policy else registration.get("policy", {}).get("reference") or registration.get("policy", {}).get("policy_reference"))
            if access.mode == "ADMIN" else None,
        policy_sources=[dict(label=row.label, sha256=row.sha256, provenance_kind=row.provenance_kind.value) for row in doc.sources]
            if access.mode == "ADMIN" else None,
        unresolved_issues=issues(record) if access.mode == "ADMIN" else None,
        full_scope_expected_occurrences=record.run["expected_occurrences"] if access.mode == "ADMIN" else None)


def session_out(record, booking):
    doc = record.document
    staff = next(row for row in doc.teaching_input.staff_members if row.staff_id == booking["staff_reference"])
    venues = doc.contexts.venue_input.venues if doc.contexts.venue_input is not None else ()
    venue = next((row for row in venues if row.venue_id == booking["venue_reference"]), None)
    modules = {row.offering_id: row for row in doc.teaching_input.module_offerings}
    teaching_date = date.fromisoformat(str(booking["teaching_date"]))
    return SessionOut(delivery_reference=booking["delivery_reference"], teaching_date=booking["teaching_date"],
        day=teaching_date.strftime("%A").upper(),
        start_minute=booking["start_minute"], end_minute=booking["end_minute"],
        start_time=f"{booking['start_minute']//60:02d}:{booking['start_minute']%60:02d}",
        end_time=f"{booking['end_minute']//60:02d}:{booking['end_minute']%60:02d}", delivery_mode=booking["delivery_mode"],
        lecturer=dict(staff_id=staff.staff_id, staff_name=staff.staff_name),
        venue={key: getattr(venue, key) for key in ("venue_id", "venue_name", "venue_type", "capacity", "building", "floor", "lab_specialization")} if venue else None,
        coverage=[dict(occurrence_id=row["occurrence_reference"], requirement_id=row["requirement_reference"],
            offering_id=row["offering_reference"], offering_identifier_status="SNAPSHOT_LOCAL_REFERENCE",
            module_code=modules[row["offering_reference"]].module_code, module_name=modules[row["offering_reference"]].module_name,
            group_id=row["group_reference"], session_type=row["session_type"], duration_minutes=row["duration_minutes"],
            academic_year=row["academic_year"], semester=row["semester"], stream=row["stream"], specialization=row["specialization"])
            for row in booking["coverage"]])


class TimetableReadService:
    def __init__(self, store):
        self.store = store

    async def page(self, access, query, *, series_id=None):
        filters = effective_filters(access, query)
        async with self.store.read(access.institution_id) as repository:
            records, total = await repository.find_timetables(**filters, series_id=series_id,
                offset=(query.page-1)*query.page_size, limit=query.page_size)
            visible = [row for row in records if access.mode == "ADMIN" or selected_bookings(row, access, filters)]
            return Page[TimetableOut](items=[summary(row, access, filters) for row in visible],
                total=total if len(visible) == len(records) else len(visible), page=query.page, page_size=query.page_size)

    async def get(self, access, version_id, query):
        filters = effective_filters(access, query)
        try:
            async with self.store.read(access.institution_id) as repository:
                record = await repository.get_timetable(version_id)
        except PersistenceConflict:
            raise ApiError(404, "NOT_FOUND", "The requested timetable was not found.") from None
        if access.mode != "ADMIN" and not selected_bookings(record, access, filters):
            raise ApiError(404, "NOT_FOUND", "The requested timetable was not found.")
        return record, filters

    async def detail(self, access, version_id, query):
        record, filters = await self.get(access, version_id, query)
        return summary(record, access, filters)

    async def sessions(self, access, version_id, query):
        record, filters = await self.get(access, version_id, query)
        rows = selected_bookings(record, access, filters)
        start = (query.page-1)*query.page_size
        return Page[SessionOut](items=[session_out(record, row) for row in rows[start:start+query.page_size]],
            total=len(rows), page=query.page, page_size=query.page_size)

    async def validation(self, access, version_id, query):
        if access.mode != "ADMIN":
            raise ApiError(403, "FORBIDDEN", "Institutional validation reports require Teaching administrative read access.")
        record, _ = await self.get(access, version_id, query)
        report = record.coverage_report()
        return ValidationOut(version_id=version_id, status="PASSED" if record.validation["passed"] else "FAILED",
            expected_occurrences=report["expected_original_occurrences"], covered_occurrences=report["covered_original_occurrences"],
            actual_deliveries=report["actual_deliveries"], unscheduled_occurrences=report["unscheduled_occurrences"],
            hard_violations=report["hard_violations"], academic_complete=record.version["academic_complete"],
            unresolved_issues=issues(record), blocked_scopes=[dict(academic_year=row["academic_year"], semester=row["semester"],
                stream=row["stream"], specialization=row["specialization"], student_count=row.get("student_count"),
                scope_status=row["scope_status"]) for row in record.limitations["blocked_scopes"]],
            research_pending=record.limitations["research_pending"], experimental_calendar=record.limitations["experimental_calendar"])

    async def versions(self, access, version_id, query):
        if access.mode != "ADMIN":
            raise ApiError(403, "FORBIDDEN", "Private version history requires Teaching administrative read access.")
        record, _ = await self.get(access, version_id, query)
        return await self.page(access, query, series_id=record.version["series_id"])

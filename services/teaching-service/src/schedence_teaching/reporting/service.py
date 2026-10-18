"""Existing tenant RLS and database-enforced read-only transaction boundary."""
import asyncio

from schedence_teaching.api.errors import ApiError
from schedence_teaching.persistence.ports import PersistenceConflict
from .workload import lecturer_workload


class WorkloadReadService:
    def __init__(self,store):self.store=store

    async def report(self,access,query):
        if access.mode!='ADMIN':raise ApiError(403,'FORBIDDEN','Institution-wide Teaching read permission is required for workload reports.')
        async with self.store.read(access.institution_id) as repository:
            try:record=await repository.get_timetable(query.version_id)
            except PersistenceConflict:raise ApiError(404,'NOT_FOUND','Timetable version not found.') from None
        if record.document.institution_id!=access.institution_id:
            raise ApiError(404,'NOT_FOUND','Timetable version not found.')
        if query.lecturer and query.lecturer not in {s.staff_id for s in record.document.teaching_input.staff_members}:
            raise ApiError(404,'NOT_FOUND','Staff member not found in this timetable version.')
        scoped=query.model_copy(update=dict(institution_id=access.institution_id))
        try:return await asyncio.to_thread(lecturer_workload,record,scoped)
        except (PersistenceConflict,ValueError):raise ApiError(409,'REPORT_INPUT_INVALID','Stored workload reporting inputs could not be reconciled.') from None

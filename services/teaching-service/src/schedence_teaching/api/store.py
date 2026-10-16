"""Database read adapter; only the existing read-only, RLS-checked facade is used."""

from contextlib import asynccontextmanager

from schedence_teaching.persistence.timetable_service import timetable_reader


class DatabaseTimetableStore:
    def __init__(self, factory):
        self.factory = factory

    @asynccontextmanager
    async def read(self, institution_id):
        async with timetable_reader(self.factory, institution_id) as repository:
            yield repository

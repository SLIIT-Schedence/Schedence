"""Values accepted by the teaching timetable input contracts."""

from enum import StrEnum


class StaffType(StrEnum):
    ACADEMIC = "ACADEMIC"
    INSTRUCTOR = "INSTRUCTOR"


class Stream(StrEnum):
    WEEKDAY = "WEEKDAY"
    WEEKEND = "WEEKEND"


class TeachingRole(StrEnum):
    LIC = "LIC"
    CO_LIC = "CO_LIC"
    TEAM_MEMBER = "TEAM_MEMBER"


class SessionType(StrEnum):
    LECTURE = "LECTURE"
    TUTORIAL = "TUTORIAL"
    WORKSHOP = "WORKSHOP"
    LAB = "LAB"

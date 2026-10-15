"""Explicit PP1 online-day settings; no campus day or extra physical hours inferred."""

from schedence_teaching.schemas import SessionType, Stream
from schedence_teaching.schemas.time import TimeWindow

from .pp1 import PP1Configuration


def configure_online_days(configuration, *, weekday_online_day, weekend_online_day,
                          weekend_online_window, weekday_online_window=None):
    raw = configuration.model_dump()
    if configuration.solver_config.delivery_calendar is not None:
        raise ValueError("Supply the physical baseline configuration; do not stack online-calendar overrides.")
    dates = configuration.calendar.day_dates
    if any(day not in dates or dates[day].weekday() >= 5 for day in (weekday_online_day, weekend_online_day)):
        raise ValueError("Both configured online lecture days must be actual weekdays in the teaching week.")
    weekend_window = TimeWindow.model_validate(weekend_online_window.model_dump() if isinstance(weekend_online_window, TimeWindow) else weekend_online_window)
    if weekday_online_window is not None:
        weekday_window = TimeWindow.model_validate(weekday_online_window.model_dump() if isinstance(weekday_online_window, TimeWindow) else weekday_online_window)
    rules = []
    for day in raw["solver_config"]["days"]:
        if day["day"] == weekend_online_day:
            if Stream.WEEKEND in day["windows"]:
                raise ValueError("The extra weekend online day cannot replace an existing physical weekend window.")
            day["windows"][Stream.WEEKEND] = [weekend_window.model_dump()]
        if day["day"] == weekday_online_day:
            if Stream.WEEKDAY not in day["windows"]:
                raise ValueError("Weekday online day must replace an existing weekday teaching day.")
            if weekday_online_window is not None:
                day["windows"][Stream.WEEKDAY] = [weekday_window.model_dump()]
        for stream in day["windows"]:
            online = (stream == Stream.WEEKDAY and day["day"] == weekday_online_day) or (stream == Stream.WEEKEND and day["day"] == weekend_online_day)
            rules.append(dict(day=day["day"], stream=stream, mode="ONLINE" if online else "PHYSICAL",
                allowed_session_types={SessionType.LECTURE} if online else set(SessionType)))
    raw["solver_config"]["delivery_calendar"] = dict(reference=configuration.configuration_reference+":explicit-PP1-online-day-settings", days=rules)
    return PP1Configuration.model_validate(raw)

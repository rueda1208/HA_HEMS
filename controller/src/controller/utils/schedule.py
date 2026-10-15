from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


def time_str_to_minutes(time_string: str) -> int:
    hour_str, minute_str = time_string.split(":")
    return int(hour_str) * 60 + int(minute_str)


@dataclass
class ScheduleEntry:
    minute_of_day: int
    setpoint: float

@dataclass
class DeviceSchedule:
    """Weekly setpoint schedule for a device, keyed by day of week (0=Sunday, ..., 6=Saturday)."""

    entries_by_day: Dict[int, List[ScheduleEntry]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, schedule_config: Dict[str, Any]) -> Optional["DeviceSchedule"]:
        setpoint_schedule = schedule_config.get("setpoint")
        if not setpoint_schedule:
            return None

        entries_by_day: Dict[int, List[ScheduleEntry]] = {}
        for day_key, day_schedule in setpoint_schedule.items():
            if not day_schedule:
                continue

            try:
                day = int(day_key)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid schedule day '{day_key}': expected an integer 0 (Sunday) to 6 (Saturday)")

            if not 0 <= day <= 6:
                raise ValueError(f"Invalid schedule day '{day_key}': expected 0 (Sunday) to 6 (Saturday)")

            entries = []
            for time_string, setpoint_value in day_schedule.items():
                try:
                    minute_of_day = time_str_to_minutes(time_string)
                except (TypeError, ValueError):
                    raise ValueError(f"Invalid schedule time '{time_string}' for day {day_key}: expected 'HH:MM'")

                try:
                    setpoint = float(setpoint_value)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Invalid schedule setpoint '{setpoint_value}' for day {day_key} at {time_string}: expected a number"
                    )

                entries.append(ScheduleEntry(minute_of_day=minute_of_day, setpoint=setpoint))

            entries.sort(key=lambda entry: entry.minute_of_day)
            entries_by_day[day] = entries

        return cls(entries_by_day=entries_by_day) if entries_by_day else None

    def get_entry_at_or_before(self, hour: int, day_of_week: int) -> Optional[Tuple[ScheduleEntry, int]]:
        """Find the last applicable schedule entry at or before `hour` on `day_of_week`, searching backward
        over the previous 7 days (looping over the week). Returns (entry, days_ago) or None if no entry exists.
        """
        return self.get_entry_at_or_before_minute(hour * 60, day_of_week)

    def get_entry_at_or_before_minute(
        self, minute_of_day: int, day_of_week: int
    ) -> Optional[Tuple[ScheduleEntry, int]]:
        """Find the last applicable schedule entry at or before a minute of day."""

        for offset in range(7):
            day = (day_of_week - offset) % 7
            day_entries = self.entries_by_day.get(day)

            if not day_entries:
                continue

            if offset == 0:
                candidates = [entry for entry in day_entries if entry.minute_of_day <= minute_of_day]
                if not candidates:
                    continue
                return candidates[-1], offset

            # Previous day in the week: every entry of that day is "before" now, keep the last one.
            return day_entries[-1], offset

        return None

    def get_entry_at(self, moment: datetime) -> Optional[Tuple[ScheduleEntry, int]]:
        """Find the last applicable entry at or before a datetime (Sunday=0)."""
        day_of_week = (moment.weekday() + 1) % 7
        minute_of_day = moment.hour * 60 + moment.minute
        return self.get_entry_at_or_before_minute(minute_of_day, day_of_week)

    def average_setpoint_between(
        self, start: datetime, end: datetime, default: float | None = None
    ) -> float:
        """Return the time-weighted scheduled setpoint over the half-open interval [start, end)."""
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        if end_utc <= start_utc:
            raise ValueError("Schedule averaging requires an end time after the start time")

        weighted_setpoint_seconds = 0.0
        cursor = start_utc
        while cursor < end_utc:
            local_cursor = cursor.astimezone(start.tzinfo)
            result = self.get_entry_at(local_cursor)
            if result is None:
                if default is None:
                    raise ValueError("No schedule setpoint is available and no default was provided")
                setpoint = default
            else:
                setpoint = result[0].setpoint

            next_minute = cursor.replace(second=0, microsecond=0) + timedelta(minutes=1)
            segment_end = min(end_utc, next_minute)
            weighted_setpoint_seconds += setpoint * (segment_end - cursor).total_seconds()
            cursor = segment_end

        return weighted_setpoint_seconds / (end_utc - start_utc).total_seconds()

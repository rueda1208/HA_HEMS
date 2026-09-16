from datetime import datetime, timezone

import pytest

from controller.utils.schedule import DeviceSchedule


def test_from_dict_returns_none_when_no_setpoint_key():
    assert DeviceSchedule.from_dict({}) is None
    assert DeviceSchedule.from_dict({"setpoint": {}}) is None


def test_from_dict_parses_and_sorts_entries():
    schedule = DeviceSchedule.from_dict({"setpoint": {"1": {"06:00": "21", "22:00": "18"}}})

    assert schedule is not None
    entries = schedule.entries_by_day[1]
    assert [entry.minute_of_day for entry in entries] == [360, 1320]
    assert [entry.setpoint for entry in entries] == [21.0, 18.0]


def test_from_dict_raises_for_invalid_day():
    with pytest.raises(ValueError):
        DeviceSchedule.from_dict({"setpoint": {"7": {"06:00": "21"}}})


def test_from_dict_raises_for_invalid_time():
    with pytest.raises(ValueError):
        DeviceSchedule.from_dict({"setpoint": {"1": {"6h00": "21"}}})


def test_from_dict_raises_for_invalid_setpoint():
    with pytest.raises(ValueError):
        DeviceSchedule.from_dict({"setpoint": {"1": {"06:00": "warm"}}})


def test_get_entry_at_or_before_same_day():
    schedule = DeviceSchedule.from_dict({"setpoint": {"1": {"06:00": "21", "22:00": "18"}}})

    result = schedule.get_entry_at_or_before(hour=10, day_of_week=1)

    assert result is not None
    entry, offset = result
    assert entry.setpoint == 21.0
    assert offset == 0


def test_get_entry_at_or_before_falls_back_to_previous_day():
    schedule = DeviceSchedule.from_dict({"setpoint": {"0": {"08:00": "19"}, "1": {"22:00": "18"}}})

    # Monday (1) at 05:00, before any entry today, should fall back to Sunday (0)'s last entry
    result = schedule.get_entry_at_or_before(hour=5, day_of_week=1)

    assert result is not None
    entry, offset = result
    assert entry.setpoint == 19.0
    assert offset == 1


def test_get_entry_at_or_before_returns_none_when_no_entries():
    schedule = DeviceSchedule(entries_by_day={})
    assert schedule.get_entry_at_or_before(hour=10, day_of_week=1) is None


def test_get_entry_at_uses_sunday_zero_convention():
    schedule = DeviceSchedule.from_dict({"setpoint": {"0": {"08:00": "19"}}})

    sunday = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)  # 2026-08-30 is a Sunday
    result = schedule.get_entry_at(sunday)

    assert result is not None
    entry, offset = result
    assert entry.setpoint == 19.0
    assert offset == 0

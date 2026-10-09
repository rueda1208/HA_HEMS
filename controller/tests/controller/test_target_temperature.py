from datetime import datetime, timezone

from controller.base import SetpointOverride
from controller.target_temperature import TargetTemperatureSource, resolve_target_temperature
from controller.utils.peak_event_plan import GdpPhase, GdpProfileName
from controller.utils.peak_events import PeakEvent


def _make_event(
    start: datetime,
    end: datetime,
) -> PeakEvent:
    return PeakEvent(
        offre="tarif",
        plagehoraire="pointe",
        duree="180",
        secteurclient="residentiel",
        datedebut=start,
        datefin=end,
    )


def test_schedule_only():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)  # Friday -> day_of_week 5
    device_configuration = {"schedule": {"setpoint": {"5": {"08:00": "20"}}}}

    resolution = resolve_target_temperature("dev1", device_configuration, gdp_event=None, now=now)

    assert resolution.value == 20.0
    assert resolution.source == TargetTemperatureSource.SCHEDULE


def test_override_wins_when_newer_than_last_schedule_change():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    device_configuration = {
        "schedule": {"setpoint": {"5": {"08:00": "20"}}},
        "setpoint": {"source": "parameter", "value": "23", "timestamp": "2026-08-28T17:00:00+00:00"},
    }

    resolution = resolve_target_temperature("dev1", device_configuration, gdp_event=None, now=now)

    assert resolution.value == 23.0
    assert resolution.source == TargetTemperatureSource.MANUAL_OVERRIDE


def test_stale_override_before_last_schedule_change_is_ignored():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    device_configuration = {
        "schedule": {"setpoint": {"5": {"08:00": "20"}}},
        "setpoint": {"source": "parameter", "value": "23", "timestamp": "2026-08-28T05:00:00+00:00"},
    }

    resolution = resolve_target_temperature("dev1", device_configuration, gdp_event=None, now=now)

    assert resolution.value == 20.0
    assert resolution.source == TargetTemperatureSource.SCHEDULE


def test_gdp_reduction_lowers_temperature():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    device_configuration = {
        "schedule": {"setpoint": {"5": {"08:00": "20"}}},
        "flexibility_downward": {"value": "2.0"},
    }
    gdp_event = _make_event(datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc), datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc))

    resolution = resolve_target_temperature("dev1", device_configuration, gdp_event=gdp_event, now=now)

    assert resolution.value == 18.0
    assert resolution.source == TargetTemperatureSource.GDP_EVENT
    assert resolution.gdp_phase == GdpPhase.REDUCTION


def test_gdp_profile_and_device_scope_are_optional_and_backward_compatible():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    event_start = datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc)
    event_end = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    configuration = {"schedule": {"setpoint": {"5": {"08:00": "20"}}}}

    legacy_resolution = resolve_target_temperature(
        "climate.legacy", configuration, gdp_event=_make_event(event_start, event_end), now=now
    )
    selected_resolution = resolve_target_temperature(
        "climate.selected",
        configuration,
        gdp_event=_make_event(event_start, event_end),
        now=now,
        gdp_profile_name=GdpProfileName.AGGRESSIVE,
        gdp_device_ids=frozenset({"climate.selected"}),
    )
    excluded_resolution = resolve_target_temperature(
        "climate.excluded",
        configuration,
        gdp_event=_make_event(event_start, event_end),
        now=now,
        gdp_profile_name=GdpProfileName.AGGRESSIVE,
        gdp_device_ids=frozenset({"climate.selected"}),
    )

    assert legacy_resolution.value == 18.5
    assert legacy_resolution.source == TargetTemperatureSource.GDP_EVENT
    assert selected_resolution.value == 17.0
    assert selected_resolution.source == TargetTemperatureSource.GDP_EVENT
    assert excluded_resolution.value == 20.0
    assert excluded_resolution.source == TargetTemperatureSource.SCHEDULE


def test_override_during_gdp_window_suppresses_gdp():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    gdp_event = _make_event(datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc), datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc))
    # MODERATE preset preconditioning lead time is 2h, so the window starts at 15:00
    device_configuration = {
        "schedule": {"setpoint": {"5": {"08:00": "20"}}},
        "setpoint": {"source": "parameter", "value": "24", "timestamp": "2026-08-28T17:30:00+00:00"},  # inside window
    }

    resolution = resolve_target_temperature("dev1", device_configuration, gdp_event=gdp_event, now=now)

    assert resolution.value == 24.0
    assert resolution.source == TargetTemperatureSource.MANUAL_OVERRIDE


def test_stale_override_before_gdp_window_does_not_suppress_gdp():
    """Regression test: an override set before the GDP window started must not suppress GDP (only an override
    made *during* the window's course should)."""
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    gdp_event = _make_event(datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc), datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc))
    device_configuration = {
        "schedule": {"setpoint": {"5": {"08:00": "20"}}},
        "flexibility_downward": {"value": "2.0"},
        "setpoint": {"source": "parameter", "value": "24", "timestamp": "2026-08-28T10:00:00+00:00"},  # before window (15:00)
    }

    resolution = resolve_target_temperature("dev1", device_configuration, gdp_event=gdp_event, now=now)

    assert resolution.value == 18.0
    assert resolution.source == TargetTemperatureSource.GDP_EVENT
    assert resolution.gdp_phase == GdpPhase.REDUCTION


def test_outside_gdp_window_returns_plain_schedule():
    now = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)  # well before the 15:00 preconditioning window
    gdp_event = _make_event(datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc), datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc))
    device_configuration = {"schedule": {"setpoint": {"5": {"00:00": "20"}}}}

    resolution = resolve_target_temperature("dev1", device_configuration, gdp_event=gdp_event, now=now)

    assert resolution.value == 20.0
    assert resolution.source == TargetTemperatureSource.SCHEDULE
    assert resolution.gdp_phase == GdpPhase.NORMAL


def test_newer_ha_setpoint_wins_over_older_hems_override():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    configuration = {
        "schedule": {"setpoint": {"5": {"08:00": "20"}}},
        "setpoint": {"source": "parameter", "value": "22", "timestamp": "2026-08-28T17:30:00+00:00"},
    }

    resolution = resolve_target_temperature(
        "dev1",
        configuration,
        gdp_event=None,
        now=now,
        ha_setpoint_override=SetpointOverride(21.5, datetime(2026, 8, 28, 17, 45, tzinfo=timezone.utc)),
    )

    assert resolution.value == 21.5
    assert resolution.source == TargetTemperatureSource.HA_MANUAL_OVERRIDE


def test_newer_hems_setpoint_wins_over_older_ha_setpoint():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    configuration = {
        "schedule": {"setpoint": {"5": {"08:00": "20"}}},
        "setpoint": {"source": "parameter", "value": "22", "timestamp": "2026-08-28T17:45:00+00:00"},
    }

    resolution = resolve_target_temperature(
        "dev1",
        configuration,
        gdp_event=None,
        now=now,
        ha_setpoint_override=SetpointOverride(21.5, datetime(2026, 8, 28, 17, 30, tzinfo=timezone.utc)),
    )

    assert resolution.value == 22.0
    assert resolution.source == TargetTemperatureSource.MANUAL_OVERRIDE


def test_ha_setpoint_during_gdp_window_cancels_remaining_gdp_phases():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    gdp_event = _make_event(
        datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc),
    )
    configuration = {
        "schedule": {"setpoint": {"5": {"08:00": "20"}}},
        "flexibility_downward": {"value": "2.0"},
    }

    resolution = resolve_target_temperature(
        "dev1",
        configuration,
        gdp_event=gdp_event,
        now=now,
        ha_setpoint_override=SetpointOverride(21.5, datetime(2026, 8, 28, 17, 30, tzinfo=timezone.utc)),
    )

    assert resolution.value == 21.5
    assert resolution.source == TargetTemperatureSource.HA_MANUAL_OVERRIDE
    assert resolution.gdp_phase == GdpPhase.REDUCTION


def test_ha_setpoint_before_gdp_window_does_not_cancel_gdp():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    gdp_event = _make_event(
        datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc),
    )
    configuration = {
        "schedule": {"setpoint": {"5": {"08:00": "20"}}},
        "flexibility_downward": {"value": "2.0"},
    }

    resolution = resolve_target_temperature(
        "dev1",
        configuration,
        gdp_event=gdp_event,
        now=now,
        ha_setpoint_override=SetpointOverride(21.5, datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc)),
    )

    assert resolution.value == 18.0
    assert resolution.source == TargetTemperatureSource.GDP_EVENT

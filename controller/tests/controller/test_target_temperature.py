from datetime import datetime, timedelta, timezone

import pytest

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


@pytest.mark.parametrize(
    ("profile", "reduction", "expected_recovery_midpoint"),
    [
        (GdpProfileName.MODERATE, 17.0, 18.5),
        (GdpProfileName.BOLD, 16.0, 18.0),
    ],
)
def test_smoother_profiles_use_cosine_ramps(profile, reduction, expected_recovery_midpoint):
    event_start = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)
    event_end = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    event = _make_event(event_start, event_end)
    configuration = {"schedule": {"setpoint": {"5": {"08:00": "20"}}}}

    preconditioning = resolve_target_temperature(
        "climate.device",
        configuration,
        gdp_event=event,
        now=datetime(2026, 8, 28, 14, 26, 15, tzinfo=timezone.utc),
        gdp_profile_name=profile,
    )
    reduction_midpoint = resolve_target_temperature(
        "climate.device",
        configuration,
        gdp_event=event,
        now=event_start + timedelta(minutes=7, seconds=30),
        gdp_profile_name=profile,
    )
    reduction_target = resolve_target_temperature(
        "climate.device",
        configuration,
        gdp_event=event,
        now=event_start + timedelta(minutes=15),
        gdp_profile_name=profile,
    )
    recovery_resolution = resolve_target_temperature(
        "climate.device",
        configuration,
        gdp_event=event,
        now=event_end + timedelta(minutes=22, seconds=30),
        gdp_profile_name=profile,
    )

    assert preconditioning.value == 20.0
    assert reduction_midpoint.value == round((21.0 + reduction) / 2, 2)
    assert reduction_target.value == reduction
    assert recovery_resolution.value == expected_recovery_midpoint
    assert all(
        resolution.value * 2 == round(resolution.value * 2)
        for resolution in (preconditioning, reduction_midpoint, reduction_target, recovery_resolution)
    )


def test_gdp_target_rounds_half_degree_ties_away_from_zero():
    event = _make_event(
        datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc),
    )
    configuration = {
        "schedule": {"setpoint": {"5": {"08:00": "20.25"}}},
        "flexibility_downward": {"value": "0.0", "source": "parameter"},
    }

    resolution = resolve_target_temperature(
        "climate.device",
        configuration,
        gdp_event=event,
        now=datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc),
        gdp_profile_name=GdpProfileName.MODERATE,
    )

    assert resolution.value == 20.5
    assert resolution.source == TargetTemperatureSource.GDP_EVENT


def test_schedule_target_is_not_quantized_to_gdp_increment():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    configuration = {"schedule": {"setpoint": {"5": {"08:00": "20.25"}}}}

    resolution = resolve_target_temperature("climate.device", configuration, gdp_event=None, now=now)

    assert resolution.value == 20.25
    assert resolution.source == TargetTemperatureSource.SCHEDULE


def test_extreme_profile_uses_step_transitions():
    event_start = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)
    event_end = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    event = _make_event(event_start, event_end)
    configuration = {"schedule": {"setpoint": {"5": {"08:00": "20"}}}}

    preconditioning_start = resolve_target_temperature(
        "climate.device",
        configuration,
        gdp_event=event,
        now=event_start - timedelta(hours=3),
        gdp_profile_name=GdpProfileName.EXTREME,
    )
    reduction_start = resolve_target_temperature(
        "climate.device",
        configuration,
        gdp_event=event,
        now=event_start,
        gdp_profile_name=GdpProfileName.EXTREME,
    )
    recovery_start = resolve_target_temperature(
        "climate.device",
        configuration,
        gdp_event=event,
        now=event_end,
        gdp_profile_name=GdpProfileName.EXTREME,
    )

    assert preconditioning_start.value == 22.0
    assert reduction_start.value == 14.0
    assert recovery_start.value == 20.0


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

    assert legacy_resolution.value == 17.0
    assert legacy_resolution.source == TargetTemperatureSource.GDP_EVENT
    assert selected_resolution.value == 14.0
    assert selected_resolution.source == TargetTemperatureSource.GDP_EVENT
    assert excluded_resolution.value == 20.0
    assert excluded_resolution.source == TargetTemperatureSource.SCHEDULE


def test_gdp_device_entry_controls_preconditioning_per_device():
    event_start = datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc)
    event_end = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    now = event_start - timedelta(hours=1)
    event = _make_event(event_start, event_end)
    configuration = {
        "schedule": {"setpoint": {"5": {"08:00": "20"}}},
        "preconditioning": {"value": "false"},
    }

    enabled = resolve_target_temperature(
        "climate.device",
        configuration,
        gdp_event=event,
        now=now,
        gdp_device_ids=frozenset({"climate.device"}),
        gdp_preconditioning_by_device={"climate.device": True},
    )
    disabled = resolve_target_temperature(
        "climate.device",
        configuration,
        gdp_event=event,
        now=now,
        gdp_device_ids=frozenset({"climate.device"}),
        gdp_preconditioning_by_device={"climate.device": False},
    )

    assert enabled.gdp_phase == GdpPhase.PRECONDITIONING
    assert enabled.value > 20.0
    assert disabled.gdp_phase == GdpPhase.NORMAL
    assert disabled.value == 20.0


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

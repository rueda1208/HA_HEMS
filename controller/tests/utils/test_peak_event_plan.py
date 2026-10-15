from datetime import datetime, timedelta, timezone

from controller.utils.peak_event_plan import (
    GDP_PROFILE_PRESETS,
    GdpPhase,
    GdpProfileName,
    GdpResponseProfile,
    PeakEventPlan,
)
from controller.utils.peak_events import PeakEvent


def _make_event(start: datetime, end: datetime) -> PeakEvent:
    return PeakEvent(
        offre="tarif",
        plagehoraire="pointe",
        duree="180",
        secteurclient="residentiel",
        datedebut=start,
        datefin=end,
    )


def test_from_device_configuration_uses_preset_defaults():
    expected_reductions = {
        GdpProfileName.EXTREME: 6.0,
        GdpProfileName.BOLD: 4.0,
        GdpProfileName.MODERATE: 3.0,
    }
    for profile_name, reduction in expected_reductions.items():
        profile = GdpResponseProfile.from_device_configuration(profile_name, {})
        preset = GDP_PROFILE_PRESETS[profile_name]

        assert profile.flexibility_upward == preset.flexibility_upward
        assert profile.flexibility_downward == reduction
        assert profile.preconditioning_enabled is True
        assert profile.preconditioning_lead_time == timedelta(hours=3)
        assert profile.recovery_duration == timedelta(hours=1)


def test_legacy_profile_names_map_to_current_presets():
    aggressive = GdpResponseProfile.from_device_configuration(GdpProfileName.AGGRESSIVE, {})
    stable = GdpResponseProfile.from_device_configuration(GdpProfileName.STABLE, {})

    assert aggressive.name == GdpProfileName.EXTREME
    assert aggressive.flexibility_downward == 6.0
    assert stable.name == GdpProfileName.MODERATE
    assert stable.flexibility_downward == 3.0


def test_from_device_configuration_applies_device_overrides():
    device_configuration = {
        "flexibility_upward": {"value": "3.5"},
        "flexibility_downward": {"value": "4.0"},
        "preconditioning": {"value": "false"},
    }
    profile = GdpResponseProfile.from_device_configuration(GdpProfileName.EXTREME, device_configuration)

    assert profile.flexibility_upward == 3.5
    assert profile.flexibility_downward == 4.0
    assert profile.preconditioning_enabled is False


def test_per_device_preconditioning_override_takes_precedence():
    profile = GdpResponseProfile.from_device_configuration(
        GdpProfileName.MODERATE,
        {"preconditioning": {"value": "false"}},
        preconditioning_override=True,
    )

    assert profile.preconditioning_enabled is True


def test_default_parameter_values_do_not_override_profile_defaults():
    profile = GdpResponseProfile.from_device_configuration(
        GdpProfileName.EXTREME,
        {
            "preconditioning": {"value": "false", "source": "default_parameter"},
            "flexibility_upward": {"value": "0.0", "source": "default_parameter"},
            "flexibility_downward": {"value": "0.0", "source": "default_parameter"},
        },
    )

    assert profile.preconditioning_enabled is True
    assert profile.flexibility_upward == 2.0
    assert profile.flexibility_downward == 6.0


def test_phase_at_reduction():
    start = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    plan = PeakEventPlan(
        event=_make_event(start, end), profile=GdpResponseProfile.from_device_configuration(GdpProfileName.MODERATE, {})
    )

    assert plan.phase_at(start + timedelta(hours=1)) == GdpPhase.REDUCTION


def test_phase_at_preconditioning_when_enabled():
    start = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    plan = PeakEventPlan(
        event=_make_event(start, end), profile=GdpResponseProfile.from_device_configuration(GdpProfileName.MODERATE, {})
    )

    assert plan.phase_at(start - timedelta(hours=1)) == GdpPhase.PRECONDITIONING


def test_phase_at_normal_when_preconditioning_disabled():
    start = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    profile = GdpResponseProfile.from_device_configuration(GdpProfileName.MODERATE, {"preconditioning": {"value": "false"}})
    plan = PeakEventPlan(event=_make_event(start, end), profile=profile)

    assert plan.phase_at(start - timedelta(hours=1)) == GdpPhase.NORMAL


def test_phase_at_recovery():
    start = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    plan = PeakEventPlan(
        event=_make_event(start, end), profile=GdpResponseProfile.from_device_configuration(GdpProfileName.MODERATE, {})
    )

    assert plan.phase_at(end + timedelta(minutes=30)) == GdpPhase.RECOVERY

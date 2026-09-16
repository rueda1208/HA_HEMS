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
    profile = GdpResponseProfile.from_device_configuration(GdpProfileName.MODERATE, {})
    preset = GDP_PROFILE_PRESETS[GdpProfileName.MODERATE]

    assert profile.flexibility_upward == preset.flexibility_upward
    assert profile.flexibility_downward == preset.flexibility_downward
    assert profile.preconditioning_enabled == preset.preconditioning_enabled


def test_from_device_configuration_applies_device_overrides():
    device_configuration = {
        "flexibility_upward": {"value": "3.5"},
        "flexibility_downward": {"value": "4.0"},
        "preconditioning": {"value": "false"},
    }
    profile = GdpResponseProfile.from_device_configuration(GdpProfileName.AGGRESSIVE, device_configuration)

    assert profile.flexibility_upward == 3.5
    assert profile.flexibility_downward == 4.0
    assert profile.preconditioning_enabled is False


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

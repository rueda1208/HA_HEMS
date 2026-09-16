from datetime import datetime, timedelta, timezone

import pytest

from controller.utils.peak_event_plan import GdpPhase, GdpProfileName, GdpResponseProfile, PeakEventPlan
from tests.mocks import make_peak_event


START = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)
END = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def moderate_plan():
    profile = GdpResponseProfile.from_device_configuration(GdpProfileName.MODERATE, {})
    return PeakEventPlan(make_peak_event(START, END), profile)


def test_phase_boundaries_are_start_inclusive_end_exclusive(moderate_plan):
    assert moderate_plan.phase_at(START - timedelta(hours=2)) == GdpPhase.PRECONDITIONING
    assert moderate_plan.phase_at(START) == GdpPhase.REDUCTION
    assert moderate_plan.phase_at(END) == GdpPhase.RECOVERY
    assert moderate_plan.phase_at(END + timedelta(hours=1)) == GdpPhase.NORMAL


def test_window_matches_phase_boundaries(moderate_plan):
    assert moderate_plan.window == (START - timedelta(hours=2), END + timedelta(hours=1))


def test_stable_profile_has_no_preconditioning():
    profile = GdpResponseProfile.from_device_configuration(GdpProfileName.STABLE, {})
    plan = PeakEventPlan(make_peak_event(START, END), profile)

    assert plan.phase_at(START - timedelta(minutes=1)) == GdpPhase.NORMAL
    assert plan.window == (START, END + timedelta(minutes=30))
    assert plan.phase_at(END + timedelta(minutes=29)) == GdpPhase.RECOVERY
    assert plan.phase_at(END + timedelta(minutes=30)) == GdpPhase.NORMAL

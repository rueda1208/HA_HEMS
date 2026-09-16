from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Dict, Tuple

from controller.utils.peak_events import PeakEvent


class GdpPhase(StrEnum):
    NORMAL = "normal"
    PRECONDITIONING = "preconditioning"
    REDUCTION = "reduction"
    RECOVERY = "recovery"


class GdpProfileName(StrEnum):
    AGGRESSIVE = "aggressive"
    MODERATE = "moderate"
    STABLE = "stable"


@dataclass
class GdpResponseProfile:
    """Tunable parameters for how a device reacts to a GDP peak event."""

    name: GdpProfileName
    preconditioning_enabled: bool
    preconditioning_lead_time: timedelta
    recovery_duration: timedelta
    flexibility_upward: float
    flexibility_downward: float
    ramp_margin: timedelta

    @classmethod
    def from_device_configuration(cls, profile_name: GdpProfileName, device_configuration: Dict[str, Any]) -> "GdpResponseProfile":
        """Start from the named preset, then apply any per-device overrides already present in configuration
        (e.g. `flexibility_upward`/`flexibility_downward`/`preconditioning`), so existing device config keys keep
        working unchanged for devices that don't opt into a named profile.
        """
        preset = GDP_PROFILE_PRESETS[profile_name]

        preconditioning_enabled = device_configuration.get("preconditioning", {}).get("value")
        flexibility_upward = device_configuration.get("flexibility_upward", {}).get("value")
        flexibility_downward = device_configuration.get("flexibility_downward", {}).get("value")

        return cls(
            name=preset.name,
            preconditioning_enabled=(
                str(preconditioning_enabled).lower() == "true"
                if preconditioning_enabled is not None
                else preset.preconditioning_enabled
            ),
            preconditioning_lead_time=preset.preconditioning_lead_time,
            recovery_duration=preset.recovery_duration,
            flexibility_upward=float(flexibility_upward) if flexibility_upward is not None else preset.flexibility_upward,
            flexibility_downward=(
                float(flexibility_downward) if flexibility_downward is not None else preset.flexibility_downward
            ),
            ramp_margin=preset.ramp_margin,
        )


GDP_PROFILE_PRESETS: Dict[GdpProfileName, GdpResponseProfile] = {
    GdpProfileName.AGGRESSIVE: GdpResponseProfile(
        name=GdpProfileName.AGGRESSIVE,
        preconditioning_enabled=True,
        preconditioning_lead_time=timedelta(hours=3),
        recovery_duration=timedelta(hours=2),
        flexibility_upward=2.0,
        flexibility_downward=3.0,
        ramp_margin=timedelta(minutes=15),
    ),
    GdpProfileName.MODERATE: GdpResponseProfile(
        name=GdpProfileName.MODERATE,
        preconditioning_enabled=True,
        preconditioning_lead_time=timedelta(hours=2),
        recovery_duration=timedelta(hours=1),
        flexibility_upward=1.0,
        flexibility_downward=1.5,
        ramp_margin=timedelta(minutes=15),
    ),
    GdpProfileName.STABLE: GdpResponseProfile(
        name=GdpProfileName.STABLE,
        preconditioning_enabled=False,
        preconditioning_lead_time=timedelta(0),
        recovery_duration=timedelta(minutes=30),
        flexibility_upward=0.0,
        flexibility_downward=0.5,
        ramp_margin=timedelta(minutes=15),
    ),
}


@dataclass
class PeakEventPlan:
    """A GDP peak event resolved against a response profile, exposing its phase windows and current phase."""

    event: PeakEvent
    profile: GdpResponseProfile

    @property
    def preconditioning_window(self) -> Tuple[datetime, datetime]:
        start = self.event.datedebut - self.profile.preconditioning_lead_time
        return start, self.event.datedebut

    @property
    def reduction_window(self) -> Tuple[datetime, datetime]:
        return self.event.datedebut, self.event.datefin

    @property
    def recovery_window(self) -> Tuple[datetime, datetime]:
        return self.event.datefin, self.event.datefin + self.profile.recovery_duration

    @property
    def window(self) -> Tuple[datetime, datetime]:
        """Full span during which this plan affects the device, from earliest active phase to latest."""
        start = self.preconditioning_window[0] if self.profile.preconditioning_enabled else self.reduction_window[0]
        return start, self.recovery_window[1]

    def phase_at(self, moment: datetime) -> GdpPhase:
        reduction_start, reduction_end = self.reduction_window
        if reduction_start <= moment < reduction_end:
            return GdpPhase.REDUCTION

        if self.profile.preconditioning_enabled:
            preconditioning_start, preconditioning_end = self.preconditioning_window
            if preconditioning_start <= moment < preconditioning_end:
                return GdpPhase.PRECONDITIONING

        recovery_start, recovery_end = self.recovery_window
        if recovery_start <= moment < recovery_end:
            return GdpPhase.RECOVERY

        return GdpPhase.NORMAL

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Dict, Tuple

from controller.utils.configuration import configured_parameter_value
from controller.utils.peak_events import PeakEvent


class GdpPhase(StrEnum):
    NORMAL = "normal"
    PRECONDITIONING = "preconditioning"
    REDUCTION = "reduction"
    RECOVERY = "recovery"


class GdpProfileName(StrEnum):
    EXTREME = "extreme"
    BOLD = "bold"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    STABLE = "stable"


def normalize_gdp_profile_name(profile_name: str | GdpProfileName) -> GdpProfileName:
    value = str(profile_name).strip().lower()
    aliases = {
        "aggressive": GdpProfileName.EXTREME,
        "extrême": GdpProfileName.EXTREME,
        "audacieux": GdpProfileName.BOLD,
        "audacious": GdpProfileName.BOLD,
        "modéré": GdpProfileName.MODERATE,
        "stable": GdpProfileName.MODERATE,
    }
    if value in aliases:
        return aliases[value]
    return GdpProfileName(value)


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
    def from_device_configuration(
        cls,
        profile_name: str | GdpProfileName,
        device_configuration: Dict[str, Any],
        preconditioning_override: bool | None = None,
    ) -> "GdpResponseProfile":
        """Start from the named preset, then apply any per-device overrides already present in configuration
        (e.g. `flexibility_upward`/`flexibility_downward`/`preconditioning`), so existing device config keys keep
        working unchanged for devices that don't opt into a named profile.
        """
        profile_name = normalize_gdp_profile_name(profile_name)
        preset = GDP_PROFILE_PRESETS[profile_name]

        preconditioning_enabled = (
            preconditioning_override
            if preconditioning_override is not None
            else configured_parameter_value(device_configuration, "preconditioning")
        )
        flexibility_upward = configured_parameter_value(device_configuration, "flexibility_upward")
        flexibility_downward = configured_parameter_value(device_configuration, "flexibility_downward")

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


GDP_PRECONDITIONING_LEAD_TIME = timedelta(hours=3)
GDP_RECOVERY_DURATION = timedelta(hours=1)


GDP_PROFILE_PRESETS: Dict[GdpProfileName, GdpResponseProfile] = {
    GdpProfileName.EXTREME: GdpResponseProfile(
        name=GdpProfileName.EXTREME,
        preconditioning_enabled=True,
        preconditioning_lead_time=GDP_PRECONDITIONING_LEAD_TIME,
        recovery_duration=GDP_RECOVERY_DURATION,
        flexibility_upward=2.0,
        flexibility_downward=6.0,
        ramp_margin=timedelta(minutes=15),
    ),
    GdpProfileName.BOLD: GdpResponseProfile(
        name=GdpProfileName.BOLD,
        preconditioning_enabled=True,
        preconditioning_lead_time=GDP_PRECONDITIONING_LEAD_TIME,
        recovery_duration=GDP_RECOVERY_DURATION,
        flexibility_upward=1.0,
        flexibility_downward=4.0,
        ramp_margin=timedelta(minutes=15),
    ),
    GdpProfileName.MODERATE: GdpResponseProfile(
        name=GdpProfileName.MODERATE,
        preconditioning_enabled=True,
        preconditioning_lead_time=GDP_PRECONDITIONING_LEAD_TIME,
        recovery_duration=GDP_RECOVERY_DURATION,
        flexibility_upward=1.0,
        flexibility_downward=3.0,
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

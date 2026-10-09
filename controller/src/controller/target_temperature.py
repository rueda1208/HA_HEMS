from __future__ import annotations

import logging

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Any, Dict, Tuple

from controller.base import SetpointOverride
from controller.utils.peak_event_plan import GdpPhase, GdpProfileName, GdpResponseProfile, PeakEventPlan
from controller.utils.peak_events import PeakEvent
from controller.utils.schedule import DeviceSchedule


logger = logging.getLogger(__name__)


class TargetTemperatureSource(StrEnum):
    MANUAL_OVERRIDE = "manual_override"
    HA_MANUAL_OVERRIDE = "ha_manual_override"
    GDP_EVENT = "gdp_event"
    SCHEDULE = "schedule"


@dataclass
class TargetTemperatureResolution:
    value: float
    source: TargetTemperatureSource
    gdp_phase: GdpPhase = GdpPhase.NORMAL


def _get_default_temperature(device_configuration: Dict[str, Any], device_id: str) -> float:
    default_temperature = device_configuration.get("setpoint", {}).get("value")
    if default_temperature is None:
        logger.error(
            "No target temperature found in schedule for device %s and no default value set, returning 21C as fallback",
            device_id,
        )
        return 21.0

    return float(default_temperature)


def _parse_schedule(device_configuration: Dict[str, Any], device_id: str) -> DeviceSchedule | None:
    try:
        return DeviceSchedule.from_dict(device_configuration.get("schedule", {}))
    except ValueError as ex:
        logger.error(f"Invalid schedule for device {device_id}, falling back to default value: {ex}")
        return None


def _get_manual_override(device_configuration: Dict[str, Any]) -> Tuple[float, datetime] | None:
    setpoint_configuration = device_configuration.get("setpoint", {})

    if setpoint_configuration.get("source", {}) != "parameter":
        return None

    override_value = setpoint_configuration.get("value")
    if override_value is None:
        return None

    override_timestamp = datetime.fromisoformat(setpoint_configuration.get("timestamp", 0))
    return float(override_value), override_timestamp


def _get_last_schedule_change(device_schedule: DeviceSchedule | None, now: datetime) -> datetime | None:
    if device_schedule is None:
        return None

    result = device_schedule.get_entry_at(now)
    if result is None:
        return None

    entry, offset = result
    return datetime.combine(
        now.date() - timedelta(days=offset),
        time(hour=entry.minute_of_day // 60, minute=entry.minute_of_day % 60),
        tzinfo=now.tzinfo,
    )


def _ramp(ramping_time: timedelta, elapsed: timedelta, initial_value: float, target_value: float, margin: timedelta) -> float:
    ramping_seconds = (ramping_time - margin).total_seconds()
    elapsed_seconds = elapsed.total_seconds()

    if ramping_seconds <= 0 or elapsed_seconds >= ramping_seconds:
        return round(target_value, 2)
    if elapsed_seconds <= 0:
        return round(initial_value, 2)

    ratio = elapsed_seconds / ramping_seconds
    return round(initial_value + (target_value - initial_value) * ratio, 2)


def _apply_gdp_adjustment(
    schedule_temperature: float,
    now: datetime,
    day_of_week: int,
    device_configuration: Dict[str, Any],
    device_id: str,
    plan: PeakEventPlan,
    phase: GdpPhase,
    device_schedule: DeviceSchedule | None,
) -> float:
    profile = plan.profile

    def baseline_at(hour: int) -> float:
        if device_schedule is not None:
            result = device_schedule.get_entry_at_or_before(hour, day_of_week)
            if result is not None:
                return result[0].setpoint
        return _get_default_temperature(device_configuration, device_id)

    if phase == GdpPhase.REDUCTION:
        return schedule_temperature - profile.flexibility_downward

    if phase == GdpPhase.PRECONDITIONING:
        precond_start, precond_end = plan.preconditioning_window
        reduction_start, reduction_end = plan.reduction_window

        max_baseline = baseline_at(reduction_start.hour)
        for hour in range(reduction_start.hour, reduction_end.hour):
            max_baseline = max(max_baseline, baseline_at(hour))

        return _ramp(
            ramping_time=precond_end - precond_start,
            elapsed=now - precond_start,
            initial_value=baseline_at(precond_start.hour),
            target_value=profile.flexibility_upward + max_baseline,
            margin=profile.ramp_margin,
        )

    if phase == GdpPhase.RECOVERY:
        recovery_start, recovery_end = plan.recovery_window

        return _ramp(
            ramping_time=recovery_end - recovery_start,
            elapsed=now - recovery_start,
            initial_value=baseline_at(recovery_start.hour - 1),
            target_value=baseline_at(recovery_end.hour),
            margin=profile.ramp_margin,
        )

    return schedule_temperature


def resolve_target_temperature(
    device_id: str,
    device_configuration: Dict[str, Any],
    gdp_event: PeakEvent | None,
    now: datetime,
    gdp_profile_name: GdpProfileName = GdpProfileName.MODERATE,
    ha_setpoint_override: SetpointOverride | None = None,
) -> TargetTemperatureResolution:
    """Resolve schedule, GDP, and timestamped HEMS/HA manual setpoint candidates.

    The most recent eligible manual override wins. During a GDP window, only overrides newer than
    the window start are eligible, so one can supersede the event's remaining phases without a stale
    earlier setpoint suppressing GDP response.
    """
    day_of_week = (now.weekday() + 1) % 7  # Convert Monday=0 to Sunday=0, ..., Saturday=6

    device_schedule = _parse_schedule(device_configuration, device_id)
    schedule_entry = device_schedule.get_entry_at(now) if device_schedule is not None else None
    schedule_temperature = (
        schedule_entry[0].setpoint
        if schedule_entry is not None
        else _get_default_temperature(device_configuration, device_id)
    )

    plan: PeakEventPlan | None = None
    if gdp_event is not None and (gdp_event.device_ids is None or device_id in gdp_event.device_ids):
        # TODO: Confirm whether HEMS device IDs identify zones, HA entities, or another controller-level ID.
        profile_name = (
            GdpProfileName(gdp_event.profile_name)
            if gdp_event.profile_name is not None
            else gdp_profile_name
        )
        profile = GdpResponseProfile.from_device_configuration(profile_name, device_configuration)
        plan = PeakEventPlan(event=gdp_event, profile=profile)

    manual_overrides: list[tuple[SetpointOverride, TargetTemperatureSource]] = []
    hems_override = _get_manual_override(device_configuration)
    if hems_override is not None:
        manual_overrides.append((SetpointOverride(*hems_override), TargetTemperatureSource.MANUAL_OVERRIDE))
    if ha_setpoint_override is not None:
        manual_overrides.append((ha_setpoint_override, TargetTemperatureSource.HA_MANUAL_OVERRIDE))

    if plan is not None and plan.window[0] <= now < plan.window[1]:
        eligible_overrides = [
            candidate
            for candidate in manual_overrides
            if candidate[0].timestamp >= plan.window[0]
        ]
    else:
        last_change = _get_last_schedule_change(device_schedule, now)
        eligible_overrides = [
            candidate
            for candidate in manual_overrides
            if last_change is None or candidate[0].timestamp > last_change
        ]

    if eligible_overrides:
        override, source = max(eligible_overrides, key=lambda candidate: candidate[0].timestamp)
        logger.debug(
            "%s: newest manual override from %s at %s sets target to %s C",
            device_id,
            source,
            override.timestamp.isoformat(),
            override.value,
        )
        return TargetTemperatureResolution(
            override.value,
            source,
            plan.phase_at(now) if plan is not None else GdpPhase.NORMAL,
        )

    if plan is not None:
        phase = plan.phase_at(now)
        if phase != GdpPhase.NORMAL:
            adjusted = _apply_gdp_adjustment(
                schedule_temperature, now, day_of_week, device_configuration, device_id, plan, phase, device_schedule
            )
            return TargetTemperatureResolution(adjusted, TargetTemperatureSource.GDP_EVENT, phase)

    return TargetTemperatureResolution(schedule_temperature, TargetTemperatureSource.SCHEDULE)

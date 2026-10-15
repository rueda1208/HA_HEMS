from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Literal, Protocol

from controller.utils import utils
from controller.utils.peak_event_plan import GdpProfileName
from controller.utils.peak_events import PeakEvent


@dataclass
class SetpointOverride:
    value: float
    timestamp: datetime


@dataclass(frozen=True)
class SwitchControlAction:
    desired_state: Literal["on", "off"]
    reason: str

    def __post_init__(self) -> None:
        if self.desired_state not in {"on", "off"}:
            raise ValueError(f"Unsupported switch state: {self.desired_state!r}")
        if not self.reason:
            raise ValueError("Switch control action reason must not be empty")


@dataclass
class ControlContext:
    """Uniform inputs shared by every device controller."""

    device_id: str
    device_configuration: Dict[str, Any]
    all_devices_configurations: Dict[str, Any]
    devices_states: Dict[str, Any]
    control_mode: utils.ControlMode
    gdp_event: PeakEvent | None
    now: datetime
    ha_setpoint_override: SetpointOverride | None = None
    gdp_profile_name: GdpProfileName = GdpProfileName.MODERATE
    gdp_device_ids: frozenset[str] | None = None
    gdp_preconditioning_by_device: Dict[str, bool] | None = None


class DeviceController(Protocol):
    def get_control_actions(self, context: ControlContext) -> Dict[str, Any]: ...

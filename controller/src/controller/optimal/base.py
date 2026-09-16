from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Protocol

from controller.utils import utils
from controller.utils.peak_events import PeakEvent


@dataclass
class ControlContext:
    """Single, uniform set of inputs for every optimal device controller (contrast with the legacy `devices/`
    controllers, whose signatures differ per device type and made polymorphic dispatch impractical)."""

    device_id: str
    device_configuration: Dict[str, Any]
    all_devices_configurations: Dict[str, Any]
    devices_states: Dict[str, Any]
    control_mode: utils.ControlMode
    gdp_event: PeakEvent | None
    now: datetime


class DeviceController(Protocol):
    def get_control_actions(self, context: ControlContext) -> Dict[str, Any]: ...

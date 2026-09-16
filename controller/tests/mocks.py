from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from controller.utils.device_type import DeviceType
from controller.utils.peak_events import PeakEvent


@dataclass
class MockDevice:
    entity_id: str
    device_type: DeviceType
    linked_zone_id: str | None = None
    setpoint_schedule: Dict[str, Dict[str, float]] = field(default_factory=dict)
    setpoint: float | None = None
    setpoint_timestamp: datetime | None = None
    extra_configuration: Dict[str, Any] = field(default_factory=dict)

    def configuration(self) -> Dict[str, Any]:
        configuration: Dict[str, Any] = {"device_type": self.device_type}
        if self.linked_zone_id is not None:
            configuration["linked_zone_id"] = {"value": self.linked_zone_id}
        if self.setpoint_schedule:
            configuration["schedule"] = {"setpoint": self.setpoint_schedule}
        if self.setpoint is not None:
            configuration["setpoint"] = {"value": self.setpoint}
            if self.setpoint_timestamp is not None:
                configuration["setpoint"].update(
                    {"source": "parameter", "timestamp": self.setpoint_timestamp.isoformat()}
                )
        configuration.update(self.extra_configuration)
        return configuration


@dataclass
class MockHomeAssistant:
    states: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    executed_actions: List[Dict[str, Any]] = field(default_factory=list)
    apply_actions: bool = True
    fail_on_execute: bool = False
    state_changed_callbacks: List[Any] = field(default_factory=list)

    def subscribe_state_changed(self, callback: Any) -> None:
        self.state_changed_callbacks.append(callback)

    def publish_state_changed(self, entity_id: str, new_state: Dict[str, Any]) -> None:
        old_state = self.states.get(entity_id, {})
        self.states[entity_id] = new_state
        event = {
            "type": "event",
            "event": {
                "event_type": "state_changed",
                "data": {"entity_id": entity_id, "old_state": old_state, "new_state": new_state},
            },
        }
        for callback in self.state_changed_callbacks:
            callback(event)

    def get_devices_states(self) -> Dict[str, Dict[str, Any]]:
        return self.states

    def execute_control_actions(
        self, control_actions: Dict[str, Any], devices_states: Dict[str, Dict[str, Any]]
    ) -> None:
        if self.fail_on_execute:
            raise RuntimeError("Simulated Home Assistant service failure")

        if not self.apply_actions:
            self.executed_actions.append(control_actions)
            return

        applied_actions: Dict[str, Any] = {}
        for entity_id, action in control_actions.items():
            state = self.states.setdefault(entity_id, {})
            attributes = state.setdefault("attributes", {})

            if isinstance(action, dict):
                if "state" in action and action["state"] != state.get("state"):
                    state["state"] = action["state"]
                    applied_actions.setdefault(entity_id, {})["state"] = action["state"]
                if "setpoint" in action and action["setpoint"] != attributes.get("temperature"):
                    attributes["temperature"] = action["setpoint"]
                    applied_actions.setdefault(entity_id, {})["setpoint"] = action["setpoint"]
            elif action != attributes.get("temperature"):
                attributes["temperature"] = action
                applied_actions[entity_id] = action

        self.executed_actions.append(applied_actions)


@dataclass
class MockDatabaseEngine:
    """Minimal database double; trend queries are bypassed in focused controller tests."""

    connection_attempts: int = 0

    def connect(self) -> Any:
        self.connection_attempts += 1
        raise AssertionError("Unexpected database access in this test")


def make_peak_event(start: datetime, end: datetime) -> PeakEvent:
    return PeakEvent(
        offre="tarif",
        plagehoraire="pointe",
        duree=str(int((end - start).total_seconds() / 60)),
        secteurclient="residentiel",
        datedebut=start,
        datefin=end,
    )


def make_states(*devices: MockDevice, outside_temperature: float = 5.0, indoor_temperature: float = 20.0) -> Dict[str, Dict[str, Any]]:
    states: Dict[str, Dict[str, Any]] = {
        "weather.home": {"attributes": {"temperature": outside_temperature}},
        "sensor.environment": {"attributes": {"current_temperature": indoor_temperature}},
    }
    for device in devices:
        states[device.entity_id] = {"attributes": {"current_temperature": indoor_temperature}}
    return states

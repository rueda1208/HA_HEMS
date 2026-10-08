from datetime import datetime, timezone

import pytest

from controller.devices.thermostat import ThermostatController
from controller.base import ControlContext
from controller.utils import utils
from controller.utils.device_type import DeviceType
from tests.mocks import MockDevice, MockHomeAssistant, make_states


NOW = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


def make_context(device, states):
    return ControlContext(
        device_id=device.entity_id,
        device_configuration=device.configuration(),
        all_devices_configurations={device.entity_id: device.configuration()},
        devices_states=states,
        control_mode=utils.ControlMode.HEATING,
        gdp_event=None,
        now=NOW,
    )


def test_simulator_applies_action_then_controller_sees_updated_state():
    device = MockDevice(
        "climate.thermostat",
        DeviceType.THERMOSTAT,
        setpoint_schedule={"5": {"08:00": 21.0}},
    )
    interface = MockHomeAssistant(states=make_states(device))
    controller = ThermostatController()

    first_action = controller.get_control_actions(make_context(device, interface.get_devices_states()))
    interface.execute_control_actions(first_action, interface.get_devices_states())
    second_action = controller.get_control_actions(make_context(device, interface.get_devices_states()))

    assert first_action == {device.entity_id: 21.0}
    assert interface.states[device.entity_id]["attributes"]["temperature"] == 21.0
    assert second_action == first_action


def test_simulator_suppresses_duplicate_setpoint_write():
    device = MockDevice("climate.thermostat", DeviceType.THERMOSTAT)
    interface = MockHomeAssistant(
        states={device.entity_id: {"attributes": {"temperature": 20.0}}}
    )

    interface.execute_control_actions({device.entity_id: 20.0}, interface.get_devices_states())

    assert interface.executed_actions == [{}]
    assert interface.states[device.entity_id]["attributes"]["temperature"] == 20.0


def test_simulator_can_model_service_failure():
    interface = MockHomeAssistant(fail_on_execute=True)

    with pytest.raises(RuntimeError, match="Simulated Home Assistant service failure"):
        interface.execute_control_actions({"climate.thermostat": 21.0}, {})

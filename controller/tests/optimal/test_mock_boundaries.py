from datetime import datetime, timezone

from tests.mocks import MockDevice, MockHomeAssistant, make_peak_event, make_states
from controller.utils.device_type import DeviceType


def test_mock_device_builds_controller_compatible_configuration():
    device = MockDevice(
        "climate.thermostat",
        DeviceType.THERMOSTAT,
        linked_zone_id="zone.living_room",
        setpoint_schedule={"1": {"06:00": 21.0}},
        setpoint=22.0,
    )

    configuration = device.configuration()

    assert configuration["device_type"] == DeviceType.THERMOSTAT
    assert configuration["linked_zone_id"] == {"value": "zone.living_room"}
    assert configuration["schedule"]["setpoint"]["1"]["06:00"] == 21.0
    assert configuration["setpoint"]["value"] == 22.0


def test_mock_home_assistant_captures_actions():
    interface = MockHomeAssistant(states={"climate.thermostat": {"state": "heat"}})
    actions = {"climate.thermostat": 21.0}

    interface.execute_control_actions(actions, interface.get_devices_states())

    assert interface.executed_actions == [actions]


def test_mock_helpers_produce_states_and_peak_event():
    device = MockDevice("climate.thermostat", DeviceType.THERMOSTAT)
    states = make_states(device, outside_temperature=-5.0, indoor_temperature=19.0)
    start = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
    event = make_peak_event(start, end)

    assert states["weather.home"]["attributes"]["temperature"] == -5.0
    assert states["sensor.environment"]["attributes"]["current_temperature"] == 19.0
    assert event.datedebut == start
    assert event.datefin == end

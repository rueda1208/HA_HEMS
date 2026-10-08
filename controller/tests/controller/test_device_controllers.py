from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from controller.base import ControlContext
from controller.devices.heat_pump import HeatPumpController
from controller.devices.thermostat import ThermostatController
from controller.devices.zone import ZoneController
from controller.utils import utils
from controller.utils.device_type import DeviceType

from tests.mocks import MockDatabaseEngine, MockDevice, make_peak_event, make_states


NOW = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


def make_context(device: MockDevice, all_devices, states, *, gdp_event=None, mode=utils.ControlMode.HEATING):
    return ControlContext(
        device_id=device.entity_id,
        device_configuration=device.configuration(),
        all_devices_configurations={key: value.configuration() for key, value in all_devices.items()},
        devices_states=states,
        control_mode=mode,
        gdp_event=gdp_event,
        now=NOW,
    )


def test_thermostat_controller_returns_single_resolved_setpoint():
    thermostat = MockDevice(
        "climate.thermostat",
        DeviceType.THERMOSTAT,
        setpoint_schedule={"5": {"08:00": 20.0}},
    )
    context = make_context(thermostat, {thermostat.entity_id: thermostat}, make_states(thermostat))

    assert ThermostatController().get_control_actions(context) == {"climate.thermostat": 20.0}


def test_heat_pump_controller_returns_mode_and_setpoint():
    heat_pump = MockDevice(
        "climate.heat_pump",
        DeviceType.HEAT_PUMP,
        setpoint_schedule={"5": {"08:00": 20.0}},
    )
    context = make_context(heat_pump, {heat_pump.entity_id: heat_pump}, make_states(heat_pump))

    actions = HeatPumpController().get_control_actions(context)

    assert actions["climate.heat_pump"] == {"state": "heat", "setpoint": 20.0, "user_pref": 20.0}


def test_zone_controller_produces_heat_pump_and_thermostat_actions(monkeypatch):
    zone = MockDevice("zone.living_room", DeviceType.ZONE, setpoint_schedule={"5": {"08:00": 21.0}})
    heat_pump = MockDevice("climate.heat_pump", DeviceType.HEAT_PUMP, linked_zone_id=zone.entity_id)
    thermostat = MockDevice("climate.thermostat", DeviceType.THERMOSTAT, linked_zone_id=zone.entity_id)
    devices = {device.entity_id: device for device in (zone, heat_pump, thermostat)}
    states = make_states(zone, heat_pump, thermostat, indoor_temperature=20.0)
    monkeypatch.setenv("WEATHER_ENTITY_ID", "weather.home")
    monkeypatch.setenv("ENVIRONMENT_SENSOR_ID", "sensor.environment")

    controller = ZoneController(MockDatabaseEngine())
    with patch.object(utils, "get_heat_pump_cop", return_value=2.5), patch.object(
        controller, "_get_indoor_temperature_trend", return_value=None
    ):
        actions = controller.get_control_actions(make_context(zone, devices, states))

    assert actions["climate.heat_pump"]["state"] == "heat"
    assert actions["climate.heat_pump"]["setpoint"] == 23
    assert actions["climate.thermostat"] > 21.0


def test_zone_controller_returns_no_actions_when_disabled(monkeypatch):
    zone = MockDevice(
        "zone.living_room",
        DeviceType.ZONE,
        extra_configuration={"disabled_until": {"value": "2026-08-28T19:00:00+00:00"}},
    )
    devices = {zone.entity_id: zone}
    monkeypatch.setenv("WEATHER_ENTITY_ID", "weather.home")

    actions = ZoneController(MockDatabaseEngine()).get_control_actions(make_context(zone, devices, make_states(zone)))

    assert actions == {}

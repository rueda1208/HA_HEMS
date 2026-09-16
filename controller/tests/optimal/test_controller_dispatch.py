from unittest.mock import MagicMock

from controller.optimal.controller import OptimalController
from controller.utils import utils
from controller.utils.device_type import DeviceType


def test_dispatches_to_registered_controller_and_skips_hub_and_unknown(monkeypatch):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")

    configurations = {
        "hub.testbuilding": {"device_type": DeviceType.HUB, "mode": {"value": "heating"}},
        "climate.thermostat_1": {"device_type": DeviceType.THERMOSTAT, "schedule": {"setpoint": {}}},
        "sensor.unknown": {"device_type": "unsupported_type"},
    }

    monkeypatch.setattr(utils, "retrieve_device_configuration", lambda: configurations)
    monkeypatch.setattr(utils, "retrieve_gdp_event", lambda: None)

    controller = OptimalController(db_engine=MagicMock())

    fake_thermostat_controller = MagicMock()
    fake_thermostat_controller.get_control_actions.return_value = {"climate.thermostat_1": 20.0}
    controller._device_controllers[DeviceType.THERMOSTAT] = fake_thermostat_controller

    control_actions = controller.get_control_actions(devices_states={})

    assert control_actions == {"climate.thermostat_1": 20.0}
    fake_thermostat_controller.get_control_actions.assert_called_once()


def test_returns_empty_when_control_mode_is_off(monkeypatch):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")
    configurations = {"hub.testbuilding": {"device_type": DeviceType.HUB, "mode": {"value": "off"}}}

    monkeypatch.setattr(utils, "retrieve_device_configuration", lambda: configurations)
    monkeypatch.setattr(utils, "retrieve_gdp_event", lambda: None)

    controller = OptimalController(db_engine=MagicMock())

    assert controller.get_control_actions(devices_states={}) == {}


def test_invalid_control_mode_defaults_to_off(monkeypatch):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")
    configurations = {"hub.testbuilding": {"device_type": DeviceType.HUB, "mode": {"value": "not_a_real_mode"}}}

    monkeypatch.setattr(utils, "retrieve_device_configuration", lambda: configurations)
    monkeypatch.setattr(utils, "retrieve_gdp_event", lambda: None)

    controller = OptimalController(db_engine=MagicMock())

    assert controller.get_control_actions(devices_states={}) == {}

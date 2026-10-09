from unittest.mock import MagicMock

from controller.controller import Controller
from controller.utils.device_type import DeviceType
from controller.utils.peak_event_plan import GdpProfileName


def test_dispatches_to_registered_controller_and_skips_hub_and_unknown(monkeypatch):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")

    configurations = {
        "hub.testbuilding": {
            "device_type": DeviceType.HUB,
            "mode": {"value": "heating"},
            "gdp_profile": {"value": "aggressive"},
            "gdp_device_ids": {"value": ["climate.thermostat_1"]},
        },
        "climate.thermostat_1": {"device_type": DeviceType.THERMOSTAT, "schedule": {"setpoint": {}}},
        "sensor.unknown": {"device_type": "unsupported_type"},
    }

    controller = Controller(db_engine=MagicMock())

    fake_thermostat_controller = MagicMock()
    fake_thermostat_controller.get_control_actions.return_value = {"climate.thermostat_1": 20.0}
    controller._device_controllers[DeviceType.THERMOSTAT] = fake_thermostat_controller

    control_actions = controller.get_control_actions(devices_states={}, configurations=configurations, gdp_event=None)

    assert control_actions == {"climate.thermostat_1": 20.0}
    fake_thermostat_controller.get_control_actions.assert_called_once()
    context = fake_thermostat_controller.get_control_actions.call_args.args[0]
    assert context.gdp_profile_name == GdpProfileName.AGGRESSIVE
    assert context.gdp_device_ids == frozenset({"climate.thermostat_1"})


def test_legacy_hub_configuration_defaults_to_moderate_profile_and_all_devices(monkeypatch):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")
    configurations = {
        "hub.testbuilding": {"device_type": DeviceType.HUB, "mode": {"value": "heating"}},
        "climate.thermostat_1": {"device_type": DeviceType.THERMOSTAT},
    }
    controller = Controller(db_engine=MagicMock())
    fake_thermostat_controller = MagicMock()
    fake_thermostat_controller.get_control_actions.return_value = {}
    controller._device_controllers[DeviceType.THERMOSTAT] = fake_thermostat_controller

    controller.get_control_actions(devices_states={}, configurations=configurations, gdp_event=None)

    context = fake_thermostat_controller.get_control_actions.call_args.args[0]
    assert context.gdp_profile_name == GdpProfileName.MODERATE
    assert context.gdp_device_ids is None


def test_returns_empty_when_control_mode_is_off(monkeypatch):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")
    configurations = {"hub.testbuilding": {"device_type": DeviceType.HUB, "mode": {"value": "off"}}}

    controller = Controller(db_engine=MagicMock())

    assert controller.get_control_actions(devices_states={}, configurations=configurations, gdp_event=None) == {}


def test_invalid_control_mode_defaults_to_off(monkeypatch):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")
    configurations = {"hub.testbuilding": {"device_type": DeviceType.HUB, "mode": {"value": "not_a_real_mode"}}}

    controller = Controller(db_engine=MagicMock())

    assert controller.get_control_actions(devices_states={}, configurations=configurations, gdp_event=None) == {}

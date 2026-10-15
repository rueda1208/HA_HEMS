import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from controller.controller import Controller
from controller.utils.device_type import DeviceType
from controller.utils.peak_event_plan import GdpProfileName
from controller.utils.peak_events import PeakEvent


def test_dispatches_to_registered_controller_and_skips_hub_and_unknown(monkeypatch):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")

    configurations = {
        "hub.testbuilding": {
            "device_type": DeviceType.HUB,
            "mode": {"value": "heating"},
            "gdp_profile": {"value": "extreme"},
            "gdp_device_ids": {
                "value": [{"device_id": "climate.thermostat_1", "preconditioning": True}]
            },
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
    assert context.gdp_profile_name == GdpProfileName.EXTREME
    assert context.gdp_device_ids == frozenset({"climate.thermostat_1"})
    assert context.gdp_preconditioning_by_device == {"climate.thermostat_1": True}


def test_missing_gdp_device_ids_defaults_to_detected_temperature_entities(monkeypatch):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")
    configurations = {
        "hub.testbuilding": {"device_type": DeviceType.HUB, "mode": {"value": "heating"}},
        "climate.thermostat_1": {"device_type": DeviceType.THERMOSTAT},
        "climate.heat_pump": {"device_type": DeviceType.HEAT_PUMP},
        "zone.living_room": {"device_type": DeviceType.ZONE},
        "switch.chauffe_eau": {"device_type": DeviceType.WATER_HEATER},
    }
    controller = Controller(db_engine=MagicMock())
    fake_thermostat_controller = MagicMock()
    fake_thermostat_controller.get_control_actions.return_value = {}
    fake_heat_pump_controller = MagicMock()
    fake_heat_pump_controller.get_control_actions.return_value = {}
    fake_zone_controller = MagicMock()
    fake_zone_controller.get_control_actions.return_value = {}
    fake_water_heater_controller = MagicMock()
    fake_water_heater_controller.get_control_actions.return_value = {}
    controller._device_controllers[DeviceType.THERMOSTAT] = fake_thermostat_controller
    controller._device_controllers[DeviceType.HEAT_PUMP] = fake_heat_pump_controller
    controller._device_controllers[DeviceType.ZONE] = fake_zone_controller
    controller._device_controllers[DeviceType.WATER_HEATER] = fake_water_heater_controller

    controller.get_control_actions(devices_states={}, configurations=configurations, gdp_event=None)

    expected_device_ids = frozenset(
        {"climate.thermostat_1", "climate.heat_pump", "zone.living_room", "switch.chauffe_eau"}
    )
    for fake_device_controller in (
        fake_thermostat_controller,
        fake_heat_pump_controller,
        fake_zone_controller,
        fake_water_heater_controller,
    ):
        context = fake_device_controller.get_control_actions.call_args.args[0]
        assert context.gdp_profile_name == GdpProfileName.MODERATE
        assert context.gdp_device_ids == expected_device_ids
        assert context.gdp_preconditioning_by_device == {}


def test_dispatch_accepts_legacy_string_gdp_device_ids(monkeypatch):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")
    configurations = {
        "hub.testbuilding": {
            "device_type": DeviceType.HUB,
            "mode": {"value": "heating"},
            "gdp_profile": {"value": "aggressive"},
            "gdp_device_ids": {"value": ["climate.thermostat_1"]},
        },
        "climate.thermostat_1": {"device_type": DeviceType.THERMOSTAT},
    }
    controller = Controller(db_engine=MagicMock())
    thermostat = MagicMock()
    thermostat.get_control_actions.return_value = {}
    controller._device_controllers[DeviceType.THERMOSTAT] = thermostat

    controller.get_control_actions(devices_states={}, configurations=configurations, gdp_event=None)

    context = thermostat.get_control_actions.call_args.args[0]
    assert context.gdp_profile_name == GdpProfileName.EXTREME
    assert context.gdp_device_ids == frozenset({"climate.thermostat_1"})
    assert context.gdp_preconditioning_by_device == {}


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


def test_logs_gdp_context_and_only_phase_transitions_when_phase_changes(monkeypatch, caplog):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")
    event_start = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        current = event_start - timedelta(hours=1)

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.current.astimezone().replace(tzinfo=None)
            return cls.current.astimezone(tz)

    monkeypatch.setattr("controller.controller.datetime", FrozenDateTime)
    configurations = {
        "hub.testbuilding": {
            "device_type": DeviceType.HUB,
            "mode": {"value": "heating"},
            "gdp_profile": {"value": "bold"},
            "gdp_device_ids": {"value": ["climate.thermostat_1"]},
        },
        "climate.thermostat_1": {"device_type": DeviceType.THERMOSTAT},
    }
    event = PeakEvent(
        offre="CPC-D",
        plagehoraire="AM",
        duree="PT04H00MS",
        secteurclient="Residentiel",
        datedebut=event_start,
        datefin=event_start + timedelta(hours=4),
    )
    controller = Controller(db_engine=MagicMock())
    thermostat = MagicMock()
    thermostat.get_control_actions.return_value = {}
    controller._device_controllers[DeviceType.THERMOSTAT] = thermostat

    with caplog.at_level(logging.INFO, logger="controller.controller"):
        controller.get_control_actions({}, configurations, event)
        assert "GDP context changed" in caplog.text
        assert "datedebut" in caplog.text
        assert "normalized_profile=bold" in caplog.text
        assert "gdp_device_ids=['climate.thermostat_1']" in caplog.text
        assert "phase=preconditioning" in caplog.text

        caplog.clear()
        FrozenDateTime.current = event_start
        controller.get_control_actions({}, configurations, event)
        assert caplog.text.count("GDP phase transition") == 1
        assert "previous_phase=preconditioning phase=reduction" in caplog.text

        caplog.clear()
        FrozenDateTime.current = event.datefin
        controller.get_control_actions({}, configurations, event)
        assert caplog.text.count("GDP phase transition") == 1
        assert "previous_phase=reduction phase=recovery" in caplog.text

        caplog.clear()
        FrozenDateTime.current = event.datefin + timedelta(hours=1)
        controller.get_control_actions({}, configurations, event)
        assert caplog.text.count("GDP phase transition") == 1
        assert "previous_phase=recovery phase=normal" in caplog.text

        caplog.clear()
        controller.get_control_actions({}, configurations, event)
        assert "GDP phase transition" not in caplog.text


def test_ending_gdp_selection_logs_normal_transition_without_event_key_error(monkeypatch, caplog):
    monkeypatch.setenv("BUILDING_ID", "TestBuilding")
    event_start = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    now = event_start + timedelta(minutes=1)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    monkeypatch.setattr("controller.controller.datetime", FrozenDateTime)
    configurations = {
        "hub.testbuilding": {
            "device_type": DeviceType.HUB,
            "mode": {"value": "heating"},
            "gdp_device_ids": {"value": ["climate.thermostat_1"]},
        },
        "climate.thermostat_1": {"device_type": DeviceType.THERMOSTAT},
    }
    event = PeakEvent(
        offre="CPC-D",
        plagehoraire="AM",
        duree="PT04H00MS",
        secteurclient="Residentiel",
        datedebut=event_start,
        datefin=event_start + timedelta(hours=4),
    )
    controller = Controller(db_engine=MagicMock())
    thermostat = MagicMock()
    thermostat.get_control_actions.return_value = {}
    controller._device_controllers[DeviceType.THERMOSTAT] = thermostat

    with caplog.at_level(logging.INFO, logger="controller.controller"):
        controller.get_control_actions({}, configurations, event)
        caplog.clear()

        controller.get_control_actions({}, configurations, None)

    assert "phase=normal" in caplog.text
    assert "reason=event_not_selected" in caplog.text

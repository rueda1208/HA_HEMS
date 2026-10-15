import logging
from unittest.mock import MagicMock

import pytest

from controller.application import ControllerRuntime, dispatch_control_actions
from controller.utils.configuration_logging import log_configuration_changes
from controller.ha_interface.ha_events import (
    setpoint_override_from_event,
    setpoint_state_change_from_event,
)
from controller.utils.hems_snapshot import setpoint_entity_ids, water_heater_switch_entity_ids
from controller.utils.runtime_config import (
    RuntimeConfig,
    get_hems_poll_seconds,
    hems_status_metrics_enabled,
    parse_control_allowlist,
)
from controller.utils.configuration import get_hems_data_source
from controller.utils.device_type import DeviceType


def test_initial_parameter_setpoints_are_logged(caplog):
    configurations = {
        "climate.salon": {
            "setpoint": {
                "value": "20",
                "source": "parameter",
                "timestamp": "2026-10-12T23:53:49-04:00",
            }
        },
        "climate.cuisine": {
            "setpoint": {
                "value": "20",
                "source": "default_parameter",
                "timestamp": "2026-10-02T23:53:49-04:00",
            }
        },
    }

    with caplog.at_level(logging.INFO, logger="controller.utils.configuration_logging"):
        log_configuration_changes(None, configurations, "startup")

    assert "IHD-side HEMS parameter temperature setpoint loaded for climate.salon: 20 C" in caplog.text
    assert "climate.cuisine" not in caplog.text


def test_configuration_changes_are_logged_as_structured_field_diffs(caplog):
    previous_configurations = {
        "climate.salon": {
            "setpoint": {
                "value": "20",
                "source": "default_parameter",
                "timestamp": "2026-10-02T23:53:49-04:00",
            }
        }
    }
    current_configurations = {
        "climate.salon": {
            "setpoint": {
                "value": "20",
                "source": "parameter",
                "timestamp": "2026-10-12T23:53:49-04:00",
            }
        }
    }

    with caplog.at_level(logging.INFO, logger="controller.utils.configuration_logging"):
        log_configuration_changes(previous_configurations, current_configurations, "hems_poll")

    assert "configuration_changed entity_type=device config_id=climate.salon operation=updated" in caplog.text
    assert '"new":"parameter","old":"default_parameter","path":"setpoint.source"' in caplog.text
    assert '"path":"setpoint.timestamp"' in caplog.text
    assert "2026-10-12T23:53:49-04:00" in caplog.text


def test_unchanged_snapshots_do_not_log_configuration_changes(caplog):
    configurations = {"hub.house": {"gdp_profile": {"value": "moderate"}}}

    with caplog.at_level(logging.INFO, logger="controller.utils.configuration_logging"):
        log_configuration_changes(configurations, configurations, "hems_poll")

    assert "configuration_changed" not in caplog.text


def test_device_and_hub_configuration_changes_are_reported_once(caplog):
    previous_configurations = {
        "climate.heat_pump": {"flexibility_upward": {"value": "0.0"}},
        "hub.house": {"gdp_profile": {"value": "moderate"}},
    }
    current_configurations = {
        "climate.heat_pump": {"flexibility_upward": {"value": "2.0"}},
        "hub.house": {"gdp_profile": {"value": "extreme"}},
    }

    with caplog.at_level(logging.INFO, logger="controller.utils.configuration_logging"):
        log_configuration_changes(previous_configurations, current_configurations, "hems_poll")
        log_configuration_changes(current_configurations, current_configurations, "hems_poll")

    assert caplog.text.count("configuration_changed ") == 2
    assert "config_id=climate.heat_pump" in caplog.text
    assert "config_id=hub.house" in caplog.text
    assert '"new":"extreme","old":"moderate","path":"gdp_profile.value"' in caplog.text


def test_sensitive_configuration_values_are_redacted(caplog):
    previous_configurations = {"hub.house": {"api_token": "old-secret"}}
    current_configurations = {"hub.house": {"api_token": "new-secret"}}

    with caplog.at_level(logging.INFO, logger="controller.utils.configuration_logging"):
        log_configuration_changes(previous_configurations, current_configurations, "hems_poll")

    assert "<redacted>" in caplog.text
    assert "old-secret" not in caplog.text
    assert "new-secret" not in caplog.text


def test_shadow_mode_logs_proposals_without_calling_home_assistant():
    ha_interface = MagicMock()
    actions = {"climate.thermostat": 21.0}

    dispatch_control_actions(ha_interface, actions, {}, "shadow", set())

    ha_interface.execute_control_actions.assert_not_called()
    ha_interface.save_shadow_control_actions.assert_called_once_with(actions)


def test_live_mode_dispatches_only_allowlisted_entities():
    ha_interface = MagicMock()
    actions = {"climate.allowed": 21.0, "climate.blocked": 19.0}
    states = {"climate.allowed": {"state": "heat"}}

    dispatch_control_actions(ha_interface, actions, states, "live", {"climate.allowed"})

    ha_interface.execute_control_actions.assert_called_once_with({"climate.allowed": 21.0}, states)


def test_live_mode_all_dispatches_every_generated_action():
    ha_interface = MagicMock()
    actions = {"climate.first": 21.0, "climate.second": 19.0}
    states = {"climate.first": {"state": "heat"}}

    dispatch_control_actions(ha_interface, actions, states, "live", None)

    ha_interface.execute_control_actions.assert_called_once_with(actions, states)


def test_live_mode_requires_a_nonempty_allowlist():
    with pytest.raises(ValueError, match="non-empty CONTROL_ALLOWLIST"):
        dispatch_control_actions(MagicMock(), {"climate.thermostat": 21.0}, {}, "live", set())


def test_control_allowlist_parses_all_and_explicit_entities():
    assert parse_control_allowlist(" all ") is None
    assert parse_control_allowlist("ALL") is None
    assert parse_control_allowlist(" climate.first, climate.second ") == {"climate.first", "climate.second"}
    assert parse_control_allowlist("") == set()


def test_setpoint_event_entities_include_only_thermostats_and_heat_pumps():
    configurations = {
        "climate.thermostat": {"device_type": DeviceType.THERMOSTAT},
        "climate.heat_pump": {"device_type": DeviceType.HEAT_PUMP},
        "zone.living_room": {"device_type": DeviceType.ZONE},
        "sensor.temperature": {"device_type": "sensor"},
    }

    assert setpoint_entity_ids(configurations) == {"climate.thermostat", "climate.heat_pump"}


def test_water_heater_switch_entities_use_configured_or_device_id():
    configurations = {
        "water_heater.tank": {
            "device_type": DeviceType.WATER_HEATER,
            "switch_entity_id": {"value": "switch.chauffe_eau"},
        },
        "switch.backup_heater": {"device_type": DeviceType.WATER_HEATER},
        "climate.thermostat": {"device_type": DeviceType.THERMOSTAT},
    }

    assert water_heater_switch_entity_ids(configurations) == {
        "switch.chauffe_eau",
        "switch.backup_heater",
    }


def test_hems_poll_seconds_defaults_to_thirty_and_accepts_environment_override(monkeypatch):
    monkeypatch.delenv("HEMS_POLL_SECONDS", raising=False)
    assert get_hems_poll_seconds() == 30

    monkeypatch.setenv("HEMS_POLL_SECONDS", "45")
    assert get_hems_poll_seconds() == 45


def test_hems_status_metrics_can_be_disabled_for_offline_testing(monkeypatch):
    monkeypatch.delenv("HEMS_STATUS_METRICS_ENABLED", raising=False)
    assert hems_status_metrics_enabled()

    monkeypatch.setenv("HEMS_STATUS_METRICS_ENABLED", "false")
    assert not hems_status_metrics_enabled()


def test_hems_data_source_defaults_to_api_and_accepts_mock(monkeypatch):
    monkeypatch.delenv("HEMS_DATA_SOURCE", raising=False)
    assert get_hems_data_source() == "api"

    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    assert get_hems_data_source() == "mock"


def test_hems_data_source_rejects_unknown_mode(monkeypatch):
    monkeypatch.setenv("HEMS_DATA_SOURCE", "fixture")

    with pytest.raises(ValueError, match="HEMS_DATA_SOURCE must be 'api' or 'mock'"):
        get_hems_data_source()


def test_ha_refresh_requests_are_coalesced():
    runtime = ControllerRuntime(
        RuntimeConfig(
            base_url="http://ha",
            token="test-token",
            hems_api_base_url="http://hems",
            building_id="test",
            control_mode="shadow",
            allowlist=set(),
            hems_status_metrics_enabled=False,
            hems_poll_seconds=30,
            reconciliation_seconds=900,
            max_snapshot_age_seconds=300,
        ),
        MagicMock(),
        MagicMock(),
    )

    runtime._request_hems_refresh("ha_setpoint")
    runtime._request_hems_refresh("ha_setpoint")

    assert runtime.hems_refresh_queue.get_nowait() == "ha_setpoint"
    assert runtime.hems_refresh_queue.empty()


def test_ha_override_uses_actual_state_update_timestamp():
    event = {
        "event": {
            "time_fired": "2026-08-28T18:00:01+00:00",
            "data": {
                "entity_id": "climate.sous_sol_2",
                "new_state": {
                    "attributes": {"temperature": 21.5},
                    "last_updated": "2026-08-28T17:59:59+00:00",
                },
            },
        }
    }

    result = setpoint_override_from_event(event)

    assert result is not None
    entity_id, override = result
    assert entity_id == "climate.sous_sol_2"
    assert override.value == 21.5
    assert override.timestamp.isoformat() == "2026-08-28T17:59:59+00:00"


def test_ha_setpoint_state_change_captures_both_values_and_event_timestamp():
    event = {
        "event": {
            "time_fired": "2026-08-28T18:00:01+00:00",
            "data": {
                "entity_id": "climate.sous_sol_2",
                "old_state": {"attributes": {"temperature": 20.5}},
                "new_state": {
                    "attributes": {"temperature": 21.5},
                    "last_updated": "2026-08-28T17:59:59.123456+00:00",
                },
            },
        }
    }

    change = setpoint_state_change_from_event(event)

    assert change is not None
    assert change.entity_id == "climate.sous_sol_2"
    assert change.old_value == 20.5
    assert change.new_value == 21.5
    assert change.timestamp.isoformat() == "2026-08-28T17:59:59.123456+00:00"
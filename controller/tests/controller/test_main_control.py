from unittest.mock import MagicMock

import pytest

from controller.main import (
    _get_hems_poll_seconds,
    _hems_status_metrics_enabled,
    _request_hems_refresh,
    _setpoint_override_from_event,
    dispatch_control_actions,
    setpoint_entity_ids,
)
from controller.utils import utils
from controller.utils.device_type import DeviceType


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


def test_live_mode_requires_a_nonempty_allowlist():
    with pytest.raises(ValueError, match="non-empty CONTROL_ALLOWLIST"):
        dispatch_control_actions(MagicMock(), {"climate.thermostat": 21.0}, {}, "live", set())


def test_setpoint_event_entities_include_only_thermostats_and_heat_pumps():
    configurations = {
        "climate.thermostat": {"device_type": DeviceType.THERMOSTAT},
        "climate.heat_pump": {"device_type": DeviceType.HEAT_PUMP},
        "zone.living_room": {"device_type": DeviceType.ZONE},
        "sensor.temperature": {"device_type": "sensor"},
    }

    assert setpoint_entity_ids(configurations) == {"climate.thermostat", "climate.heat_pump"}


def test_hems_poll_seconds_defaults_to_thirty_and_accepts_environment_override(monkeypatch):
    monkeypatch.delenv("HEMS_POLL_SECONDS", raising=False)
    assert _get_hems_poll_seconds() == 30

    monkeypatch.setenv("HEMS_POLL_SECONDS", "45")
    assert _get_hems_poll_seconds() == 45


def test_hems_status_metrics_can_be_disabled_for_offline_testing(monkeypatch):
    monkeypatch.delenv("HEMS_STATUS_METRICS_ENABLED", raising=False)
    assert _hems_status_metrics_enabled()

    monkeypatch.setenv("HEMS_STATUS_METRICS_ENABLED", "false")
    assert not _hems_status_metrics_enabled()


def test_hems_data_source_defaults_to_api_and_accepts_mock(monkeypatch):
    monkeypatch.delenv("HEMS_DATA_SOURCE", raising=False)
    assert utils.get_hems_data_source() == "api"

    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    assert utils.get_hems_data_source() == "mock"


def test_hems_data_source_rejects_unknown_mode(monkeypatch):
    monkeypatch.setenv("HEMS_DATA_SOURCE", "fixture")

    with pytest.raises(ValueError, match="HEMS_DATA_SOURCE must be 'api' or 'mock'"):
        utils.get_hems_data_source()


def test_ha_refresh_requests_are_coalesced():
    from queue import Queue

    refresh_queue: Queue[str] = Queue(maxsize=1)

    _request_hems_refresh(refresh_queue, "ha_setpoint")
    _request_hems_refresh(refresh_queue, "ha_setpoint")

    assert refresh_queue.get_nowait() == "ha_setpoint"
    assert refresh_queue.empty()


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

    result = _setpoint_override_from_event(event)

    assert result is not None
    entity_id, override = result
    assert entity_id == "climate.sous_sol_2"
    assert override.value == 21.5
    assert override.timestamp.isoformat() == "2026-08-28T17:59:59+00:00"
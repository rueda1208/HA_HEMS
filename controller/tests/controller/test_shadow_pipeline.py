import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from controller.controller import Controller
from controller.ha_interface.ha_interface import HomeAssistantDeviceInterface
from controller.main import (
    _setpoint_override_from_event,
    _setpoint_state_change_from_event,
    dispatch_control_actions,
)
from controller.utils import utils


def _write_configuration(path, setpoint, timestamp):
    configuration = {
        "hub.test": {"device_type": "hub", "mode": {"value": "heating"}},
        "climate.sous_sol_2": {
            "device_type": "thermostat",
            "setpoint": {"source": "parameter", "value": setpoint, "timestamp": timestamp.isoformat()},
        },
    }
    path.write_text(json.dumps(configuration), encoding="utf-8")


def test_mock_hems_and_ha_setpoint_flow_through_shadow_control(tmp_path, monkeypatch):
    configuration_path = tmp_path / "devices.json"
    gdp_events_path = tmp_path / "peak-events.json"
    gdp_events_path.write_text("[]", encoding="utf-8")

    now = datetime.now().astimezone()
    ha_timestamp = now - timedelta(seconds=30)
    _write_configuration(configuration_path, 20.0, now - timedelta(seconds=60))

    monkeypatch.setenv("BUILDING_ID", "TEST")
    monkeypatch.setenv("HEMS_DATA_SOURCE", "mock")
    monkeypatch.setenv("MOCK_CONFIGURATION_PATH", str(configuration_path))
    monkeypatch.setenv("MOCK_GDP_EVENTS_PATH", str(gdp_events_path))

    ha_event = {
        "event": {
            "time_fired": now.isoformat(),
            "data": {
                "entity_id": "climate.sous_sol_2",
                "old_state": {"attributes": {"temperature": 20.5}},
                "new_state": {
                    "attributes": {"temperature": 21.5},
                    "last_updated": ha_timestamp.isoformat(),
                },
            },
        }
    }
    state_change = _setpoint_state_change_from_event(ha_event)
    override = _setpoint_override_from_event(ha_event)
    assert state_change is not None
    assert override is not None
    entity_id, ha_override = override

    database_rows = []
    ha_interface = HomeAssistantDeviceInterface("http://ha", "test-token")
    ha_interface._save_in_database = MagicMock(side_effect=lambda **row: database_rows.append(row))
    controller = Controller(MagicMock())

    ha_response = MagicMock()
    ha_response.json.return_value = [
        {
            "entity_id": "climate.sous_sol_2",
            "state": "heat",
            "attributes": {"temperature": 21.5, "current_temperature": 20.0},
        }
    ]

    with (
        patch("controller.ha_interface.ha_interface.requests.get", return_value=ha_response),
        patch("controller.ha_interface.ha_interface.requests.post") as ha_post,
    ):
        devices_states = ha_interface.get_devices_states()
        ha_interface.save_setpoint_state_change(
            state_change.entity_id,
            state_change.old_value,
            state_change.new_value,
            state_change.timestamp,
        )

        first_configuration = utils.retrieve_device_configuration()
        first_actions = controller.get_control_actions(
            devices_states,
            first_configuration,
            gdp_event=utils.retrieve_gdp_event(),
            ha_setpoint_overrides={entity_id: ha_override},
        )
        dispatch_control_actions(ha_interface, first_actions, devices_states, "shadow", set())

        _write_configuration(configuration_path, 22.0, now - timedelta(seconds=10))
        refreshed_configuration = utils.retrieve_device_configuration()
        second_actions = controller.get_control_actions(
            devices_states,
            refreshed_configuration,
            gdp_event=utils.retrieve_gdp_event(),
            ha_setpoint_overrides={entity_id: ha_override},
        )
        dispatch_control_actions(ha_interface, second_actions, devices_states, "shadow", set())

    assert first_actions == {"climate.sous_sol_2": 21.5}
    assert second_actions == {"climate.sous_sol_2": 22.0}
    ha_post.assert_not_called()

    state_rows = [row for row in database_rows if row["data"]["metric_type"] == "state_change"]
    assert [row["data"]["name"] for row in state_rows] == ["setpoint_old", "setpoint_new"]
    assert [row["data"]["value"] for row in state_rows] == [20.5, 21.5]
    assert all(row["timestamp"] == ha_timestamp for row in state_rows)

    proposal_rows = [row for row in database_rows if row["data"]["metric_type"] == "control_shadow"]
    assert [row["data"]["value"] for row in proposal_rows] == [21.5, 22.0]
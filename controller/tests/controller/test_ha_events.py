import json
from unittest.mock import MagicMock, patch

import websocket

from controller.ha_events import HomeAssistantEventListener


def state_changed_message(entity_id="climate.thermostat"):
    return {
        "type": "event",
        "event": {
            "event_type": "state_changed",
            "data": {"entity_id": entity_id, "new_state": {"state": "heat"}, "old_state": None},
        },
    }


def test_build_websocket_url():
    assert HomeAssistantEventListener._build_websocket_url("http://supervisor/core") == "ws://supervisor/core/api/websocket"
    assert HomeAssistantEventListener._build_websocket_url("https://ha.example.test/") == "wss://ha.example.test/api/websocket"


def test_relevant_event_filter_accepts_climate_and_weather():
    listener = HomeAssistantEventListener("http://ha", "token", lambda event: None)

    assert listener._is_relevant_state_changed(state_changed_message("climate.thermostat"))
    assert listener._is_relevant_state_changed(state_changed_message("weather.home"))
    assert not listener._is_relevant_state_changed(state_changed_message("sensor.temperature"))
    assert not listener._is_relevant_state_changed({"type": "result", "success": True})


def test_relevant_event_filter_can_use_explicit_entity_ids():
    listener = HomeAssistantEventListener(
        "http://ha", "token", lambda event: None, relevant_entity_ids={"climate.allowed"}
    )

    assert listener._is_relevant_state_changed(state_changed_message("climate.allowed"))
    assert not listener._is_relevant_state_changed(state_changed_message("climate.other"))


def test_temperature_filter_only_accepts_changed_setpoint_on_relevant_entity():
    listener = HomeAssistantEventListener(
        "http://ha",
        "token",
        lambda event: None,
        relevant_entity_ids={"climate.thermostat"},
        relevant_attribute="temperature",
    )
    changed = state_changed_message("climate.thermostat")
    changed["event"]["data"]["old_state"] = {"attributes": {"temperature": 20}}
    changed["event"]["data"]["new_state"] = {"attributes": {"temperature": 21}}

    unchanged = state_changed_message("climate.thermostat")
    unchanged["event"]["data"]["old_state"] = {"attributes": {"temperature": 20}}
    unchanged["event"]["data"]["new_state"] = {"attributes": {"temperature": 20}}

    unrelated_entity = state_changed_message("climate.other")
    unrelated_entity["event"]["data"]["old_state"] = {"attributes": {"temperature": 20}}
    unrelated_entity["event"]["data"]["new_state"] = {"attributes": {"temperature": 21}}

    assert listener._is_relevant_state_changed(changed)
    assert not listener._is_relevant_state_changed(unchanged)
    assert not listener._is_relevant_state_changed(unrelated_entity)


def test_temperature_filter_accepts_climate_setpoint_before_entity_list_is_loaded():
    listener = HomeAssistantEventListener(
        "http://ha", "token", lambda event: None, relevant_attribute="temperature"
    )
    changed_climate = state_changed_message("climate.sous_sol_2")
    changed_climate["event"]["data"]["old_state"] = {"attributes": {"temperature": 21.0}}
    changed_climate["event"]["data"]["new_state"] = {"attributes": {"temperature": 22.0}}

    changed_sensor = state_changed_message("sensor.outdoor_temperature")
    changed_sensor["event"]["data"]["old_state"] = {"attributes": {"temperature": 5.0}}
    changed_sensor["event"]["data"]["new_state"] = {"attributes": {"temperature": 6.0}}

    assert listener._is_relevant_state_changed(changed_climate)
    assert not listener._is_relevant_state_changed(changed_sensor)


def test_relevant_entity_ids_can_be_refreshed():
    listener = HomeAssistantEventListener(
        "http://ha", "token", lambda event: None, relevant_entity_ids={"climate.old"}
    )

    listener.set_relevant_entity_ids({"climate.new"})

    assert listener._is_relevant_state_changed(state_changed_message("climate.new"))
    assert not listener._is_relevant_state_changed(state_changed_message("climate.old"))


def test_listen_once_authenticates_subscribes_and_forwards_event():
    connection = MagicMock()
    connection.recv.side_effect = [
        json.dumps({"type": "auth_required", "ha_version": "2026.8"}),
        json.dumps({"type": "auth_ok", "ha_version": "2026.8"}),
        json.dumps({"type": "result", "id": 1, "success": True, "result": None}),
        websocket.WebSocketTimeoutException("idle connection"),
        json.dumps(state_changed_message()),
    ]
    received = []
    listener = HomeAssistantEventListener("http://ha", "secret", received.append)
    listener._on_state_changed = lambda event: (received.append(event), listener._stop_event.set())

    with patch("controller.ha_events.websocket.create_connection", return_value=connection):
        listener._listen_once()

    assert received == [state_changed_message()]
    connection.settimeout.assert_called_once_with(1.0)
    sent_messages = [json.loads(call.args[0]) for call in connection.send.call_args_list]
    assert sent_messages[0] == {"type": "auth", "access_token": "secret"}
    assert sent_messages[1] == {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
    connection.close.assert_called_once()


def test_listen_once_rejects_auth_failure():
    connection = MagicMock()
    connection.recv.side_effect = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_invalid", "message": "Invalid password"}),
    ]
    listener = HomeAssistantEventListener("http://ha", "secret", lambda event: None)

    with patch("controller.ha_events.websocket.create_connection", return_value=connection):
        try:
            listener._listen_once()
        except RuntimeError as error:
            assert "authentication failed" in str(error)
        else:
            raise AssertionError("Expected authentication failure")

    connection.close.assert_called_once()


def test_simulator_publishes_state_changed_notification():
    from tests.mocks import MockHomeAssistant

    interface = MockHomeAssistant(states={"climate.thermostat": {"state": "heat"}})
    received = []
    interface.subscribe_state_changed(received.append)
    new_state = {"state": "heat", "attributes": {"temperature": 22.0}}

    interface.publish_state_changed("climate.thermostat", new_state)

    assert received[0]["event"]["event_type"] == "state_changed"
    assert received[0]["event"]["data"]["entity_id"] == "climate.thermostat"
    assert received[0]["event"]["data"]["new_state"] == new_state

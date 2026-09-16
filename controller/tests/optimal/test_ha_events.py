import json
from unittest.mock import MagicMock, patch

from controller.optimal.ha_events import HomeAssistantEventListener


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


def test_listen_once_authenticates_subscribes_and_forwards_event():
    connection = MagicMock()
    connection.recv.side_effect = [
        json.dumps({"type": "auth_required", "ha_version": "2026.8"}),
        json.dumps({"type": "auth_ok", "ha_version": "2026.8"}),
        json.dumps({"type": "result", "id": 1, "success": True, "result": None}),
        json.dumps(state_changed_message()),
    ]
    received = []
    listener = HomeAssistantEventListener("http://ha", "secret", received.append)
    listener._on_state_changed = lambda event: (received.append(event), listener._stop_event.set())

    with patch("controller.optimal.ha_events.websocket.create_connection", return_value=connection):
        listener._listen_once()

    assert received == [state_changed_message()]
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

    with patch("controller.optimal.ha_events.websocket.create_connection", return_value=connection):
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

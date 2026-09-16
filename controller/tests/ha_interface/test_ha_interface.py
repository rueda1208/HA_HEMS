from unittest.mock import MagicMock, patch


with patch("sqlalchemy.create_engine", return_value=MagicMock()):
    from controller.ha_interface.ha_interface import HEAT_PUMP_ENTITY_ID, HomeAssistantDeviceInterface


class ResponseDouble:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_get_devices_states_maps_entities_without_mutating_response():
    payload = [
        {"entity_id": "climate.thermostat", "state": "heat", "attributes": {"temperature": 20.0}},
        {"entity_id": "weather.home", "state": "sunny", "attributes": {"temperature": 5.0}},
    ]
    interface = HomeAssistantDeviceInterface("http://ha", "token")

    with patch("controller.ha_interface.ha_interface.requests.get", return_value=ResponseDouble(payload)):
        states = interface.get_devices_states()

    assert states["climate.thermostat"]["state"] == "heat"
    assert states["weather.home"]["attributes"]["temperature"] == 5.0
    assert "entity_id" in payload[0]


def test_get_devices_states_propagates_http_failure():
    response = MagicMock()
    response.raise_for_status.side_effect = RuntimeError("HA unavailable")
    interface = HomeAssistantDeviceInterface("http://ha", "token")

    with patch("controller.ha_interface.ha_interface.requests.get", return_value=response):
        try:
            interface.get_devices_states()
        except RuntimeError as error:
            assert str(error) == "HA unavailable"
        else:
            raise AssertionError("Expected the HA error to propagate")


def test_execute_zone_setpoint_posts_temperature_and_skips_same_value():
    interface = HomeAssistantDeviceInterface("http://ha", "token")
    interface._save_in_database = MagicMock()
    states = {"climate.thermostat": {"attributes": {"temperature": 20.0}}}

    with patch("controller.ha_interface.ha_interface.requests.post", return_value=ResponseDouble({})) as post:
        interface.execute_control_actions({"climate.thermostat": 21.0}, states)
        interface.execute_control_actions(
            {"climate.thermostat": 21.0}, {"climate.thermostat": {"attributes": {"temperature": 21.0}}}
        )

    post.assert_called_once_with(
        "http://ha/api/services/climate/set_temperature",
        headers=interface._headers,
        json={"entity_id": "climate.thermostat", "temperature": 21.0},
    )


def test_execute_heat_pump_posts_mode_and_setpoint():
    interface = HomeAssistantDeviceInterface("http://ha", "token")
    interface._save_in_database = MagicMock()
    states = {HEAT_PUMP_ENTITY_ID: {"state": "off", "attributes": {"temperature": 18.0}}}
    action = {HEAT_PUMP_ENTITY_ID: {"state": "heat", "setpoint": 21.0, "user_pref": 20.0}}

    with patch("controller.ha_interface.ha_interface.requests.post", return_value=ResponseDouble({})) as post:
        interface.execute_control_actions(action, states)

    assert post.call_count == 2
    assert post.call_args_list[0].kwargs["json"] == {"entity_id": HEAT_PUMP_ENTITY_ID, "hvac_mode": "heat"}
    assert post.call_args_list[1].kwargs["json"] == {"entity_id": HEAT_PUMP_ENTITY_ID, "temperature": 21.0}


def test_execute_control_actions_propagates_service_failure():
    interface = HomeAssistantDeviceInterface("http://ha", "token")
    interface._save_in_database = MagicMock()
    response = MagicMock()
    response.raise_for_status.side_effect = RuntimeError("service failed")

    with patch("controller.ha_interface.ha_interface.requests.post", return_value=response):
        try:
            interface.execute_control_actions({"climate.thermostat": 21.0}, {})
        except RuntimeError as error:
            assert str(error) == "service failed"
        else:
            raise AssertionError("Expected the HA service error to propagate")

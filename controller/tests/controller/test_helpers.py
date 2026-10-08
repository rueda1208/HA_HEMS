import pytest

from controller.helpers import (
    get_devices_for_zone,
    get_heat_pump_device_id,
    get_indoor_temperature,
    is_linked_to_controlled_zone,
)
from controller.utils.device_type import DeviceType


def test_is_linked_to_controlled_zone_false_when_no_link():
    assert is_linked_to_controlled_zone({}, {}) is False


def test_is_linked_to_controlled_zone_false_when_zone_off():
    device_configuration = {"linked_zone_id": {"value": "zone.living_room"}}
    all_configurations = {"zone.living_room": {"mode": {"value": "off"}}}

    assert is_linked_to_controlled_zone(device_configuration, all_configurations) is False


def test_is_linked_to_controlled_zone_true_when_zone_active():
    device_configuration = {"linked_zone_id": {"value": "zone.living_room"}}
    all_configurations = {"zone.living_room": {"mode": {"value": "heating"}}}

    assert is_linked_to_controlled_zone(device_configuration, all_configurations) is True


def test_get_devices_for_zone_filters_by_linked_zone_id():
    all_configurations = {
        "climate.heat_pump": {"linked_zone_id": {"value": "zone.living_room"}},
        "climate.other": {"linked_zone_id": {"value": "zone.bedroom"}},
    }

    result = get_devices_for_zone("zone.living_room", all_configurations)

    assert list(result.keys()) == ["climate.heat_pump"]


def test_get_heat_pump_device_id_returns_matching_device():
    zone_devices = {
        "climate.thermostat": {"device_type": DeviceType.THERMOSTAT},
        "climate.heat_pump": {"device_type": DeviceType.HEAT_PUMP},
    }

    assert get_heat_pump_device_id(zone_devices) == "climate.heat_pump"


def test_get_heat_pump_device_id_raises_when_missing():
    with pytest.raises(ValueError):
        get_heat_pump_device_id({"climate.thermostat": {"device_type": DeviceType.THERMOSTAT}})


def test_get_indoor_temperature_prefers_current_temperature_attribute():
    devices_states = {"sensor.env": {"attributes": {"current_temperature": "21.5"}}}
    assert get_indoor_temperature("sensor.env", devices_states) == 21.5


def test_get_indoor_temperature_falls_back_to_state():
    devices_states = {"sensor.env": {"state": "19.2"}}
    assert get_indoor_temperature("sensor.env", devices_states) == 19.2


def test_get_indoor_temperature_returns_none_when_missing():
    assert get_indoor_temperature("sensor.env", {}) is None

from __future__ import annotations

import logging

from typing import Any, Dict

from controller.utils.device_type import DeviceType


logger = logging.getLogger(__name__)


def is_linked_to_controlled_zone(device_configuration: Dict[str, Any], all_devices_configurations: Dict[str, Any]) -> bool:
    linked_zone_id = device_configuration.get("linked_zone_id", {}).get("value")
    if not linked_zone_id:
        return False

    linked_zone_configuration = all_devices_configurations.get(linked_zone_id)
    if not linked_zone_configuration:
        logger.warning(
            f"Device {device_configuration} is linked to zone {linked_zone_id} which does not exist in the "
            f"configuration"
        )
        return False

    linked_zone_mode = linked_zone_configuration.get("mode", {}).get("value", "off")
    if linked_zone_mode == "off":
        logger.info(f"Device {device_configuration} is linked to zone {linked_zone_id} which is in OFF mode")
        return False

    return True


def get_devices_for_zone(zone_entity_id: str, all_devices_configurations: Dict[str, Any]) -> Dict[str, Any]:
    return {
        device_entity_id: device_configuration
        for device_entity_id, device_configuration in all_devices_configurations.items()
        if device_configuration.get("linked_zone_id", {}).get("value") == zone_entity_id
    }


def get_heat_pump_device_id(zone_devices: Dict[str, Any]) -> str:
    for device_id, device_configuration in zone_devices.items():
        if device_configuration.get("device_type") == DeviceType.HEAT_PUMP:
            return device_id
    logger.error(f"No heat pump device linked to zone found among devices: {zone_devices.keys()}")
    raise ValueError("No heat pump device linked to zone found")


def get_indoor_temperature(environment_sensor_id: str, devices_states: Dict[str, Any]) -> float | None:
    temp_sensor_data = devices_states.get(environment_sensor_id)

    if temp_sensor_data is None:
        logger.error(f"Environment sensor {environment_sensor_id} not found in devices states")
        return None

    current_temperature = temp_sensor_data.get("attributes", {}).get("current_temperature")
    if current_temperature is not None:
        return float(current_temperature)

    state = temp_sensor_data.get("state")
    if state is not None:
        try:
            return float(state)
        except ValueError:
            logger.error(f"State value for sensor {environment_sensor_id} is not a valid float: {state}")
            return None

    logger.error(f"Temperature data not found for sensor {environment_sensor_id}")
    return None

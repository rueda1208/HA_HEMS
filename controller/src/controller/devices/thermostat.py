from __future__ import annotations

import logging

from typing import Any, Dict

from controller.base import ControlContext
from controller.helpers import is_linked_to_controlled_zone
from controller.target_temperature import resolve_target_temperature


logger = logging.getLogger(__name__)


class ThermostatController:
    """Calculate actions for a standalone thermostat."""

    def get_control_actions(self, context: ControlContext) -> Dict[str, Any]:
        if is_linked_to_controlled_zone(context.device_configuration, context.all_devices_configurations):
            logger.info(
                f"Thermostat {context.device_id} is linked to a controlled zone, skipping individual control actions"
            )
            return {}

        resolution = resolve_target_temperature(
            context.device_id,
            context.device_configuration,
            context.gdp_event,
            context.now,
            ha_setpoint_override=context.ha_setpoint_override,
            gdp_profile_name=context.gdp_profile_name,
            gdp_device_ids=context.gdp_device_ids,
        )

        current_temperature = (
            context.devices_states.get(context.device_id, {}).get("attributes", {}).get("current_temperature")
        )
        logger.debug(
            f"Device {context.device_id} - Inside temp.: {current_temperature} C, Target temp.: {resolution.value} C "
            f"(source={resolution.source}, phase={resolution.gdp_phase})"
        )

        return {context.device_id: resolution.value}

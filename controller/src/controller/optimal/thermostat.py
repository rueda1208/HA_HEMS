from __future__ import annotations

import logging

from typing import Any, Dict

from controller.optimal.base import ControlContext
from controller.optimal.helpers import is_linked_to_controlled_zone
from controller.optimal.target_temperature import resolve_target_temperature


logger = logging.getLogger(__name__)


class ThermostatController:
    """Optimal implementation for a standalone thermostat (device_type=thermostat), mirroring
    ClimateController._get_control_actions_for_thermostat but using the shared target-temperature resolution.
    """

    def get_control_actions(self, context: ControlContext) -> Dict[str, Any]:
        if is_linked_to_controlled_zone(context.device_configuration, context.all_devices_configurations):
            logger.info(
                f"Thermostat {context.device_id} is linked to a controlled zone, skipping individual control actions"
            )
            return {}

        resolution = resolve_target_temperature(
            context.device_id, context.device_configuration, context.gdp_event, context.now
        )

        current_temperature = (
            context.devices_states.get(context.device_id, {}).get("attributes", {}).get("current_temperature")
        )
        logger.debug(
            f"Device {context.device_id} - Inside temp.: {current_temperature} C, Target temp.: {resolution.value} C "
            f"(source={resolution.source}, phase={resolution.gdp_phase})"
        )

        return {context.device_id: resolution.value}

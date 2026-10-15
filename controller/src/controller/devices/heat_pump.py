from __future__ import annotations

import logging

from typing import Any, Dict

import numpy as np

from controller.base import ControlContext
from controller.utils import configuration, utils
from controller.utils.device_helpers import is_linked_to_controlled_zone
from controller.utils.target_temperature import resolve_target_temperature


logger = logging.getLogger(__name__)


def calculate_heat_pump_cop(
    heat_pump_specifications: Dict[str, Any], control_mode: utils.ControlMode, outside_temperature: float
) -> float:
    if control_mode == utils.ControlMode.COOLING:
        cop_points = heat_pump_specifications.get("cooling", {}).get("COP_points", {})
    elif control_mode == utils.ControlMode.HEATING:
        cop_points = heat_pump_specifications.get("heating", {}).get("COP_points", {})
    else:
        raise ValueError(f"Invalid control mode '{control_mode}' for COP model creation")

    outside_temperatures = [data["outdoor_dry_bulb_C"] for data in cop_points.values()]
    cop_values = [data["max"] for data in cop_points.values()]
    cop_model = np.poly1d(np.polyfit(outside_temperatures, cop_values, 2))
    cop = float(cop_model(outside_temperature))

    logger.info(
        f"Outside temperature: {outside_temperature} C, Heat Pump COP: {cop:.2f}, Heat Pump mode: {control_mode}"
    )
    return cop


def get_heat_pump_cop(control_mode: utils.ControlMode, outside_temperature: float) -> float:
    specifications = configuration.get_heat_pump_specifications()
    return calculate_heat_pump_cop(specifications, control_mode, outside_temperature)


class HeatPumpController:
    """Calculate actions for a standalone heat pump."""

    def get_control_actions(self, context: ControlContext) -> Dict[str, Any]:
        if is_linked_to_controlled_zone(
            context.device_id, context.device_configuration, context.all_devices_configurations
        ):
            logger.info(
                f"Heat pump {context.device_id} is linked to a controlled zone, skipping individual control actions"
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
            gdp_preconditioning_by_device=context.gdp_preconditioning_by_device,
        )

        current_temperature = (
            context.devices_states.get(context.device_id, {}).get("attributes", {}).get("current_temperature")
        )
        logger.debug(
            f"Device {context.device_id} - Inside temp.: {current_temperature} C, Target temp.: {resolution.value} C "
            f"(source={resolution.source}, phase={resolution.gdp_phase})"
        )

        return {
            context.device_id: {
                "state": "heat" if context.control_mode == utils.ControlMode.HEATING else "cool",
                "setpoint": resolution.value,
                "user_pref": resolution.value,
            }
        }

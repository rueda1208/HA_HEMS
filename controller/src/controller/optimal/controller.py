from __future__ import annotations

import logging
import os

from datetime import datetime
from typing import Any, Dict

from sqlalchemy import Engine

from controller.optimal.base import ControlContext, DeviceController
from controller.optimal.battery import BatteryController
from controller.optimal.electric_vehicle import ElectricVehicleController
from controller.optimal.heat_pump import HeatPumpController
from controller.optimal.thermostat import ThermostatController
from controller.optimal.water_heater import WaterHeaterController
from controller.optimal.zone import ZoneController
from controller.utils import utils
from controller.utils.device_type import DeviceType


logger = logging.getLogger(__name__)


class OptimalController:
    """Registry-based dispatcher: adding a new device type means registering a controller below, with no
    if/elif branching to edit (contrast with controller.Controller, the current hardcoded implementation this
    is a standalone alternative to — nothing here is wired into the live control loop).
    """

    def __init__(self, db_engine: Engine) -> None:
        self._device_controllers: Dict[DeviceType, DeviceController] = {
            DeviceType.ZONE: ZoneController(db_engine),
            DeviceType.HEAT_PUMP: HeatPumpController(),
            DeviceType.THERMOSTAT: ThermostatController(),
            DeviceType.BATTERY: BatteryController(),
            DeviceType.ELECTRIC_VEHICLE: ElectricVehicleController(),
            DeviceType.WATER_HEATER: WaterHeaterController(),
        }

    def get_control_actions(self, devices_states: Dict[str, Any]) -> Dict[str, Any]:
        configurations = utils.retrieve_device_configuration()

        building_id = str(os.getenv("BUILDING_ID"))

        control_mode_str = configurations.get(f"hub.{building_id.lower()}", {}).get("mode", {}).get("value", "off")
        control_mode = self._get_control_mode_from_string(control_mode_str)
        if control_mode == utils.ControlMode.OFF:
            logger.info("Controller is in OFF mode, skipping control actions")
            return {}

        gdp_event = utils.retrieve_gdp_event()
        now = datetime.now().astimezone()

        control_actions: Dict[str, Any] = {}

        for device_id, configuration in configurations.items():
            device_type = configuration.get("device_type")

            if device_type == DeviceType.HUB:
                logger.debug(f"No control actions required for device of type hub: {device_id}")
                continue

            device_controller = self._device_controllers.get(device_type)
            if device_controller is None:
                logger.info(f"Ignoring control actions for device: {device_id}")
                continue

            context = ControlContext(
                device_id=device_id,
                device_configuration=configuration,
                all_devices_configurations=configurations,
                devices_states=devices_states,
                control_mode=control_mode,
                gdp_event=gdp_event,
                now=now,
            )
            control_actions.update(device_controller.get_control_actions(context))

        return control_actions

    def _get_control_mode_from_string(self, control_mode_str: str) -> utils.ControlMode:
        try:
            return utils.ControlMode(control_mode_str)
        except ValueError:
            logger.warning(f"Invalid control mode '{control_mode_str}' in configuration, defaulting to OFF")
            return utils.ControlMode.OFF

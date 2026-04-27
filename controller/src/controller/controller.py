import logging
import os

from typing import Any, Dict

from sqlalchemy import Engine

from controller.devices import BatteryController, ClimateController, ElectricVehicleController, WaterHeaterController
from controller.utils import utils
from controller.utils.device_type import DeviceType


logger = logging.getLogger(__name__)


class Controller:
    _climate_controller: ClimateController
    _battery_controller: BatteryController
    _electric_vehicle_controller: ElectricVehicleController
    _water_heater_controller: WaterHeaterController

    def __init__(self, db_engine: Engine) -> None:
        self._climate_controller = ClimateController(db_engine)
        self._battery_controller = BatteryController()
        self._electric_vehicle_controller = ElectricVehicleController()
        self._water_heater_controller = WaterHeaterController()

    def get_control_actions(self, devices_states: Dict[str, Any]) -> Dict[str, Any]:
        configurations = utils.retrieve_device_configuration()

        building_id = str(os.getenv("BUILDING_ID"))

        control_mode_str = configurations.get(f"hub.{building_id.lower()}", {}).get("mode", {}).get("value", "off")
        control_mode = self._get_control_mode_from_string(control_mode_str)
        if control_mode == utils.ControlMode.OFF:
            logger.info("Controller is in OFF mode, skipping control actions")
            return {}

        gdp_event = utils.retrieve_gdp_event()
        if gdp_event:
            logger.info("GDP event detected, adjusting control strategy accordingly")
        else:
            logger.info("No GDP event detected, proceeding with normal control strategy")

        control_actions: Dict[str, Any] = {}

        for device_id, configuration in configurations.items():
            device_type = configuration.get("device_type")

            if device_type == DeviceType.HUB:
                logger.debug(f"No control actions required for device of type hub: {device_id}")
            elif (
                device_type == DeviceType.ZONE
                or device_type == DeviceType.THERMOSTAT
                or device_type == DeviceType.HEAT_PUMP
            ):
                logger.debug(f"Processing control actions for zone or climate device: {device_id}")
                control_actions.update(
                    self._climate_controller.get_control_actions(
                        device_id, configuration, configurations, devices_states, control_mode, gdp_event
                    )
                )
            elif device_type == DeviceType.BATTERY:
                logger.debug(f"Processing control actions for battery device: {device_id}")
                control_actions.update(
                    self._battery_controller.get_control_actions(
                        device_id, configuration, configurations, devices_states, gdp_event
                    )
                )
            elif device_type == DeviceType.ELECTRIC_VEHICLE:
                logger.debug(f"Processing control actions for electric vehicle device: {device_id}")
                control_actions.update(
                    self._electric_vehicle_controller.get_control_actions(
                        device_id, configuration, configurations, devices_states, gdp_event
                    )
                )
            elif device_type == DeviceType.WATER_HEATER:
                logger.debug(f"Processing control actions for water heater device: {device_id}")
                control_actions.update(
                    self._water_heater_controller.get_control_actions(
                        device_id, configuration, configurations, devices_states, gdp_event
                    )
                )

            else:
                logger.info(f"Ignoring control actions for device: {device_id}")

        return control_actions

    def _get_control_mode_from_string(self, control_mode_str: str) -> utils.ControlMode:
        try:
            return utils.ControlMode(control_mode_str)
        except ValueError:
            logger.warning(f"Invalid control mode '{control_mode_str}' in configuration, defaulting to OFF")
            return utils.ControlMode.OFF

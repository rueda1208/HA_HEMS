import logging
import os

from datetime import datetime
from typing import Any, Dict

from sqlalchemy import Engine

from controller.base import ControlContext, DeviceController, SetpointOverride
from controller.devices import (
    BatteryController,
    ElectricVehicleController,
    HeatPumpController,
    ThermostatController,
    WaterHeaterController,
    ZoneController,
)
from controller.utils import utils
from controller.utils.device_type import DeviceType
from controller.utils.peak_event_plan import GdpProfileName
from controller.utils.peak_events import PeakEvent


logger = logging.getLogger(__name__)


class Controller:
    def __init__(self, db_engine: Engine) -> None:
        self._device_controllers: Dict[DeviceType, DeviceController] = {
            DeviceType.ZONE: ZoneController(db_engine),
            DeviceType.HEAT_PUMP: HeatPumpController(),
            DeviceType.THERMOSTAT: ThermostatController(),
            DeviceType.BATTERY: BatteryController(),
            DeviceType.ELECTRIC_VEHICLE: ElectricVehicleController(),
            DeviceType.WATER_HEATER: WaterHeaterController(),
        }

    def get_control_actions(
        self,
        devices_states: Dict[str, Any],
        configurations: Dict[str, Any],
        gdp_event: PeakEvent | None,
        ha_setpoint_overrides: Dict[str, SetpointOverride] | None = None,
    ) -> Dict[str, Any]:
        building_id = str(os.getenv("BUILDING_ID"))

        hub_configuration = configurations.get(f"hub.{building_id.lower()}", {})
        control_mode_str = hub_configuration.get("mode", {}).get("value", "off")
        control_mode = self._get_control_mode_from_string(control_mode_str)
        if control_mode == utils.ControlMode.OFF:
            logger.info("Controller is in OFF mode, skipping control actions")
            return {}

        # TODO: Confirm these value wrappers and whether HEMS uses controller configuration IDs here.
        profile_value = hub_configuration.get("gdp_profile", {}).get("value", GdpProfileName.MODERATE.value)
        try:
            gdp_profile_name = GdpProfileName(profile_value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Unsupported GDP profile in hub configuration: {profile_value}") from error

        raw_gdp_device_ids = hub_configuration.get("gdp_device_ids", {}).get("value")
        if raw_gdp_device_ids is None:
            gdp_device_ids = None
        elif isinstance(raw_gdp_device_ids, list) and all(isinstance(device_id, str) for device_id in raw_gdp_device_ids):
            gdp_device_ids = frozenset(raw_gdp_device_ids)
        else:
            raise ValueError("hub gdp_device_ids must be a list of controller configuration IDs")

        if gdp_event:
            logger.info("GDP event detected, adjusting control strategy accordingly")
        else:
            logger.info("No GDP event detected, proceeding with normal control strategy")

        control_actions: Dict[str, Any] = {}

        for device_id, configuration in configurations.items():
            device_type = configuration.get("device_type")
            if device_type == DeviceType.HUB:
                logger.debug(f"No control actions required for device of type hub: {device_id}")
            else:
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
                    now=datetime.now().astimezone(),
                    ha_setpoint_override=(ha_setpoint_overrides or {}).get(device_id),
                    gdp_profile_name=gdp_profile_name,
                    gdp_device_ids=gdp_device_ids,
                )
                control_actions.update(device_controller.get_control_actions(context))

        return control_actions

    def _get_control_mode_from_string(self, control_mode_str: str) -> utils.ControlMode:
        try:
            return utils.ControlMode(control_mode_str)
        except ValueError:
            logger.warning(f"Invalid control mode '{control_mode_str}' in configuration, defaulting to OFF")
            return utils.ControlMode.OFF

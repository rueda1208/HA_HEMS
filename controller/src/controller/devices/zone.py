from __future__ import annotations

import logging
import math
import os

from datetime import datetime
from dataclasses import dataclass
from typing import Any, Dict

import numpy as np

from sqlalchemy import Engine, text

from controller.base import ControlContext
from controller.helpers import get_devices_for_zone, get_heat_pump_device_id, get_indoor_temperature
from controller.target_temperature import resolve_target_temperature
from controller.utils import utils


logger = logging.getLogger(__name__)


@dataclass
class ZoneControlParameters:
    """Tunable proportional-control parameters, overridable per zone via device configuration."""

    max_heat_push: float = 1.5
    max_cool_push: float = -2.0
    kp: float = 0.35
    cop_low: float = 1.3
    cop_good: float = 2.0
    cop_excellent: float = 3.0
    temp_tolerance: float = 0.3
    heat_pump_calibration_offset: float = 2.0

    @classmethod
    def from_device_configuration(cls, device_configuration: Dict[str, Any]) -> "ZoneControlParameters":
        defaults = cls()
        return cls(
            max_heat_push=float(device_configuration.get("max_heat_push", {}).get("value", defaults.max_heat_push)),
            max_cool_push=float(device_configuration.get("max_cool_push", {}).get("value", defaults.max_cool_push)),
            kp=float(device_configuration.get("kp", {}).get("value", defaults.kp)),
            cop_low=float(device_configuration.get("cop_low", {}).get("value", defaults.cop_low)),
            cop_good=float(device_configuration.get("cop_good", {}).get("value", defaults.cop_good)),
            cop_excellent=float(device_configuration.get("cop_excellent", {}).get("value", defaults.cop_excellent)),
            temp_tolerance=float(device_configuration.get("temp_tolerance", {}).get("value", defaults.temp_tolerance)),
            heat_pump_calibration_offset=float(
                device_configuration.get("heat_pump_calibration_offset", {}).get(
                    "value", defaults.heat_pump_calibration_offset
                )
            ),
        )


class ZoneController:
    """Calculate zone actions using target-temperature resolution and configurable control parameters."""

    def __init__(self, db_engine: Engine) -> None:
        self._db_engine = db_engine

    def get_control_actions(self, context: ControlContext) -> Dict[str, Any]:
        zone_id = context.device_id
        zone_configuration = context.device_configuration

        disabled_until = datetime.fromisoformat(
            zone_configuration.get("disabled_until", {}).get("value", "1970-01-01T00:00:00Z")
        )
        if disabled_until > context.now:
            logger.info(
                f"Zone {zone_id} is disabled until {disabled_until}, skipping control actions for this zone, linked "
                f"devices will be controlled individually"
            )
            return {}

        weather_entity_id = os.getenv("WEATHER_ENTITY_ID", "weather.home")
        outside_temperature = context.devices_states.get(weather_entity_id, {}).get("attributes", {}).get("temperature")
        if outside_temperature is None:
            raise ValueError("Outside temperature is None, cannot compute heat pump COP.")

        resolution = resolve_target_temperature(
            zone_id,
            zone_configuration,
            context.gdp_event,
            context.now,
            ha_setpoint_override=context.ha_setpoint_override,
        )
        target_temperature = resolution.value

        heat_pump_cop = utils.get_heat_pump_cop(context.control_mode, outside_temperature)

        environment_sensor_id = str(os.getenv("ENVIRONMENT_SENSOR_ID"))
        indoor_temperature = get_indoor_temperature(environment_sensor_id, context.devices_states)

        logger.debug(
            f"Zone {zone_id} - Inside temp.: {indoor_temperature} C, Target temp.: {target_temperature} C "
            f"(source={resolution.source}, phase={resolution.gdp_phase})"
        )

        zone_devices = get_devices_for_zone(zone_id, context.all_devices_configurations)
        heat_pump_device_id = get_heat_pump_device_id(zone_devices)

        control_actions: Dict[str, Any] = {heat_pump_device_id: {"user_pref": target_temperature}}

        params = ZoneControlParameters.from_device_configuration(zone_configuration)

        if context.control_mode == utils.ControlMode.HEATING:
            control_actions[heat_pump_device_id]["state"] = "heat"
            control_actions[heat_pump_device_id]["setpoint"] = math.ceil(
                target_temperature + params.heat_pump_calibration_offset
            )

            temp_trend = self._get_indoor_temperature_trend(environment_sensor_id)
            thermostat_adjustment = self._compute_heating_adjustment(
                target_temperature, indoor_temperature, temp_trend, heat_pump_cop, params
            )

            for device_id in zone_devices.keys():
                if device_id != heat_pump_device_id:
                    control_actions[device_id] = target_temperature + thermostat_adjustment
        else:  # context.control_mode == utils.ControlMode.COOLING
            control_actions[heat_pump_device_id]["state"] = "cool"
            control_actions[heat_pump_device_id]["setpoint"] = math.ceil(
                target_temperature + params.heat_pump_calibration_offset
            )

            # Turn off auxiliary heating in cooling mode
            for device_id in zone_devices.keys():
                if device_id != heat_pump_device_id:
                    control_actions[device_id] = 5  # Use a lower setpoint to ensure to turn off heating

        return control_actions

    def _compute_heating_adjustment(
        self,
        target_temperature: float,
        indoor_temperature: float | None,
        temp_trend: float | None,
        heat_pump_cop: float | None,
        params: ZoneControlParameters,
    ) -> float:
        temp_error = target_temperature - indoor_temperature
        proportional_adjustment = params.kp * temp_error
        thermostat_adjustment = 0.0

        if indoor_temperature <= target_temperature - params.temp_tolerance:
            # Cool zone
            if temp_trend is not None and temp_trend < 0:
                thermostat_adjustment = 1.2
            elif temp_trend is not None and temp_trend > 0:
                thermostat_adjustment = 0.3
            else:
                thermostat_adjustment = 0.6
        elif indoor_temperature >= target_temperature + params.temp_tolerance:
            # Hot zone
            if temp_trend is not None and temp_trend > 0:
                thermostat_adjustment = -2.0
            elif temp_trend is not None and temp_trend < 0:
                thermostat_adjustment = -0.5
            else:
                thermostat_adjustment = -1.0
        else:
            # Neutral zone
            if temp_trend is not None and temp_trend > 0:
                thermostat_adjustment = -0.5
            elif temp_trend is not None and temp_trend < 0:
                thermostat_adjustment = 0.5
            else:
                thermostat_adjustment = 0.0

        thermostat_adjustment += proportional_adjustment

        if thermostat_adjustment > 0 and heat_pump_cop is not None:
            if heat_pump_cop < params.cop_low:
                thermostat_adjustment *= 0.4
            elif heat_pump_cop < params.cop_good:
                thermostat_adjustment *= 0.7
            elif heat_pump_cop > params.cop_excellent:
                thermostat_adjustment *= 1.2

        return max(params.max_cool_push, min(params.max_heat_push, thermostat_adjustment))

    def _get_indoor_temperature_trend(self, environment_sensor_id: str, window_minutes: int = 15) -> float | None:
        """Calculates the indoor temperature trend based on historical data from TimescaleDB."""
        query = text(f"""
            SELECT time, value::double precision AS value
            FROM space_heating
            WHERE device_id = '{environment_sensor_id}'
                AND name = 'temperature'
                AND time > now() - interval '{window_minutes} minutes'
            ORDER BY time ASC
            LIMIT 100;
        """)
        try:
            with self._db_engine.connect() as conn:
                result = conn.execute(query, {"device_id": environment_sensor_id})
                data = result.fetchall()

            if len(data) < 3:
                logger.warning(
                    f"Not enough data points to calculate temperature trend for sensor {environment_sensor_id}"
                )
                return None

            clean_data = [(row[0], float(row[1])) for row in data if row[1] is not None]
            if len(clean_data) < 3:
                return None

            times = np.array([(row[0] - clean_data[0][0]).total_seconds() / 60.0 for row in clean_data], dtype=float)
            temperatures = np.array([row[1] for row in clean_data], dtype=float)

            a = np.vstack([times, np.ones(len(times))]).T
            slope, _ = np.linalg.lstsq(a, temperatures, rcond=None)[0]

            slope = float(np.clip(slope, -0.5, 0.5))
            return round(slope, 4)

        except Exception as e:
            logger.error(f"Error calculating temperature trend for sensor {environment_sensor_id}: {e}")
            return None

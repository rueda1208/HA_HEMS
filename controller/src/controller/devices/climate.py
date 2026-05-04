import logging
import math
import os

from datetime import datetime, time, timedelta
from typing import Any, Dict, Tuple

import numpy as np

from sqlalchemy import Engine, text

from controller.utils import utils
from controller.utils.device_type import DeviceType
from controller.utils.peak_events import PeakEvent


logger = logging.getLogger(__name__)


class ClimateController:
    _db_engine: Engine

    def __init__(self, db_engine: Engine) -> None:
        self._db_engine = db_engine

    def get_control_actions(
        self,
        device_id: str,
        device_configuration: Dict[str, Any],
        all_devices_configurations: Dict[str, Any],
        devices_states: Dict[str, Any],
        control_mode: utils.ControlMode,
        peak_event: PeakEvent | None,
    ) -> Dict[str, Any]:

        device_type = device_configuration.get("device_type")

        if device_type == DeviceType.ZONE:
            return self._get_control_actions_for_zone(
                device_id, device_configuration, all_devices_configurations, devices_states, control_mode, peak_event
            )

        if device_type == DeviceType.HEAT_PUMP:
            return self._get_control_actions_for_heat_pump(
                device_id, device_configuration, all_devices_configurations, devices_states, control_mode, peak_event
            )

        if device_type == DeviceType.THERMOSTAT:
            return self._get_control_actions_for_thermostat(
                device_id, device_configuration, all_devices_configurations, devices_states, peak_event
            )

        logger.error(f"Unsupported device type for device {device_id}, device type: {device_type}. No control actions.")
        return {}

    def _get_control_actions_for_zone(
        self,
        zone_id: str,
        zone_configuration: Dict[str, Any],
        all_devices_configurations: Dict[str, Any],
        devices_states: Dict[str, Any],
        control_mode: utils.ControlMode,
        peak_event: PeakEvent | None = None,
    ) -> Dict[str, Any]:
        control_actions: Dict[str, Any] = {}

        disabled_until = datetime.fromisoformat(
            zone_configuration.get("disabled_until", {}).get("value", "1970-01-01T00:00:00Z")
        )

        if disabled_until > datetime.now().astimezone():
            logger.info(
                f"Zone {zone_id} is disabled until {disabled_until}, skipping control actions for this zone, linked "
                f"devices will be controlled individually"
            )

            return control_actions

        # Calculate heat pump COP based on current outside temperature
        weather_entity_id = os.getenv("WEATHER_ENTITY_ID", "weather.home")
        outside_temperature = devices_states.get(weather_entity_id, {}).get("attributes", {}).get("temperature")
        if outside_temperature is None:
            raise ValueError("Outside temperature is None, cannot compute heat pump COP.")

        target_temperature = self._get_target_temperature(zone_id, zone_configuration, devices_states, peak_event)

        heat_pump_cop = utils.get_heat_pump_cop(control_mode, outside_temperature)

        environment_sensor_id = str(os.getenv("ENVIRONMENT_SENSOR_ID"))
        indoor_temperature = self._get_indoor_temperature(environment_sensor_id, devices_states)

        logger.debug(f"Zone {zone_id} - Inside temp.: {indoor_temperature} C, Target temp.: {target_temperature} C")

        # Get devices associated with the zone
        zone_devices = self._get_devices_for_zone(zone_id, all_devices_configurations)
        heat_pump_device_id = self._get_heat_pump_device_id(zone_devices)

        control_actions[heat_pump_device_id] = {
            "user_pref": target_temperature,
        }

        # Configurable parameters for control logic
        max_heat_push = 1.5  # Maximum heating push (positive value) to avoid excessive heating, to be tuned based on system response and desired comfort levels
        max_cool_push = -2.0  # Maximum cooling push (negative value) to avoid excessive cooling, to be tuned based on system response and desired comfort levels

        kp = 0.35  # Proportional gain for temperature error adjustment, to be tuned based on system response and desired aggressiveness of control actions

        cop_low = 1.3  # Threshold below which the heat pump is considered inefficient and the control logic relies more on auxiliary heating
        cop_good = 2.0  # Threshold above which the heat pump is considered efficient and the control logic relies more on the heat pump
        cop_excellent = 3.0  # Threshold above which the heat pump is considered very efficient and the control logic pushes more on the heat pump

        temp_tolerance = 0.3  # Degrees Celsius tolerance to avoid excessive on/off cycling
        # TODO: Get this value from configuration or compute it based on data (automatic) instead of hardcoding it here.
        # TODO: Use a value for cooling and others for heating instead of a single value for both modes ?
        heat_pump_calibration_offset = 2.0  # Degrees Celsius offset to account for heat pump compensation

        # Get indoor temperature trend to adjust control actions dynamically and avoid excessive on/off cycling of heat pump and auxiliary heating
        temp_trend = self._get_indoor_temperature_trend(environment_sensor_id)

        # Set heat pump setpoint and zone setpoints based on mode
        if control_mode == utils.ControlMode.HEATING:
            # Set heat pump setpoint with calibration offset
            control_actions[heat_pump_device_id]["state"] = "heat"
            control_actions[heat_pump_device_id]["setpoint"] = math.ceil(
                target_temperature + heat_pump_calibration_offset
            )

            # Calculate temperature error and trend to adjust thermostat setpoints dynamically
            temp_error = target_temperature - indoor_temperature  # + = frío
            thermostat_adjustment = 0.0

            # Proportional control adjustment based on temperature error
            proportional_adjustment = kp * temp_error

            # Thermostat adjustment logic
            if indoor_temperature <= target_temperature - temp_tolerance:
                # Cool zone
                if temp_trend is not None and temp_trend < 0:
                    thermostat_adjustment = +1.2
                elif temp_trend is not None and temp_trend > 0:
                    thermostat_adjustment = +0.3
                else:
                    thermostat_adjustment = +0.6
                logger.debug(
                    f"Zone is cool, temp trend: {temp_trend}, initial thermostat adjustment: {thermostat_adjustment:.2f} C"
                )

            elif indoor_temperature >= target_temperature + temp_tolerance:
                # Hot zone
                if temp_trend is not None and temp_trend > 0:
                    thermostat_adjustment = -2.0
                elif temp_trend is not None and temp_trend < 0:
                    thermostat_adjustment = -0.5
                else:
                    thermostat_adjustment = -1.0
                logger.debug(
                    f"Zone is hot, temp trend: {temp_trend}, initial thermostat adjustment: {thermostat_adjustment:.2f} C"
                )

            else:
                # Neutral zone
                if temp_trend is not None and temp_trend > 0:
                    thermostat_adjustment = -0.5
                elif temp_trend is not None and temp_trend < 0:
                    thermostat_adjustment = +0.5
                else:
                    thermostat_adjustment = 0.0
                logger.debug(
                    f"Zone is neutral, temp trend: {temp_trend}, initial thermostat adjustment: {thermostat_adjustment:.2f} C"
                )

            # Add proportional adjustment and modulate by heat pump COP
            thermostat_adjustment += proportional_adjustment
            if thermostat_adjustment > 0 and heat_pump_cop is not None:
                if heat_pump_cop < cop_low:
                    # Inefficient heat pump → let the resistive do the work
                    thermostat_adjustment *= 0.4

                elif heat_pump_cop < cop_good:
                    # Average heat pump → moderate adjustment
                    thermostat_adjustment *= 0.7

                elif heat_pump_cop > cop_excellent:
                    # Efficient heat pump → push more
                    thermostat_adjustment *= 1.2

            thermostat_adjustment = max(max_cool_push, min(max_heat_push, thermostat_adjustment))
            logger.debug(
                f"Temperature error: {temp_error:.2f} C, Proportional adjustment: {proportional_adjustment:.2f} C, Final thermostat adjustment after COP modulation: {thermostat_adjustment:.2f} C"
            )

            for device_id in zone_devices.keys():
                if device_id != heat_pump_device_id:
                    control_actions[device_id] = target_temperature + thermostat_adjustment

        else:  # control_mode == utils.ControlMode.COOLING:
            # Set heat pump setpoint with calibration offset
            control_actions[heat_pump_device_id]["state"] = "cool"
            control_actions[heat_pump_device_id]["setpoint"] = math.ceil(
                target_temperature + heat_pump_calibration_offset
            )

            # Turn off auxiliary heating in cooling mode
            for device_id in zone_devices.keys():
                if device_id != heat_pump_device_id:
                    control_actions[device_id] = 5  # Use a lower setpoint to ensure to turn off heating

        return control_actions

    def _get_heat_pump_device_id(self, zone_devices: Dict[str, Any]) -> str:
        for device_id, device_configuration in zone_devices.items():
            if device_configuration.get("device_type") == DeviceType.HEAT_PUMP:
                return device_id
        logger.error(f"No heat pump device linked to zone found among devices: {zone_devices.keys()}")
        raise ValueError("No heat pump device linked to zone found")

    def _get_control_actions_for_heat_pump(
        self,
        device_id: str,
        heat_pump_configuration: Dict[str, Any],
        all_devices_configurations: Dict[str, Any],
        devices_states: Dict[str, Any],
        control_mode: utils.ControlMode,
        peak_event: PeakEvent | None = None,
    ) -> Dict[str, Any]:
        control_actions: Dict[str, Any] = {}

        if self._is_linked_to_controlled_zone(heat_pump_configuration, all_devices_configurations):
            logger.info(f"Heat pump {device_id} is linked to a controlled zone, skipping individual control actions")
            return control_actions

        target_temperature = self._get_target_temperature(
            device_id, heat_pump_configuration, devices_states, peak_event
        )
        current_temperature = devices_states.get(device_id, {}).get("attributes", {}).get("current_temperature")

        control_actions[device_id] = {
            "state": "heat" if control_mode == utils.ControlMode.HEATING else "cool",
            "setpoint": target_temperature,
            "user_pref": target_temperature,
        }

        logger.debug(
            f"Device {device_id} - Inside temp.: {current_temperature} C, Target temp.: {target_temperature} C"
        )

        return control_actions

    def _get_control_actions_for_thermostat(
        self,
        device_id: str,
        thermostat_configuration: Dict[str, Any],
        all_devices_configurations: Dict[str, Any],
        devices_states: Dict[str, Any],
        peak_event: PeakEvent | None = None,
    ) -> Dict[str, Any]:
        control_actions: Dict[str, Any] = {}

        if self._is_linked_to_controlled_zone(thermostat_configuration, all_devices_configurations):
            logger.info(f"Thermostat {device_id} is linked to a controlled zone, skipping individual control actions")
            return control_actions

        target_temperature = self._get_target_temperature(
            device_id, thermostat_configuration, devices_states, peak_event
        )
        current_temperature = devices_states.get(device_id, {}).get("attributes", {}).get("current_temperature")
        control_actions[device_id] = target_temperature  # Apply target temperature directly as setpoint
        logger.debug(
            f"Device {device_id} - Inside temp.: {current_temperature} C, Target temp.: {target_temperature} C"
        )

        return control_actions

    def _get_devices_for_zone(self, zone_entity_id: str, all_devices_configurations: Dict[str, Any]) -> Dict[str, Any]:
        devices_for_zone: Dict[str, Any] = {}
        for device_entity_id, device_configuration in all_devices_configurations.items():
            if device_configuration.get("linked_zone_id", {}).get("value") == zone_entity_id:
                devices_for_zone[device_entity_id] = device_configuration
        return devices_for_zone

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

            # Clean and align data
            clean_data = [(row[0], float(row[1])) for row in data if row[1] is not None]

            if len(clean_data) < 3:
                return None

            # Calculate trend (simple linear regression)
            times = np.array([(row[0] - clean_data[0][0]).total_seconds() / 60.0 for row in clean_data], dtype=float)
            temperatures = np.array([row[1] for row in clean_data], dtype=float)

            # Linear regression to find the slope (temperature change per minute)
            a = np.vstack([times, np.ones(len(times))]).T
            slope, _ = np.linalg.lstsq(a, temperatures, rcond=None)[0]

            slope = float(np.clip(slope, -0.5, 0.5))
            return round(slope, 4)  # Temperature change per minute

        except Exception as e:
            logger.error(f"Error calculating temperature trend for sensor {environment_sensor_id}: {e}")
            return None

    def _get_indoor_temperature(self, environment_sensor_id: str, devices_states: Dict[str, Any]) -> float | None:
        """Retrieves the current indoor temperature from the specified environment sensor in Home Assistant."""
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

    def _is_linked_to_controlled_zone(
        self, device_configuration: Dict[str, Any], all_devices_configurations: Dict[str, Any]
    ) -> bool:
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

    def _get_target_temperature(
        self, device_id: str, configuration: Dict[str, Any], devices_states: Dict[str, Any], gdp_event: PeakEvent | None
    ) -> float:

        # Determine day type and current hour
        now = datetime.now().astimezone()
        current_hour = now.hour
        today = now.weekday()  # Current day of the week as an integer (0=Monday, 6=Sunday)
        day_of_week = (today + 1) % 7  # Convert to Sunday=0, Monday=1, ..., Saturday=6

        # Get initial target temperature from schedule or manual override
        init_target_temperature, manual_override = self._get_target_from_schedule(
            current_hour, day_of_week, devices_states, configuration, device_id
        )

        if manual_override:
            logger.debug(
                f"{device_id}: manual override detected with target temperature = {init_target_temperature} °C"
            )
            return init_target_temperature

        # Check for GDP events today
        if not gdp_event:
            logger.debug("No GDP events today, using regular schedule")
            return init_target_temperature

        # Get target temperature from schedule and apply flexibility
        target_temperature = self._get_target_from_gdp_event(
            init_target_temperature, now, day_of_week, devices_states, configuration, device_id, gdp_event
        )

        logger.debug(f"{device_id}: target temperature = {target_temperature} °C")
        return target_temperature

    def _get_target_from_gdp_event(
        self,
        init_target_temperature: float,
        now: datetime,
        day_of_week: int,
        devices_states: Dict[str, Any],
        configuration: Dict[str, Any],
        device_id: str,
        gdp_event: PeakEvent,
    ) -> float:
        gdp_timestamp_dict = {
            "start": gdp_event.datedebut,
            "end": gdp_event.datefin,
        }

        preconditioning_timestamp_dict = {
            "start": gdp_timestamp_dict["start"] - timedelta(hours=2),  # Two hours before event
            "end": gdp_timestamp_dict["start"],
        }

        post_event_recovery_timestamp_dict = {
            "start": gdp_timestamp_dict["end"],
            "end": gdp_timestamp_dict["end"] + timedelta(hours=1),  # One hour after event
        }

        # Apply flexibility adjustment if current hour is within GDP event hours
        zone_settings = configuration.get(device_id, {})
        flexibility_upward = float(zone_settings.get("flexibility_upward", {}).get("value", 0.0))
        flexibility_downward = float(zone_settings.get("flexibility_downward", {}).get("value", 0.0))
        zone_preconditioning = zone_settings.get("preconditioning", {}).get("value", "false").lower() == "true"

        if now >= gdp_timestamp_dict["start"] and now < gdp_timestamp_dict["end"]:
            # Negative for lowering temp during event
            target_temperature = init_target_temperature - flexibility_downward
        elif (
            zone_preconditioning
            and now >= preconditioning_timestamp_dict["start"]
            and now < preconditioning_timestamp_dict["end"]
        ):
            # Calculate max target temperature during GDP event hours for preconditioning
            start_preconditioning_hour = (preconditioning_timestamp_dict["start"]).hour
            start_gdp_hour = (gdp_timestamp_dict["start"]).hour
            stop_gdp_hour = (gdp_timestamp_dict["end"]).hour

            max_target_temperature_at_gdp_event, _ = (
                self._get_target_from_schedule(start_gdp_hour, day_of_week, devices_states, configuration, device_id)
                or 0.0
            )

            for hour in range(start_gdp_hour, stop_gdp_hour):
                max_target_temperature_at_gdp_event = max(
                    max_target_temperature_at_gdp_event,
                    (self._get_target_from_schedule(hour, day_of_week, devices_states, configuration, device_id)[0])
                    or 0.0,
                )

            # Positive for raising temp during preconditioning
            target_temperature = self._conditioning_ramping(
                ramping_time=int(
                    (preconditioning_timestamp_dict["end"] - preconditioning_timestamp_dict["start"]).total_seconds()
                ),
                elapsed_time=int((now - preconditioning_timestamp_dict["start"]).total_seconds()),
                initial_value=(
                    self._get_target_from_schedule(
                        start_preconditioning_hour, day_of_week, devices_states, configuration, device_id
                    )[0]
                )
                or 0.0,
                target_value=flexibility_upward + max_target_temperature_at_gdp_event,
            )
        elif now >= post_event_recovery_timestamp_dict["start"] and now < post_event_recovery_timestamp_dict["end"]:
            stop_post_event_hour = (post_event_recovery_timestamp_dict["end"]).hour

            max_target_temperature_post_event_recovery, _ = self._get_target_from_schedule(
                stop_post_event_hour, day_of_week, devices_states, configuration, device_id
            )

            init_zone_temperature_after_gdp_event = (
                self._get_target_from_schedule(
                    (post_event_recovery_timestamp_dict["start"]).hour - 1,  # One hour before recovery
                    day_of_week,
                    devices_states,
                    configuration,
                    device_id,
                )[0]
                or 0.0
            )

            target_temperature = self._conditioning_ramping(
                ramping_time=int(
                    (
                        post_event_recovery_timestamp_dict["end"] - post_event_recovery_timestamp_dict["start"]
                    ).total_seconds()
                ),
                elapsed_time=int((now - post_event_recovery_timestamp_dict["start"]).total_seconds()),
                initial_value=init_zone_temperature_after_gdp_event,
                target_value=max_target_temperature_post_event_recovery,  # No flexibility during recovery, just return to target
            )
        else:
            target_temperature = init_target_temperature  # No adjustment outside GDP event hours to keep user comfort

        return target_temperature

    def _time_str_to_minutes(self, time_string: str) -> int:
        hour_str, minute_str = time_string.split(":")
        return int(hour_str) * 60 + int(minute_str)

    # TODO: Refactor this function to handle hours and minutes in schedule time slots (ex.: 10h30-15h45)
    def _get_target_from_schedule(
        self,
        current_hour: int,
        day_of_week: int,
        devices_states: Dict[str, Any],
        configuration: Dict[str, Any],
        device_id: str,
    ) -> Tuple[float, bool]:
        """
        Retourne la dernière consigne applicable (en °C) à partir de la cédule hebdomadaire ou s'il y a eu une modification
        manuelle récente (ex.: override sur thermostat ou IHD) qui s'applique, le 2e parametre sera True si un override est
        présent.

        - `day_of_week`: 0 = dimanche, 1 = lundi, ..., 6 = samedi
        - `schedule["setpoint"]`: {
            "0": { "22:00": "18", ... },
            "1": { "00:00": "18", "06:00": "21", ... },
            ...
        }

        La recherche se fait en reculant dans le temps (même jour puis jours précédents,
        en bouclant sur la semaine) jusqu'à trouver la dernière entrée applicable.
        """
        device_settings = configuration.get(device_id, {})
        schedule = device_settings.get("schedule", {})

        if "setpoint" not in schedule:
            logger.warning(
                "No schedule found in configuration for device %s, returning default value for target temperature",
                device_id,
            )
        else:
            setpoint_schedule = schedule["setpoint"]

            current_minutes = current_hour * 60

            # On recule sur un maximum de 7 jours (une semaine complète)
            for offset in range(0, 7):
                day = (day_of_week - offset) % 7
                day_key = str(day)
                day_schedule = setpoint_schedule.get(day_key)

                if not day_schedule:
                    continue

                # Convertit les entrées "HH:MM": temperature -> minutes: temperature
                converted_schedule: list[tuple[int, float]] = []
                for time_string, target_temperature_raw_value in day_schedule.items():
                    minutes = self._time_str_to_minutes(time_string)
                    target_temperature = float(target_temperature_raw_value)
                    converted_schedule.append((minutes, target_temperature))

                if not converted_schedule:
                    continue

                # Trie par heure croissante
                converted_schedule.sort(key=lambda x: x[0])

                if offset == 0:
                    # Même jour: on ne garde que les entrées <= heure actuelle
                    candidates = [
                        schedule_entry for schedule_entry in converted_schedule if schedule_entry[0] <= current_minutes
                    ]

                    if not candidates:
                        continue

                    # Dernière entrée avant ou à l'heure courante
                    minutes, target_temperature = candidates[-1]

                    # Convert schedule entry time to timestamp for comparison with manual override entries
                    schedule_entry_timestamp = (
                        datetime.combine(
                            datetime.now().astimezone().date() - timedelta(days=offset),
                            time(hour=minutes // 60, minute=minutes % 60),
                        )
                        .astimezone()
                        .timestamp()
                    )

                    # Check for manual override after this schedule entry
                    manual_override_temperature = self._get_manual_override_temperature(
                        schedule_entry_timestamp, devices_states, configuration, device_id
                    )

                    if manual_override_temperature is not None:
                        return manual_override_temperature, True

                    return target_temperature, False
                else:
                    # Jour précédent dans la semaine: toute heure de ce jour est "avant" maintenant.
                    # On prend simplement la dernière entrée de ce jour.
                    minutes, target_temperature = converted_schedule[-1]

                    # Convert schedule entry time to timestamp for comparison with manual override entries
                    schedule_entry_timestamp = (
                        datetime.combine(
                            datetime.now().astimezone().date() - timedelta(days=offset),
                            time(hour=minutes // 60, minute=minutes % 60),
                        )
                        .astimezone()
                        .timestamp()
                    )

                    # Check for manual override newer than this schedule entry
                    manual_override_temperature = self._get_manual_override_temperature(
                        schedule_entry_timestamp, devices_states, configuration, device_id
                    )

                    if manual_override_temperature is not None:
                        return manual_override_temperature, True

                    return target_temperature, False

        # Aucune consigne trouvée sur la semaine ou aucune cédule trouvée pour ce device
        # Retourner le setpoint par default des parametres (qui pourrait être un default, override manuel)
        default_temperature = device_settings.get("setpoint", {}).get("value")
        if default_temperature is None:
            logger.error(
                "No target temperature found in schedule for device %s and no default value set, returning 21C as fallback",
                device_id,
            )
            default_temperature = 21.0

        return default_temperature, False

    def _get_manual_override_temperature(
        self,
        schedule_entry_timestamp: float,
        devices_states: Dict[str, Any],
        configuration: Dict[str, Any],
        device_id: str,
    ) -> float | None:
        # Check if there is a manual override entry in the configuration for the device_id (from IHD)
        setpoint_configuration = configuration.get(device_id, {}).get("setpoint", {})

        if setpoint_configuration.get("source", {}) == "parameter":
            timestamp_value = setpoint_configuration.get("timestamp", 0)
            override_timestamp = datetime.fromisoformat(timestamp_value).timestamp()
            if override_timestamp > schedule_entry_timestamp:
                # Manual override is newer than the schedule entry, it should take precedence
                override_value = setpoint_configuration.get("value")

                if override_value is not None:
                    logger.debug(
                        f"Device {device_id}: manual override from IHD with target temperature = {override_value} °C"
                    )
                    return float(override_value)

        # TODO Check if there is a manual override done by looking at devices_states (from Home Assistant/Thermostat)
        device_state = devices_states.get(device_id, {})

        return None

    def _conditioning_ramping(
        self, ramping_time: int, elapsed_time: int, initial_value: float, target_value: float
    ) -> float:
        # Shorten ramping time by 15 minutes to reach target earlier and heat/cool more efficiently
        ramping_time_short = ramping_time - 900

        # Clamp elapsed time between 0 and ramping_time_short
        if elapsed_time <= 0:
            return round(initial_value, 2)
        if elapsed_time >= ramping_time_short:
            return round(target_value, 2)

        # Linear interpolation
        ratio = elapsed_time / ramping_time_short
        y = initial_value + (target_value - initial_value) * ratio
        return round(y, 2)

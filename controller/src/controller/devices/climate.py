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

        # Per-zone state for the resistive (auxiliary) backup heating, persisted across control cycles since this
        # controller instance lives for the whole process lifetime (see main.py's scheduled loop).
        self._aux_active: Dict[str, bool] = {}
        self._aux_last_on: Dict[str, datetime] = {}
        self._aux_last_off: Dict[str, datetime] = {}
        self._heat_demand_start: Dict[str, datetime] = {}

        # Per-zone state for the heat pump setpoint hysteresis (see _get_stable_heat_pump_setpoint), to avoid
        # sending a new setpoint to the heat pump on every control cycle just because of sensor noise.
        self._heat_pump_setpoint: Dict[str, int] = {}

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

        target_temperature = self._get_target_temperature(zone_id, zone_configuration, devices_states, peak_event)

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
        temp_tolerance = 0.3  # Degrees Celsius tolerance to avoid excessive on/off cycling
        # TODO: Get this value from configuration or compute it based on data (automatic) instead of hardcoding it here.
        # TODO: Use a value for cooling and others for heating instead of a single value for both modes ?
        # Maximum degrees Celsius pushed on top of target so the heat pump modulates towards its maximum output
        # while it is still catching up. This offset is tapered down as the room approaches the target (see
        # _get_heat_pump_heating_offset) so the heat pump settles back near the real target instead of chasing
        # target + offset forever, which would otherwise cause a permanent overshoot/discomfort once the heat pump
        # is actually capable of reaching it (e.g. in mild weather).
        heat_pump_calibration_offset = 2.0

        # Get indoor temperature trend to detect whether the heat pump alone is actually catching up
        temp_trend = self._get_indoor_temperature_trend(environment_sensor_id)

        # Set heat pump setpoint and zone setpoints based on mode
        if control_mode == utils.ControlMode.HEATING:
            # Push the heat pump above target so it modulates at its maximum capacity, but only as much as the
            # current heating deficit warrants, to avoid overheating once the room is at (or near) the target.
            heat_pump_offset = self._get_heat_pump_heating_offset(
                target_temperature, indoor_temperature, heat_pump_calibration_offset
            )
            control_actions[heat_pump_device_id]["state"] = "heat"
            control_actions[heat_pump_device_id]["setpoint"] = self._get_stable_heat_pump_setpoint(
                zone_id, target_temperature + heat_pump_offset
            )

            # The resistive backup only turns on when the heat pump alone hasn't been able to keep up with comfort
            aux_active = self._update_aux_backup_state(
                zone_id, target_temperature, indoor_temperature, temp_trend, temp_tolerance
            )

            for device_id in zone_devices.keys():
                if device_id != heat_pump_device_id:
                    control_actions[device_id] = target_temperature if aux_active else 5  # 5 => effectively off

        else:  # control_mode == utils.ControlMode.COOLING:
            # Set heat pump setpoint with calibration offset
            control_actions[heat_pump_device_id]["state"] = "cool"
            control_actions[heat_pump_device_id]["setpoint"] = self._get_stable_heat_pump_setpoint(
                zone_id, target_temperature + heat_pump_calibration_offset
            )

            # Resistive heating has no role in cooling mode: force it off and clear any pending backup state
            self._clear_aux_backup_state(zone_id)
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

    def _update_aux_backup_state(
        self,
        zone_id: str,
        target_temperature: float,
        indoor_temperature: float | None,
        temp_trend: float | None,
        temp_tolerance: float,
    ) -> bool:
        """
        Decides whether the resistive (auxiliary) backup heating should be active for the zone.

        The heat pump is always the primary heat source and is pushed towards its maximum output via the
        calibration offset applied in the caller. The resistive backup is only activated when, after an adaptive
        grace period, the heat pump alone hasn't been able to close the gap with the target temperature. The grace
        period shrinks as the temperature gap grows, so a large deviation triggers the backup much faster than a
        small one, and an actively dropping indoor temperature triggers it immediately.
        """
        # Anti short-cycling: minimum time the backup must stay on/off once it changes state, to be tuned
        min_runtime_minutes = 15.0
        min_off_time_minutes = 15.0

        now = datetime.now().astimezone()

        if indoor_temperature is None:
            logger.warning(f"Zone {zone_id}: indoor temperature unknown, keeping auxiliary backup state unchanged")
            return self._aux_active.get(zone_id, False)

        temp_error = target_temperature - indoor_temperature  # positive => room is colder than target

        if temp_error <= temp_tolerance:
            # Comfort is met (or the zone is too warm): no heat demand, reset the grace period timer
            self._heat_demand_start.pop(zone_id, None)
            should_activate = False
        else:
            demand_start = self._heat_demand_start.setdefault(zone_id, now)
            elapsed_minutes = (now - demand_start).total_seconds() / 60.0
            activation_window_minutes = self._compute_aux_activation_window(temp_error, temp_tolerance)

            # React immediately if the room is actively losing heat despite the heat pump running
            temp_actively_dropping = temp_trend is not None and temp_trend < -0.01

            should_activate = temp_actively_dropping or elapsed_minutes >= activation_window_minutes

            logger.debug(
                f"Zone {zone_id}: temp error={temp_error:.2f} C, trend={temp_trend}, elapsed={elapsed_minutes:.1f} "
                f"min, activation window={activation_window_minutes:.1f} min, should_activate={should_activate}"
            )

        aux_active = self._aux_active.get(zone_id, False)
        last_on = self._aux_last_on.get(zone_id)
        last_off = self._aux_last_off.get(zone_id)

        can_enable = last_off is None or (now - last_off).total_seconds() / 60.0 >= min_off_time_minutes
        can_disable = last_on is None or (now - last_on).total_seconds() / 60.0 >= min_runtime_minutes

        if should_activate and not aux_active and can_enable:
            aux_active = True
            self._aux_last_on[zone_id] = now
        elif not should_activate and aux_active and can_disable:
            aux_active = False
            self._aux_last_off[zone_id] = now

        self._aux_active[zone_id] = aux_active
        return aux_active

    def _compute_aux_activation_window(self, temp_error: float, temp_tolerance: float) -> float:
        """
        Computes how long (in minutes) the heat pump alone is given to close the gap with the target temperature
        before the resistive backup is allowed to kick in. The window shrinks linearly as the temperature error
        grows, so a large deviation reacts much faster than a small one.
        """
        base_window_minutes = 20.0  # Grace period for a small temperature gap, to be tuned
        fast_window_minutes = 3.0  # Minimum grace period, used once the error reaches the fast-reaction threshold
        fast_reaction_error_threshold = 1.5  # Degrees Celsius gap considered "large" and requiring a fast reaction

        if temp_error >= fast_reaction_error_threshold:
            return fast_window_minutes

        ratio = (temp_error - temp_tolerance) / (fast_reaction_error_threshold - temp_tolerance)
        ratio = max(0.0, min(1.0, ratio))
        return base_window_minutes - ratio * (base_window_minutes - fast_window_minutes)

    def _clear_aux_backup_state(self, zone_id: str) -> None:
        self._aux_active[zone_id] = False
        self._heat_demand_start.pop(zone_id, None)

    def _get_heat_pump_heating_offset(
        self,
        target_temperature: float,
        indoor_temperature: float | None,
        max_offset: float,
    ) -> float:
        """
        Computes how far above the target the heat pump setpoint should be pushed, in heating mode, to keep it
        modulating at (or near) its maximum output while it is still catching up.

        The offset is capped at the current heating deficit (target - indoor), so it tapers down to zero as the
        room approaches/exceeds the target instead of staying pinned at `max_offset`. Without this tapering, the
        heat pump would keep chasing target + max_offset even once comfort is reached, causing a permanent
        overshoot (and discomfort) any time the heat pump is actually capable of reaching that point, e.g. in mild
        weather.
        """
        if indoor_temperature is None:
            # Sensor unavailable: fall back to the full offset (conservative, avoids under-heating)
            return max_offset

        heating_deficit = target_temperature - indoor_temperature  # positive => room is colder than target
        return min(max_offset, max(0.0, heating_deficit))

    def _get_stable_heat_pump_setpoint(
        self,
        zone_id: str,
        raw_setpoint: float,
        hysteresis: float = 0.75,
    ) -> int:
        """
        Converts the continuously-computed raw setpoint (target + offset) into a stable, integer setpoint to send
        to the heat pump, applying hysteresis to avoid changing it on every control cycle.

        Without this, since the heating offset now varies continuously with indoor temperature (see
        _get_heat_pump_heating_offset), `math.ceil(raw_setpoint)` could flip back and forth between two
        consecutive integers on every cycle (every 120 s, see main.py) whenever `raw_setpoint` hovers near an
        integer boundary, due to nothing more than normal sensor noise. Repeatedly re-sending a changed setpoint
        to the heat pump this often could induce excessive internal compressor modulation/cycling and affect its
        durability.

        The previously applied setpoint is kept as long as `raw_setpoint` hasn't drifted away from it by more
        than `hysteresis` degrees; once it does, a new integer setpoint is computed and becomes the new reference
        point. This still reacts to real, sustained changes in heating demand, just not to momentary noise.
        """
        previous_setpoint = self._heat_pump_setpoint.get(zone_id)

        if previous_setpoint is None or abs(raw_setpoint - previous_setpoint) >= hysteresis:
            previous_setpoint = math.ceil(raw_setpoint)
            self._heat_pump_setpoint[zone_id] = previous_setpoint

        return previous_setpoint

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
        self,
        device_id: str,
        device_configuration: Dict[str, Any],
        devices_states: Dict[str, Any],
        gdp_event: PeakEvent | None,
    ) -> float:

        # Determine day type and current hour
        now = datetime.now().astimezone()
        current_hour = now.hour
        today = now.weekday()  # Current day of the week as an integer (0=Monday, 6=Sunday)
        day_of_week = (today + 1) % 7  # Convert to Sunday=0, Monday=1, ..., Saturday=6

        # Get initial target temperature from schedule or manual override
        init_target_temperature, manual_override = self._get_target_from_schedule(
            current_hour, day_of_week, devices_states, device_configuration, device_id
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
            init_target_temperature, now, day_of_week, devices_states, device_configuration, device_id, gdp_event
        )

        logger.debug(f"{device_id}: target temperature = {target_temperature} °C")
        return target_temperature

    def _get_target_from_gdp_event(
        self,
        init_target_temperature: float,
        now: datetime,
        day_of_week: int,
        devices_states: Dict[str, Any],
        device_configuration: Dict[str, Any],
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
        flexibility_upward = float(device_configuration.get("flexibility_upward", {}).get("value", 0.0))
        flexibility_downward = float(device_configuration.get("flexibility_downward", {}).get("value", 0.0))
        zone_preconditioning = device_configuration.get("preconditioning", {}).get("value", "false").lower() == "true"

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
                self._get_target_from_schedule(
                    start_gdp_hour, day_of_week, devices_states, device_configuration, device_id
                )
                or 0.0
            )

            for hour in range(start_gdp_hour, stop_gdp_hour):
                max_target_temperature_at_gdp_event = max(
                    max_target_temperature_at_gdp_event,
                    (
                        self._get_target_from_schedule(
                            hour, day_of_week, devices_states, device_configuration, device_id
                        )[0]
                    )
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
                        start_preconditioning_hour, day_of_week, devices_states, device_configuration, device_id
                    )[0]
                )
                or 0.0,
                target_value=flexibility_upward + max_target_temperature_at_gdp_event,
            )
        elif now >= post_event_recovery_timestamp_dict["start"] and now < post_event_recovery_timestamp_dict["end"]:
            stop_post_event_hour = (post_event_recovery_timestamp_dict["end"]).hour

            max_target_temperature_post_event_recovery, _ = self._get_target_from_schedule(
                stop_post_event_hour, day_of_week, devices_states, device_configuration, device_id
            )

            init_zone_temperature_after_gdp_event = (
                self._get_target_from_schedule(
                    (post_event_recovery_timestamp_dict["start"]).hour - 1,  # One hour before recovery
                    day_of_week,
                    devices_states,
                    device_configuration,
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
        device_configuration: Dict[str, Any],
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
        schedule = device_configuration.get("schedule", {})

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
                        schedule_entry_timestamp, devices_states, device_configuration, device_id
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
                        schedule_entry_timestamp, devices_states, device_configuration, device_id
                    )

                    if manual_override_temperature is not None:
                        return manual_override_temperature, True

                    return target_temperature, False

        # Aucune consigne trouvée sur la semaine ou aucune cédule trouvée pour ce device
        # Retourner le setpoint par default des parametres (qui pourrait être un default, override manuel)
        default_temperature = device_configuration.get("setpoint", {}).get("value")
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
        device_configuration: Dict[str, Any],
        device_id: str,
    ) -> float | None:
        # Check if there is a manual override entry in the configuration for the device_id (from IHD)
        setpoint_configuration = device_configuration.get("setpoint", {})

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

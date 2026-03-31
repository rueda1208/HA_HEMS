import logging
import math
import os

from typing import Any, Dict, List

import numpy as np
import pandas as pd
import requests

from sqlalchemy import create_engine, text

from controller.utils import utils
from controller.utils.peak_events import PeakEvent


logger = logging.getLogger(__name__)


# Get TimescaleDB connection parameters
POSTGRES_DB_NAME = os.getenv("POSTGRES_NAME", "homeassistant")
POSTGRES_DB_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_DB_HOST = os.getenv("POSTGRES_HOST", "77b2833f-timescaledb")
POSTGRES_DB_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "homeassistant")

# Create database connection URL
db_url = (
    f"postgresql://{POSTGRES_DB_USER}:{POSTGRES_DB_PASSWORD}@{POSTGRES_DB_HOST}:{POSTGRES_DB_PORT}/{POSTGRES_DB_NAME}"
)

# Create SQLAlchemy engine
postgres_db_engine = create_engine(db_url)

HEAT_PUMP_ENTITY_ID = "climate.heat_pump"


class HomeAssistantDeviceInterface:
    _url_base: str
    _headers: Dict[str, str]

    def __init__(self, base_url: str, token: str) -> None:
        self._url_base = base_url
        self._headers = {"Authorization": f"Bearer {token}", "content-type": "application/json"}

    def get_devices_states(self) -> Dict[str, Any]:
        """
        Retrieves the state of all the devices from the Home Assistant API.
        """
        response = requests.get(f"{self._url_base}/api/states", headers=self._headers)
        response.raise_for_status()
        response_json: List = response.json()

        devices_states: Dict[str, Any] = {}

        for state in response_json:
            entity_id = state.pop("entity_id")
            devices_states[entity_id] = state

        return devices_states

    def get_control_actions(
        self, devices_states: Dict[str, Any], heat_pump_cop_models: Dict[utils.HeatPumpMode, np.poly1d]
    ) -> Dict[str, Any]:
        gdp_event = utils.retrieve_gdp_event()
        if gdp_event:
            logger.info("GDP event detected, adjusting control strategy accordingly")
        else:
            logger.info("No GDP event detected, proceeding with normal control strategy")

        logger.info("Determining control actions for heat pump impacted zones")

        # Calculate heat pump COP based on current outside temperature
        weather_entity_id = os.getenv("WEATHER_ENTITY_ID", "weather.home")
        outside_temperature = devices_states.get(weather_entity_id, {}).get("attributes", {}).get("temperature")
        if outside_temperature is None:
            raise ValueError("Outside temperature is None, cannot compute heat pump COP.")

        configuration = utils.retrieve_device_configuration()

        hvac_mode_str = configuration.get(HEAT_PUMP_ENTITY_ID, {}).get("hvac_mode", {}).get("value", "off")
        if hvac_mode_str == utils.HeatPumpMode.COOL.value:
            heat_pump_mode = utils.HeatPumpMode.COOL
        elif hvac_mode_str == utils.HeatPumpMode.HEAT.value:
            heat_pump_mode = utils.HeatPumpMode.HEAT
        else:
            heat_pump_mode = utils.HeatPumpMode.OFF

        if heat_pump_mode == utils.HeatPumpMode.OFF:
            heat_pump_cop = 0.0
            logger.debug("Heat pump is off, COP set to 0.0")
        else:
            heat_pump_cop_model = heat_pump_cop_models[heat_pump_mode]
            heat_pump_cop = heat_pump_cop_model(outside_temperature)

        logger.info(
            f"Outside temperature: {outside_temperature} C, Heat Pump COP: {heat_pump_cop:.2f}, Heat Pump mode: {heat_pump_mode}"  # noqa: E501
        )

        # Select zones with heat pump impact
        zones_with_hp_impact = utils.select_zones_with_hp_impact(HEAT_PUMP_ENTITY_ID, configuration)
        logger.debug(f"Zones with heat pump impact: {list(zones_with_hp_impact.keys())}")

        heat_pump_enabled = (
            str(
                configuration.get(HEAT_PUMP_ENTITY_ID, {}).get("automated_control_enabled", {}).get("value", "false")
            ).lower()
            == "true"
        )

        if not zones_with_hp_impact:
            control_actions = {}

            if not heat_pump_enabled:
                logger.warning("Heat pump is disabled in configuration. Skipping heat pump control logic.")

                control_actions[HEAT_PUMP_ENTITY_ID] = {
                    "state": "off",
                    "setpoint": None,
                    "user_pref": -99.0,
                }
            else:
                logger.warning("No zones with heat pump impact found. Using user preferences for heat pump control.")

                hp_target_temperature = utils.get_target_temperature(
                    HEAT_PUMP_ENTITY_ID, devices_states, configuration, gdp_event
                )
                logger.debug(f"Heat pump target temperature: {hp_target_temperature} C")

                control_actions[HEAT_PUMP_ENTITY_ID] = {
                    "state": heat_pump_mode,
                    "setpoint": hp_target_temperature,
                    "user_pref": hp_target_temperature,
                }
        else:
            # Get state information for zones with heat pump impact
            zones_with_hp_impact_state = self._get_zone_metrics(
                configuration,
                zones_with_hp_impact,
                devices_states,
                gdp_event,
            )

            # Determine control actions for heat pump impacted zones
            # TODO: Integrate preconditioning logic here in the future
            control_actions = self._control_logic_hp(
                zones_with_hp_impact_state, heat_pump_mode, heat_pump_cop, devices_states
            )
            logger.debug(f"Control actions for heat pump impacted zones: {control_actions}")

        # --------------------------------------------------------- #
        # ---------------- Thermostat Control Logic --------------- #
        # --------------------------------------------------------- #
        logger.info("Determining control actions for non-heat pump impacted zones")

        # Select zones with heat pump impact
        zones_without_hp_impact_state = utils.select_zones_without_hp_impact(HEAT_PUMP_ENTITY_ID, configuration)
        logger.debug(f"Zones without heat pump impact: {zones_without_hp_impact_state}")

        if not zones_without_hp_impact_state:
            logger.info("No zones without heat pump impact found, skipping thermostat control logic.")
            return control_actions

        # Get state information for zones without heat pump impact
        zones_without_hp_impact_state = self._get_zone_metrics(
            configuration,
            zones_without_hp_impact_state,
            devices_states,
            gdp_event,
        )

        # Determine control actions for zones without heat pump impact
        thermostat_control_actions = self._control_logic_thermostat(zones_without_hp_impact_state)
        logger.debug(f"Thermostat setpoint for non-heat pump impacted zones: {thermostat_control_actions}")

        # Merge control actions
        control_actions.update(thermostat_control_actions)
        logger.debug(f"Final control actions: {control_actions}")

        return control_actions

    def execute_control_actions(self, control_actions: Dict[str, Any], devices_states: Dict[str, Any]) -> None:
        credentials = {
            "api_url": f"{self._url_base}/api/services/climate/set_temperature",
            "headers": self._headers,
        }

        if not control_actions:
            logger.info("No control actions to execute")
            return

        for entity_id, action in control_actions.items():
            if entity_id == HEAT_PUMP_ENTITY_ID:
                # Set heat pump mode
                if action["state"].value == devices_states.get(HEAT_PUMP_ENTITY_ID, {}).get("state"):
                    logger.info(f"No change to heat pump state requested (remains {action['state']})")
                else:
                    logger.info(f"Setting heat pump state to {action['state']}")
                    credentials["api_url"] = f"{self._url_base}/api/services/climate/set_hvac_mode"
                    params = {
                        "action": {"entity_id": HEAT_PUMP_ENTITY_ID, "hvac_mode": action["state"].value},
                    }

                    self._send_action(credentials, params)

                # Set heat pump temperature setpoint
                if action["state"] == utils.HeatPumpMode.OFF:
                    logger.info("Heat pump turned off, skipping setpoint adjustment")
                else:
                    setpoint = action["setpoint"]
                    if setpoint == devices_states.get(HEAT_PUMP_ENTITY_ID, {}).get("temperature"):
                        logger.info(f"No change to heat pump setpoint requested (remains {setpoint} C)")
                    else:
                        logger.info(f"Setting heat pump setpoint to {setpoint} C")
                        credentials["api_url"] = f"{self._url_base}/api/services/climate/set_temperature"
                        params = {
                            "action": {"entity_id": HEAT_PUMP_ENTITY_ID, "temperature": setpoint},
                        }

                        self._send_action(credentials, params)

                # Save user preference in database
                self._save_in_database(
                    data={
                        "metric_type": "control",
                        "device_id": HEAT_PUMP_ENTITY_ID,
                        "name": "user_pref",
                        "value": action["user_pref"],
                    }
                )
            else:
                # Set zone temperature setpoint
                if action == devices_states.get(entity_id, {}).get("temperature"):
                    logger.info(f"No change to zone {entity_id} temperature requested (remains {action} C)")
                else:
                    logger.info(f"Setting zone {entity_id} temperature to {action} C")
                    credentials["api_url"] = f"{self._url_base}/api/services/climate/set_temperature"
                    params = {
                        "action": {"entity_id": entity_id, "temperature": action},
                    }
                    self._send_action(credentials, params)

    def _control_logic_hp(
        self,
        zones_with_hp_impact_state: Dict[str, Any],
        heat_pump_mode: utils.HeatPumpMode,
        heat_pump_cop: float,
        devices_states: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Get mean inside and target temperatures across all zones
        # TODO: Validate if mean is the best approach here. Maybe consider only the coldest/hottest zone? or a weighted average? or zone with highest HP impact?
        environment_sensor_id = str(os.getenv("ENVIRONMENT_SENSOR_ID"))
        inside_temp = self._get_indoor_temperature(environment_sensor_id, devices_states)
        target_temp = np.mean([float(state["target_temperature"]) for state in zones_with_hp_impact_state.values()])
        logger.debug(f"Mean inside temperature: {inside_temp} C, Mean target temperature: {target_temp} C")

        # Determine control action for each zone based on heat pump mode and COP
        control_actions: Dict[str, Any] = {}

        control_actions[HEAT_PUMP_ENTITY_ID] = {
            "state": heat_pump_mode,
            "setpoint": None,
            "user_pref": target_temp,
        }

        # Configurable parameters for control logic
        max_heat_push = 1.5  # Maximum heating push (positive value) to avoid excessive heating, to be tuned based on system response and desired comfort levels
        max_cool_push = -2.0  # Maximum cooling push (negative value) to avoid excessive cooling, to be tuned based on system response and desired comfort levels

        Kp = 0.35  # Proportional gain for temperature error adjustment, to be tuned based on system response and desired aggressiveness of control actions

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
        if heat_pump_mode == utils.HeatPumpMode.HEAT:
            # Set heat pump setpoint with calibration offset
            control_actions[HEAT_PUMP_ENTITY_ID]["setpoint"] = math.ceil(target_temp + heat_pump_calibration_offset)

            # Calculate temperature error and trend to adjust thermostat setpoints dynamically
            temp_error = target_temp - inside_temp  # + = frío
            thermostat_adjustment = 0.0

            # Proportional control adjustment based on temperature error
            proportional_adjustment = Kp * temp_error

            # Thermostat adjustment logic
            if inside_temp <= target_temp - temp_tolerance:
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

            elif inside_temp >= target_temp + temp_tolerance:
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
            for zone_id in zones_with_hp_impact_state.keys():
                control_actions[zone_id] = target_temp + thermostat_adjustment

        elif heat_pump_mode == utils.HeatPumpMode.COOL:
            # Set heat pump setpoint with calibration offset
            control_actions[HEAT_PUMP_ENTITY_ID]["setpoint"] = math.ceil(target_temp + heat_pump_calibration_offset)

            # Turn off auxiliary heating in cooling mode
            for zone_id in zones_with_hp_impact_state.keys():
                control_actions[zone_id] = 5  # Use a lower setpoint to ensure to turn off heating

        else:
            for zone_id in zones_with_hp_impact_state.keys():
                control_actions[zone_id] = 10  # Set low setpoint to turn off heating

        return control_actions

    def _control_logic_thermostat(self, zones_without_hp_impact_state: Dict[str, Any]) -> Dict:
        control_actions = {}

        for zone_id, state in zones_without_hp_impact_state.items():
            inside_temp = state["inside_temperature"]
            target_temp = state["target_temperature"]
            control_actions[zone_id] = target_temp  # Apply target temperature directly as setpoint
            logger.debug(f"Zone {zone_id} - Inside temperature: {inside_temp} C, Target temperature: {target_temp} C")

        return control_actions

    def _get_zone_metrics(
        self,
        configuration: Dict[str, Any],
        zones_to_check: Dict,
        devices_states: Dict[str, Any],
        gdp_event: PeakEvent | None,
    ) -> Dict[str, Any]:
        zone_metrics = {}

        for zone_id, hp_impact in zones_to_check.items():
            # Get current inside temperature
            inside_temperature = devices_states.get(zone_id, {}).get("attributes", {}).get("current_temperature")
            logger.debug(f"Zone {zone_id} - Inside temperature: {inside_temperature} C")

            if inside_temperature is None:
                logger.warning(f"Inside temperature for zone {zone_id} is None, skipping control action.")
                continue

            # Get target temperature from user preferences
            target_temperature = utils.get_target_temperature(zone_id, configuration, devices_states, gdp_event)
            logger.debug(f"Zone {zone_id} - Target temperature: {target_temperature} C")

            if target_temperature is None:
                logger.warning(f"Target temperature for zone {zone_id} is None, skipping control action.")
                continue

            # Store state information
            zone_metrics[zone_id] = {
                "inside_temperature": inside_temperature,
                "target_temperature": target_temperature,
                "heat_pump_impact": hp_impact,
            }
        return zone_metrics

    def _send_action(self, credentials: dict, params: dict) -> None:
        api_url = credentials["api_url"]
        headers = credentials["headers"]
        action = params["action"]

        response = requests.post(api_url, headers=headers, json=action)
        response.raise_for_status()
        logger.debug("Device %s requested to apply action %s", action["entity_id"], action)
        self._save_control_actions(control_actions=action)

    def _save_control_actions(self, control_actions: Dict[str, Any]) -> None:
        if "hvac_mode" in control_actions:
            mapping = {"off": 0, "heat": 1, "cool": 2, "auto": 3, "dry": 4, "fan_only": 5, "unknown": np.nan}
            action_name = "hvac_mode"
            action_value = mapping.get(control_actions.get("hvac_mode", "unknown"), np.nan)
        elif "temperature" in control_actions:
            action_name = "setpoint"
            action_value = control_actions.get("temperature", np.nan)
        else:
            logger.warning("No valid control action to save")
            return

        self._save_in_database(
            data={
                "metric_type": "control",
                "device_id": control_actions.get("entity_id", "unknown"),
                "name": action_name,
                "value": action_value,
            }
        )

        logger.info("Control actions saved to TimescaleDB")

    def _save_in_database(self, data: Dict[str, Any]) -> None:
        data_to_save = pd.DataFrame(
            data=[  # Single row of data
                [data.get("metric_type", "unknown")]
                + [data.get("device_id", "unknown")]
                + [data.get("name", "unknown")]
                + [data.get("value", np.nan)]
            ],
            index=[pd.Timestamp.now(tz="UTC").replace(microsecond=0)],  # Single timestamp index
            columns=["metric_type", "device_id", "name", "value"],  # Column names
        )

        # Set the index name to 'time'
        data_to_save.index.name = "time"

        # Save to TimescaleDB
        data_to_save.to_sql(
            name="space_heating",
            con=postgres_db_engine,
            if_exists="append",
        )

        logger.debug("Data saved to TimescaleDB")

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

    def _get_indoor_temperature_trend(self, environment_sensor_id: str) -> float | None:
        """Calculates the indoor temperature trend based on historical data from TimescaleDB."""
        query = text(f"""
            SELECT time, value::double precision AS value
            FROM space_heating
            WHERE device_id = '{environment_sensor_id}'
                AND name = 'temperature'
                AND time > now() - interval '15 minutes'
            ORDER BY time ASC
            LIMIT 100;
        """)
        try:
            with postgres_db_engine.connect() as conn:
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
            A = np.vstack([times, np.ones(len(times))]).T
            slope, _ = np.linalg.lstsq(A, temperatures, rcond=None)[0]

            slope = float(np.clip(slope, -0.5, 0.5))
            return round(slope, 4)  # Temperature change per minute

        except Exception as e:
            logger.error(f"Error calculating temperature trend for sensor {environment_sensor_id}: {e}")
            return None

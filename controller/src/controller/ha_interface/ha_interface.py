import logging
import os
from functools import lru_cache

from typing import Any, Dict, List

import numpy as np
import pandas as pd
import requests

from sqlalchemy import create_engine

from controller.utils import utils


logger = logging.getLogger(__name__)


# Get TimescaleDB connection parameters
POSTGRES_DB_NAME = os.getenv("POSTGRES_NAME", "homeassistant")
POSTGRES_DB_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_DB_HOST = os.getenv("POSTGRES_HOST", "77b2833f-timescaledb")
POSTGRES_DB_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "homeassistant")

# Create database connection URL
db_url = (
    f"postgresql+psycopg2://{POSTGRES_DB_USER}:{POSTGRES_DB_PASSWORD}@{POSTGRES_DB_HOST}:{POSTGRES_DB_PORT}/{POSTGRES_DB_NAME}"
)

@lru_cache(maxsize=1)
def _get_postgres_db_engine():
    return create_engine(db_url)

class HomeAssistantDeviceInterface:
    _url_base: str
    _headers: Dict[str, str]
    _allow_control: bool

    def _get_temperature(self, device_state: Dict[str, Any]) -> Any:
        return device_state.get("attributes", {}).get("temperature", device_state.get("temperature"))

    def __init__(self, base_url: str, token: str, allow_control: bool = False) -> None:
        self._url_base = base_url
        self._headers = {"Authorization": f"Bearer {token}", "content-type": "application/json"}
        self._allow_control = allow_control

    def get_devices_states(self) -> Dict[str, Any]:
        """
        Retrieves the state of all the devices from the Home Assistant API.
        """
        response = requests.get(f"{self._url_base}/api/states", headers=self._headers, timeout=10)
        response.raise_for_status()
        response_json: List = response.json()

        devices_states: Dict[str, Any] = {}

        for state in response_json:
            state_data = dict(state)
            entity_id = state_data.pop("entity_id")
            devices_states[entity_id] = state_data

        return devices_states

    def execute_control_actions(self, control_actions: Dict[str, Any], devices_states: Dict[str, Any]) -> None:
        if not self._allow_control:
            if control_actions:
                logger.info("Suppressing %s control actions because actuator control is disabled", len(control_actions))
            return

        credentials = {
            "api_url": f"{self._url_base}/api/services/climate/set_temperature",
            "headers": self._headers,
        }

        if not control_actions:
            logger.info("No control actions to execute")
            return

        for entity_id, action in control_actions.items():
            if isinstance(action, dict):
                current_state = devices_states.get(entity_id, {})
                hvac_mode = action["state"]
                if hvac_mode == current_state.get("state"):
                    logger.info(f"No change to heat pump state requested for {entity_id} (remains {hvac_mode})")
                else:
                    logger.info(f"Setting heat pump state for {entity_id} to {hvac_mode}")
                    credentials["api_url"] = f"{self._url_base}/api/services/climate/set_hvac_mode"
                    params = {
                        "action": {"entity_id": entity_id, "hvac_mode": hvac_mode},
                    }

                    self._send_action(credentials, params)

                # Set heat pump temperature setpoint
                if hvac_mode == utils.ControlMode.OFF:
                    logger.info("Heat pump turned off, skipping setpoint adjustment")
                else:
                    setpoint = action["setpoint"]
                    if setpoint == self._get_temperature(current_state):
                        logger.info(f"No change to heat pump setpoint requested for {entity_id} (remains {setpoint} C)")
                    else:
                        logger.info(f"Setting heat pump setpoint for {entity_id} to {setpoint} C")
                        credentials["api_url"] = f"{self._url_base}/api/services/climate/set_temperature"
                        params = {
                            "action": {"entity_id": entity_id, "temperature": setpoint},
                        }

                        self._send_action(credentials, params)

                # Save user preference in database
                self._save_in_database(
                    data={
                        "metric_type": "control",
                        "device_id": entity_id,
                        "name": "user_pref",
                        "value": action["user_pref"],
                    }
                )
            else:
                # Set zone temperature setpoint
                if action == self._get_temperature(devices_states.get(entity_id, {})):
                    logger.info(f"No change to zone {entity_id} temperature requested (remains {action} C)")
                else:
                    logger.info(f"Setting zone {entity_id} temperature to {action} C")
                    credentials["api_url"] = f"{self._url_base}/api/services/climate/set_temperature"
                    params = {
                        "action": {"entity_id": entity_id, "temperature": action},
                    }
                    self._send_action(credentials, params)

    def _send_action(self, credentials: dict, params: dict) -> None:
        api_url = credentials["api_url"]
        headers = credentials["headers"]
        action = params["action"]

        response = requests.post(api_url, headers=headers, json=action, timeout=10)
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
            con=_get_postgres_db_engine(),
            if_exists="append",
        )

        logger.debug("Data saved to TimescaleDB")

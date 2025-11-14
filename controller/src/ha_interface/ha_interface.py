import os
import yaml
import logging
import numpy as np
from utils import utils
from requests import get, post
from abc import ABC, abstractmethod
from typing import Union, Any, Dict, List

logger = logging.getLogger(__name__)

CONFIG_FILE_PATH = os.getenv("CONFIG_FILE_PATH", "config/config.yaml")

class DeviceInterface(ABC):
    @abstractmethod
    def get(self, params: dict) -> float:
        pass

    @abstractmethod
    def set(self, params: dict) -> None:
        pass

class MockDeviceInterface(DeviceInterface):
    def get(self, params: dict) -> float:
        logger.info(f"Received get device state request: {params}")
        return 1.0

    def set(self, params: dict) -> None:
        logger.info(f"Received set device state request: {params}")

class HomeAssistantDeviceInterface(DeviceInterface):
    _host: str
    _port: int
    _headers: str

    def __init__(self, host: str, port: int, token: str) -> None:
        self._url_base = host + ":" + str(port) if port != 0 else host
        self._headers = {"Authorization": f"Bearer {token}", "content-type": "application/json"}

    def get_devices_list(self) -> List[str]:
        """
            Retrieves the installed devices from the Core API.
        """
        api_url = f"{self._url_base}/api/states"

        response = get(api_url, headers=self._headers, proxies={"http": None})
        response.raise_for_status()
        state_objects_array = response.json()
        
        devices = []
        for object in state_objects_array:
            if object["entity_id"].startswith(("climate.", "weather.")):
                devices.append(object["entity_id"])
                
        logger.debug(f"{len(devices)} climate devices retrieved from API")
        return devices

    def get_device_state(self, devices_id: Union[str, list]) -> float:
        """
            Retrieves the state of a device or multiple devices from the Home Assistant API.
        """
        credentials = {
            "api_url": f"{self._url_base}/api/states/",
            "headers": self._headers,
        }

        devices_state = None
        if isinstance(devices_id, list):
            for entity_id in devices_id:
                if not isinstance(entity_id, str):
                    raise ValueError("All device IDs must be strings.")
                else:
                    params = {"device": entity_id}
                    params["field"] = ["state", "last_changed"]
                    
                    if entity_id.startswith("weather."):
                        params["field"] += ["temperature"]
                    elif entity_id.startswith("climate."):
                        params["field"] += ["current_temperature","temperature"]
                    
                device_current_state = self.get(credentials=credentials, params=params)
                
                if devices_state is None:
                    devices_state = {entity_id: device_current_state}
                else:
                    devices_state[entity_id] = device_current_state
            
        return devices_state

    def get_control_actions(self, devices_state: Dict[str, Any], heat_pump_cop_models:np.poly1d) -> Dict[str, Any]:
        # --------------------------------------------------------- #
        # ---------------- Heat Pump Control Logic ---------------- #
        # --------------------------------------------------------- #
        logger.debug("Determining control actions for heat pump impacted zones")

        # Calculate heat pump COP based on current outside temperature
        outside_temperature = devices_state["weather.home"]["temperature"]
        if outside_temperature is None:
            raise ValueError("Outside temperature is None, cannot compute heat pump COP.")
        elif outside_temperature > 20:
            heat_pump_mode = "cool"
            logger.debug("Using cooling COP model")
        elif outside_temperature < 10:
            heat_pump_mode = "heat"
            logger.debug("Using heating COP model")
        else:
            heat_pump_mode = "off"
            logger.debug("Outside temperature in neutral range, heat pump turned off")

        if heat_pump_mode == "off":
            heat_pump_cop = 0.0
            logger.debug("Heat pump is off, COP set to 0.0")
        else:
            heat_pump_cop_model = heat_pump_cop_models[heat_pump_mode]
            heat_pump_cop = heat_pump_cop_model(outside_temperature)
        logger.debug(f"Outside temperature: {outside_temperature} C, Heat Pump COP: {heat_pump_cop:.2f}")

        # Select zones with heat pump impact
        zones_with_hp_impact = utils.select_zones_hp_impact(with_impact=True)
        logger.debug(f"Zones with heat pump impact: {list(zones_with_hp_impact.keys())}")
        if not zones_with_hp_impact:
            logger.warning("No zones with heat pump impact found. Using user preferences for heat pump control.")
            control_actions = {}
            hp_target_temperature = utils.get_target_temperature(zone_id="climate.heat_pump", hvac_mode=heat_pump_mode+"ing")
            logger.debug(f"Heat pump target temperature: {hp_target_temperature} C")
            control_actions["heat_pump"] = {"state":heat_pump_mode, "setpoint": hp_target_temperature}
        else:
            # Get state information for zones with heat pump impact
            zones_with_hp_impact_state = self._get_zone_metrics(
                zones_to_check=zones_with_hp_impact,
                devices_state=devices_state,
                )
            
            # Determine control actions for heat pump impacted zones
            control_actions = self._control_logic_hp(
                zones_with_hp_impact_state=zones_with_hp_impact_state,
                heat_pump_mode=heat_pump_mode,
                heat_pump_cop=heat_pump_cop,
                )
            logger.debug(f"Control actions for heat pump impacted zones: {control_actions}")

        # --------------------------------------------------------- #
        # ---------------- Thermostat Control Logic --------------- #
        # --------------------------------------------------------- #
        logger.debug("Determining control actions for non-heat pump impacted zones")

        # Select zones with heat pump impact
        zones_without_hp_impact_state = utils.select_zones_hp_impact(with_impact=False)
        logger.debug(f"Zones without heat pump impact: {zones_without_hp_impact_state}")
        if not zones_without_hp_impact_state:
            logger.info("No zones without heat pump impact found, skipping thermostat control logic.")
            return control_actions
        else:
            # Get state information for zones without heat pump impact
            zones_without_hp_impact_state = self._get_zone_metrics(
                zones_to_check=zones_without_hp_impact_state,
                devices_state=devices_state,
                )
            
            # Determine control actions for zones without heat pump impact
            thermostat_control_actions = self._control_logic_thermostat(
                zones_without_hp_impact_state=zones_without_hp_impact_state,
                )
            logger.debug(f"Thermostat setpoint for non-heat pump impacted zones: {thermostat_control_actions}")
            
            # Merge control actions
            control_actions.update(thermostat_control_actions)
            logger.debug(f"Final control actions: {control_actions}")

        return control_actions

    def execute_control_actions(self, control_actions: Dict[str, Any], devices_state:Dict[str, Any]) -> None:
        credentials = {
            "api_url": f"{self._url_base}/api/services/climate/set_temperature",
            "headers": self._headers,
        }

        if not control_actions:
            logger.info("No control actions to execute")
            return
        
        for device_id, action in control_actions.items():
            if device_id == "heat_pump":
                # Set heat pump mode 
                if action["state"] == devices_state.get("climate.heat_pump", {}).get("state"):
                    logger.info("No change to heat pump state requested")
                else:
                    logger.info(f"Setting heat pump state to {action['state']} in {action['mode']} mode") 
                    credentials["api_url"] = f"{self._url_base}/api/services/climate/set_hvac_mode"
                    params = {
                        "action": {"entity_id": "climate." + device_id, "hvac_mode": action["state"]},
                    }
                    self.set(credentials=credentials, params=params)
                
                # Set heat pump temperature setpoint
                if action["state"] == "off":
                    logger.info("Heat pump turned off, skipping setpoint adjustment")
                else:
                    setpoint = action["setpoint"]
                    if setpoint == devices_state.get("climate.heat_pump", {}).get("temperature"):
                        logger.info("No change to heat pump setpoint requested")
                    else:
                        logger.info(f"Setting heat pump setpoint to {setpoint} C")
                        credentials["api_url"] = f"{self._url_base}/api/services/climate/set_temperature"
                        params = {
                            "action": {"entity_id": "climate." + device_id, "temperature": setpoint},
                        }
                        self.set(credentials=credentials, params=params)
            else:
                # Set zone temperature setpoint
                if action == devices_state.get(device_id, {}).get("temperature"):
                    logger.info(f"No change to zone {device_id} temperature requested")
                else:
                    logger.info(f"Setting zone {device_id} temperature to {action} C")
                    credentials["api_url"] = f"{self._url_base}/api/services/climate/set_temperature"
                    params = {
                        "action": {"entity_id": device_id, "temperature": action},
                    }            
                    self.set(credentials=credentials, params=params)
    
    @staticmethod
    def _control_logic_hp(zones_with_hp_impact_state: Dict[str, Any], heat_pump_mode: str, heat_pump_cop: float) -> Union[float, None]:
        # Get mean inside and target temperatures across all zones
        # TODO: Validate if mean is the best approach here. Maybe consider only the coldest/hottest zone? or a weighted average? or zone with highest HP impact?
        inside_temp = np.mean([state["inside_temperature"] for state in zones_with_hp_impact_state.values()])
        target_temp = np.mean([state["target_temperature"] for state in zones_with_hp_impact_state.values()])
        logger.debug(f"Mean inside temperature: {inside_temp} C, Mean target temperature: {target_temp} C")
        
        # Determine control action for each zone based on heat pump mode and COP
        control_actions = {}
        control_actions["heat_pump"] = {"state":heat_pump_mode, "setpoint": None}
        
        # Set heat pump setpoint and zone setpoints based on mode
        if heat_pump_mode == "heat":
            if inside_temp < target_temp:
                control_actions["heat_pump"]["setpoint"] = target_temp + 1
                if heat_pump_cop >= 2.5:
                    for zone_id in zones_with_hp_impact_state.keys():
                        control_actions[zone_id] = target_temp - 1  # Slightly lower setpoint for zones
                else:
                    for zone_id in zones_with_hp_impact_state.keys():
                        control_actions[zone_id] = target_temp # Use auxiliary heating (e.g., electric baseboards)
            else:
                control_actions["heat_pump"]["setpoint"] = target_temp # Use heat pump only
                for zone_id in zones_with_hp_impact_state.keys():
                    control_actions[zone_id] = target_temp - 2  # Turn off auxiliary heating

        elif heat_pump_mode == "cool":
            # Turn off auxiliary heating in cooling mode
            for zone_id in zones_with_hp_impact_state.keys():
                control_actions[zone_id] = 5 # Use a lower setpoint to ensure to turn off heating
            
            if inside_temp > target_temp:
                control_actions["heat_pump"]["setpoint"] = target_temp - 1
            else:
                control_actions["heat_pump"]["setpoint"] = target_temp

        else:
            for zone_id in zones_with_hp_impact_state.keys():
                control_actions[zone_id] = 10 # Set low setpoint to turn off heating
        
        return control_actions

    @staticmethod
    def _control_logic_thermostat(zones_without_hp_impact_state: Dict[str, Any]) -> float:
        control_actions = {}
        for zone_id, state in zones_without_hp_impact_state.items():
            inside_temp = state["inside_temperature"]
            target_temp = state["target_temperature"]
            control_actions[zone_id] = target_temp # Apply target temperature directly as setpoint
            logger.debug(f"Zone {zone_id} - Inside temperature: {inside_temp} C, Target temperature: {target_temp} C")
        return control_actions
    
    @staticmethod
    def _get_zone_metrics(zones_to_check: Dict, devices_state:Dict[str, Any]) -> Dict[str, float]:
        zone_metrics = {}
        for zone_id, hp_impact in zones_to_check.items():
            # Get current inside temperature
            inside_temperature = devices_state.get(zone_id, {}).get("current_temperature")
            logger.debug(f"Zone {zone_id} - Inside temperature: {inside_temperature} C")
            if inside_temperature is None:
                logger.warning(f"Inside temperature for zone {zone_id} is None, skipping control action.")
                continue

            # Get target temperature from user preferences
            target_temperature = utils.get_target_temperature(zone_id=zone_id)
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

    @staticmethod
    def get(credentials:dict, params: dict) -> float:

        api_url = credentials["api_url"] + params["device"]
        headers = credentials["headers"]
        states_list = params["field"]

        response = get(api_url, headers=headers, proxies={"http": None})
        response.raise_for_status()
        logger.info("Device %s state successfully retrieved", params["device"])

        device_state = {}
        for state_to_get in states_list:
            if state_to_get in ["state","last_changed"]:
                dummy_device_state = response.json().get(state_to_get)
            else:
                dummy_device_state = response.json().get("attributes", {}).get(state_to_get, None)
        
            # logger.debug(f"Device {params['device']} - {state_to_get}: {dummy_device_state}")
            if isinstance(dummy_device_state, str):
                device_state[state_to_get] = dummy_device_state.lower()
            elif isinstance(dummy_device_state, (int, float)):
                device_state[state_to_get] = float(dummy_device_state)
            else:
                device_state[state_to_get] = dummy_device_state
        
        return device_state

    @staticmethod
    def set(credentials:dict, params: dict) -> None:
        api_url = credentials["api_url"]
        headers = credentials["headers"]
        action = params["action"]

        response = post(api_url, headers=headers, json=action)
        response.raise_for_status()
        logger.info("Device %s requested to apply action %s", action["entity_id"], action)

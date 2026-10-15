import datetime
import json
import logging
import os

from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any, Dict

import requests
import yaml

from controller.utils.device_type import DeviceType


logger = logging.getLogger(__name__)


def get_hems_data_source() -> str:
    source = os.getenv("HEMS_DATA_SOURCE", "api").strip().lower()
    if source not in {"api", "mock"}:
        raise ValueError("HEMS_DATA_SOURCE must be 'api' or 'mock'")
    return source


def configured_parameter_value(device_configuration: Dict[str, Any], key: str) -> Any:
    setting = device_configuration.get(key)
    if not isinstance(setting, dict) or setting.get("source") == "default_parameter":
        return None
    return setting.get("value")


def parse_gdp_device_selection(
    raw_devices: Any,
    configurations: Dict[str, Any],
) -> tuple[frozenset[str], dict[str, bool]]:
    if raw_devices is None:
        temperature_device_types = {
            DeviceType.THERMOSTAT,
            DeviceType.HEAT_PUMP,
            DeviceType.ZONE,
            DeviceType.WATER_HEATER,
        }
        return (
            frozenset(
                device_id
                for device_id, configuration in configurations.items()
                if configuration.get("device_type") in temperature_device_types
            ),
            {},
        )
    if not isinstance(raw_devices, list):
        raise ValueError("hub gdp_device_ids must be a list")

    device_ids: set[str] = set()
    preconditioning_by_device: dict[str, bool] = {}
    for index, item in enumerate(raw_devices):
        if isinstance(item, str) and item:
            device_id = item
        elif isinstance(item, dict):
            device_id = item.get("device_id")
            preconditioning = item.get("preconditioning")
            if not isinstance(device_id, str) or not device_id:
                raise ValueError(f"hub gdp_device_ids[{index}].device_id must be a non-empty string")
            if not isinstance(preconditioning, bool):
                raise ValueError(f"hub gdp_device_ids[{index}].preconditioning must be a boolean")
            preconditioning_by_device[device_id] = preconditioning
        else:
            raise ValueError(
                f"hub gdp_device_ids[{index}] must be a device ID string or an object with "
                "device_id and boolean preconditioning fields"
            )
        if device_id in device_ids:
            raise ValueError(f"hub gdp_device_ids contains duplicate device ID {device_id!r}")
        device_ids.add(device_id)

    return frozenset(device_ids), preconditioning_by_device


class ConfigurationClient(ABC):
    @abstractmethod
    def get_configuration(self, day: int) -> Dict[str, Any]:
        """Fetch configuration data for a specific building and day. Sunday = 0, Monday = 1, Tuesday = 2, Wednesday = 3,
        Thursday = 4, Friday = 5, Saturday = 6."""
        pass


class MockConfigurationClient(ConfigurationClient):
    configuration_path: str

    def __init__(self, configuration_path: str):
        self.configuration_path = configuration_path

    def get_configuration(self, day: int) -> Dict[str, Any]:
        # Mock implementation returning dummy configuration data
        with open(self.configuration_path, "r") as file_path:
            configuration = json.load(file_path)
            # TODO filter configuration based on the day parameter
            return configuration


class RestConfigurationClient(ConfigurationClient):
    _hems_api_base_url: str

    def __init__(self, hems_api_base_url: str):
        self._hems_api_base_url = hems_api_base_url
        self._building_id = os.getenv("BUILDING_ID")

    def get_configuration(self, day: int) -> Dict[str, Any]:
        parameters = {"day": day}
        response = requests.get(
            f"{self._hems_api_base_url}/api/devices/{self._building_id}",
            params=parameters,
            verify=False,
            timeout=10,
        )
        response.raise_for_status()
        configuration_data = response.json()

        return configuration_data


def retrieve_device_configuration() -> Dict[str, Any]:
    configuration_client: ConfigurationClient
    if get_hems_data_source() == "mock":
        configuration_path = os.getenv("MOCK_CONFIGURATION_PATH", "").strip()
        if not configuration_path:
            raise ValueError("MOCK_CONFIGURATION_PATH is required when HEMS_DATA_SOURCE=mock")
        if not os.path.isfile(configuration_path):
            raise FileNotFoundError(f"Mock device configuration file not found: {configuration_path}")
        logger.debug("Using device configuration from local file: %s", configuration_path)
        configuration_client = MockConfigurationClient(configuration_path)
    else:
        logger.debug("Using device configuration from HEMS API")
        configuration_client = RestConfigurationClient(
            os.getenv("HEMS_API_BASE_URL", "http://hems-api.hydroquebec.lab:8500")
        )

    today = datetime.datetime.now().astimezone().weekday()
    day = (today + 1) % 7
    device_configuration = configuration_client.get_configuration(day)

    logger.debug("Retrieved %s device configurations", len(device_configuration))

    return device_configuration


def get_heat_pump_specifications() -> Dict[str, Any]:
    if get_hems_data_source() == "mock":
        mock_specifications_path = os.getenv("MOCK_HEAT_PUMP_SPECIFICATIONS_PATH", "").strip()
        if not mock_specifications_path:
            raise ValueError("MOCK_HEAT_PUMP_SPECIFICATIONS_PATH is required when HEMS_DATA_SOURCE=mock")
        if not os.path.isfile(mock_specifications_path):
            raise FileNotFoundError(f"Mock heat-pump specifications file not found: {mock_specifications_path}")
        logger.info("Using heat-pump specifications from local file: %s", mock_specifications_path)
        with open(mock_specifications_path, "r", encoding="utf-8") as file_path:
            if mock_specifications_path.lower().endswith((".yaml", ".yml")):
                heat_pump_specifications = yaml.safe_load(file_path)
            else:
                heat_pump_specifications = json.load(file_path)
        return heat_pump_specifications.get("heat_pump_performance_specs", heat_pump_specifications)

    hems_api_base_url = os.getenv("HEMS_API_BASE_URL", "http://hems-api.hydroquebec.lab:8500")
    heat_pump_model = os.getenv("HEAT_PUMP_MODEL", "DLCERBH18AAK")
    return _get_heat_pump_specifications(hems_api_base_url, heat_pump_model)


@lru_cache(maxsize=16)
def _get_heat_pump_specifications(hems_api_base_url: str, heat_pump_model: str) -> Dict[str, Any]:
    response = requests.get(
        f"{hems_api_base_url}/api/devices/specifications/{heat_pump_model}", verify=False, timeout=10
    )
    response.raise_for_status()
    return response.json()

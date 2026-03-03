import json
import os

from abc import ABC, abstractmethod
from typing import Any, Dict

import requests


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
            f"{self._hems_api_base_url}/configuration/{self._building_id}", params=parameters, verify=False
        )
        response.raise_for_status()
        configuration_data = response.json()

        return configuration_data

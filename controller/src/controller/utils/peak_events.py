import json
import os

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, List

import requests

from dataclasses_json import config, dataclass_json


# TODO: Replace these provisional event-level keys when HEMS confirms the response contract and nesting.
GDP_PROFILE_KEY = "gdp_profile"
GDP_DEVICE_IDS_KEY = "gdp_device_ids"


@dataclass_json
@dataclass
class PeakEvent:
    offre: str
    plagehoraire: str
    duree: str
    secteurclient: str
    datedebut: datetime = field(metadata=config(decoder=datetime.fromisoformat, encoder=datetime.isoformat))
    datefin: datetime = field(metadata=config(decoder=datetime.fromisoformat, encoder=datetime.isoformat))
    profile_name: str | None = None
    device_ids: frozenset[str] | None = None


def peak_event_from_dict(event_data: dict[str, Any]) -> PeakEvent:
    data = dict(event_data)
    profile_name = data.pop(GDP_PROFILE_KEY, None)
    raw_device_ids = data.pop(GDP_DEVICE_IDS_KEY, None)

    if profile_name is not None:
        if not isinstance(profile_name, str):
            raise ValueError(f"{GDP_PROFILE_KEY} must be a string")
        from controller.utils.peak_event_plan import GdpProfileName

        try:
            profile_name = GdpProfileName(profile_name).value
        except ValueError as error:
            raise ValueError(f"Unsupported GDP profile: {profile_name}") from error

    if raw_device_ids is None:
        device_ids = None
    elif isinstance(raw_device_ids, list) and all(isinstance(device_id, str) for device_id in raw_device_ids):
        device_ids = frozenset(raw_device_ids)
    else:
        raise ValueError(f"{GDP_DEVICE_IDS_KEY} must be a list of controller device IDs")

    event = PeakEvent.from_dict(data)
    return replace(event, profile_name=profile_name, device_ids=device_ids)


class BasePeakEventClient(ABC):
    @abstractmethod
    def get_peak_events(self) -> List[PeakEvent]:
        """Fetch peak events between start_date and end_date."""
        pass


class MockPeakEventClient(BasePeakEventClient):
    gdp_events_path: str

    def __init__(self, gdp_events_path: str):
        self.gdp_events_path = gdp_events_path

    def get_peak_events(self) -> List[PeakEvent]:
        # Mock implementation returning dummy peak events
        with open(self.gdp_events_path, "r") as file_path:
            peak_events = json.load(file_path)
            return [peak_event_from_dict(event) for event in peak_events]


class PeakEventClient(BasePeakEventClient):
    _hems_api_base_url: str

    def __init__(self, hems_api_base_url: str):
        self._hems_api_base_url = hems_api_base_url
        self._building_id = os.getenv("BUILDING_ID")

    def get_peak_events(self) -> List[PeakEvent]:
        response = requests.get(
            f"{self._hems_api_base_url}/api/peak-events/{self._building_id}", verify=False, timeout=10
        )
        response.raise_for_status()
        peak_events_data = response.json()
        return [peak_event_from_dict(event) for event in peak_events_data]

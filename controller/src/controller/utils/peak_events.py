import json
import os

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List

import requests

from dataclasses_json import dataclass_json


@dataclass
@dataclass_json
class PeakEvent:
    offre: str
    datedebut: datetime
    datefin: datetime
    duree: str
    secteurclient: str
    plagehoraire: str


class BasePeakEventClient(ABC):
    @abstractmethod
    def get_peak_events(self) -> List[PeakEvent]:
        """Fetch peak events between start_date and end_date."""
        pass


class MockPeakEventClient(BasePeakEventClient):
    def get_peak_events(self) -> List[PeakEvent]:
        # Mock implementation returning dummy peak events
        with open(str(os.getenv("GDP_EVENTS_PATH")), "r") as file_path:
            peak_events = json.load(file_path)
            return [PeakEvent.from_dict(event) for event in peak_events]  # type: ignore


class PeakEventClient(BasePeakEventClient):
    _hems_api_base_url: str

    def __init__(self, hems_api_base_url: str):
        self._hems_api_base_url = hems_api_base_url

    def get_peak_events(self) -> List[PeakEvent]:
        response = requests.get(
            f"{self._hems_api_base_url}/peak-events",
        )
        response.raise_for_status()
        peak_events_data = response.json()
        return [PeakEvent.from_dict(event) for event in peak_events_data]  # type: ignore

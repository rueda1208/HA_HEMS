import json

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

import requests

from dataclasses_json import config, dataclass_json


@dataclass_json
@dataclass
class PeakEvent:
    offre: str
    plagehoraire: str
    duree: str
    secteurclient: str
    datedebut: datetime = field(metadata=config(decoder=datetime.fromisoformat, encoder=datetime.isoformat))
    datefin: datetime = field(metadata=config(decoder=datetime.fromisoformat, encoder=datetime.isoformat))


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

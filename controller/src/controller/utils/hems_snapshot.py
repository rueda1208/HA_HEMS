"""Fetch and expose a consistent snapshot of HEMS configuration and GDP event data."""

import time
from dataclasses import dataclass
from typing import Any

from controller.devices.water_heater import WaterHeaterController
from controller.utils.configuration import retrieve_device_configuration
from controller.utils import utils
from controller.utils.device_type import DeviceType
from controller.utils.peak_events import PeakEvent


@dataclass(frozen=True)
class HemsSnapshot:
    configurations: dict[str, Any]
    gdp_event: PeakEvent | None
    fetched_at: float


def fetch_hems_snapshot() -> HemsSnapshot:
    configurations = retrieve_device_configuration()
    return HemsSnapshot(
        configurations=configurations,
        gdp_event=utils.retrieve_gdp_event(configurations),
        fetched_at=time.monotonic(),
    )


def setpoint_entity_ids(configurations: dict[str, Any]) -> set[str]:
    return {
        entity_id
        for entity_id, configuration in configurations.items()
        if configuration.get("device_type") in (DeviceType.THERMOSTAT, DeviceType.HEAT_PUMP)
    }


def water_heater_switch_entity_ids(configurations: dict[str, Any]) -> set[str]:
    switch_entity_ids = set()
    for device_id, configuration in configurations.items():
        if configuration.get("device_type") != DeviceType.WATER_HEATER:
            continue
        switch_entity_id = WaterHeaterController.get_switch_entity_id(device_id, configuration)
        if switch_entity_id is not None:
            switch_entity_ids.add(switch_entity_id)
    return switch_entity_ids

import datetime
import json
import logging
import os

from enum import StrEnum
from functools import lru_cache
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Dict

import numpy as np
import requests
import yaml

from controller.utils.configuration import ConfigurationClient, MockConfigurationClient, RestConfigurationClient
from controller.utils.peak_events import BasePeakEventClient, MockPeakEventClient, PeakEvent, PeakEventClient


logger = logging.getLogger(__name__)
CONFIG_FILE_PATH = os.getenv("CONFIG_FILE_PATH")
LOGS_DIR = os.getenv("LOGS_DIR", "/share/controller/logs")


class ControlMode(StrEnum):
    HEATING = "heating"
    COOLING = "cooling"
    OFF = "off"


def get_hems_data_source() -> str:
    source = os.getenv("HEMS_DATA_SOURCE", "api").strip().lower()
    if source not in {"api", "mock"}:
        raise ValueError("HEMS_DATA_SOURCE must be 'api' or 'mock'")
    return source


def setup_logging(filename: str):
    if not os.path.exists(LOGS_DIR):
        os.makedirs(LOGS_DIR)

    # Create a TimedRotatingFileHandler
    file_handler = TimedRotatingFileHandler(
        f"{LOGS_DIR}/{filename}",  # Base log file name
        when="midnight",  # Rotate log at midnight
        interval=1,  # Number of intervals before rotating (1 day here)
        backupCount=7,  # Keep logs for the last 7 days
    )

    # Set the format for the log file
    formatter = logging.Formatter("%(asctime)s [%(levelname)5s] %(message)s (%(filename)s:%(lineno)s)")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    # Configure the root logger
    logger_level = os.getenv("LOGLEVEL", "DEBUG").upper()
    logging.basicConfig(
        level=logger_level,
        handlers=[
            file_handler,  # Log to file with rotation
            stream_handler,  # Log to console
        ],
    )


def get_heat_pump_cop(control_mode: ControlMode, outside_temperature: float) -> float:
    hems_api_base_url = os.getenv("HEMS_API_BASE_URL", "http://hems-api.hydroquebec.lab:8500")
    heat_pump_model = os.getenv("HEAT_PUMP_MODEL", "DLCERBH18AAK")
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
        heat_pump_specifications = heat_pump_specifications.get("heat_pump_performance_specs", heat_pump_specifications)
    else:
        heat_pump_specifications = _get_heat_pump_specifications(hems_api_base_url, heat_pump_model)

    if control_mode == ControlMode.COOLING:
        cop_points = heat_pump_specifications.get("cooling", {}).get("COP_points", {})
    elif control_mode == ControlMode.HEATING:
        cop_points = heat_pump_specifications.get("heating", {}).get("COP_points", {})
    else:
        raise ValueError(f"Invalid control mode '{control_mode}' for COP model creation")

    # Create regression model using heating COP data points
    outside_temperatures_list = [data["outdoor_dry_bulb_C"] for data in cop_points.values()]
    cop_values_list = [data["max"] for data in cop_points.values()]
    cop_model = np.poly1d(np.polyfit(outside_temperatures_list, cop_values_list, 2))
    cop = cop_model(outside_temperature)

    logger.info(
        f"Outside temperature: {outside_temperature} C, Heat Pump COP: {cop:.2f}, Heat Pump mode: {control_mode}"
    )

    return cop


@lru_cache(maxsize=16)
def _get_heat_pump_specifications(hems_api_base_url: str, heat_pump_model: str) -> Dict[str, Any]:
    response = requests.get(
        f"{hems_api_base_url}/api/devices/specifications/{heat_pump_model}", verify=False, timeout=10
    )
    response.raise_for_status()
    return response.json()


def retrieve_gdp_event() -> PeakEvent | None:
    peak_events_client: BasePeakEventClient
    if get_hems_data_source() == "mock":
        gdp_events_path = os.getenv("MOCK_GDP_EVENTS_PATH", "").strip()
        if not gdp_events_path:
            raise ValueError("MOCK_GDP_EVENTS_PATH is required when HEMS_DATA_SOURCE=mock")
        if not os.path.isfile(gdp_events_path):
            raise FileNotFoundError(f"Mock GDP events file not found: {gdp_events_path}")
        logger.debug("Using GDP events from local file: %s", gdp_events_path)
        peak_events_client = MockPeakEventClient(gdp_events_path)
    else:
        logger.debug("Using GDP events from Hydro-Quebec API")
        peak_events_client = PeakEventClient(os.getenv("HEMS_API_BASE_URL", "http://hems-api.hydroquebec.lab:8500"))

    peak_events = peak_events_client.get_peak_events()

    logger.debug("Retrieved %s GDP events", len(peak_events))

    now = datetime.datetime.now().astimezone()
    today = now.date()

    # Filter events for today
    today_events = [event for event in peak_events if event.datedebut.date() == today]

    if not today_events:
        logger.debug("No GDP events found for today")
        return None

    # Sort events by start time
    today_events.sort(key=lambda e: e.datedebut)

    # Check for ongoing or upcoming event
    for event in today_events:
        # Event is ongoing
        if event.datedebut <= now <= event.datefin:
            logger.debug("Current GDP event: %s", event)
            return event
        # Event is upcoming
        elif now < event.datedebut:
            logger.debug("Next GDP event: %s", event)
            return event

    # All events are finished
    logger.debug("All GDP events for today are finished")
    return None


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

    today = datetime.datetime.now().astimezone().weekday()  # Current day of the week as an integer (0=Monday, 6=Sunday)
    day = (today + 1) % 7  # Convert to Sunday=0, Monday=1, ..., Saturday=6
    device_configuration = configuration_client.get_configuration(day)

    logger.debug("Retrieved %s device configurations", device_configuration)

    return device_configuration

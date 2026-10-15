import datetime
import logging
import os

from enum import StrEnum
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Dict

from controller.utils import configuration
from controller.utils.peak_event_plan import (
    GDP_PROFILE_PRESETS,
    GdpProfileName,
    GdpResponseProfile,
    normalize_gdp_profile_name,
)
from controller.utils.peak_events import BasePeakEventClient, MockPeakEventClient, PeakEvent, PeakEventClient


logger = logging.getLogger(__name__)
CONFIG_FILE_PATH = os.getenv("CONFIG_FILE_PATH")
LOGS_DIR = os.getenv("LOGS_DIR", "/share/controller/logs")


class ControlMode(StrEnum):
    HEATING = "heating"
    COOLING = "cooling"
    OFF = "off"


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


def _gdp_profile_for_configuration(configurations: Dict[str, Any] | None) -> GdpResponseProfile:
    if configurations is None:
        return GDP_PROFILE_PRESETS[GdpProfileName.MODERATE]

    building_id = str(os.getenv("BUILDING_ID")).lower()
    hub_configuration = configurations.get(f"hub.{building_id}", {})
    mode_configuration = hub_configuration.get("mode", {}) if isinstance(hub_configuration, dict) else {}
    control_mode = mode_configuration.get("value", ControlMode.OFF.value) if isinstance(mode_configuration, dict) else ""
    if control_mode not in {ControlMode.HEATING.value, ControlMode.COOLING.value}:
        return GDP_PROFILE_PRESETS[GdpProfileName.MODERATE]

    profile_configuration = hub_configuration.get("gdp_profile", {})
    profile_name = (
        profile_configuration.get("value", GdpProfileName.MODERATE.value)
        if isinstance(profile_configuration, dict)
        else GdpProfileName.MODERATE.value
    )
    try:
        normalized_profile_name = normalize_gdp_profile_name(profile_name)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Unsupported GDP profile in hub configuration: {profile_name}") from error
    return GDP_PROFILE_PRESETS[normalized_profile_name]


def retrieve_gdp_event(configurations: Dict[str, Any] | None = None) -> PeakEvent | None:
    peak_events_client: BasePeakEventClient
    if configuration.get_hems_data_source() == "mock":
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
    profile = _gdp_profile_for_configuration(configurations)

    today_events = [event for event in peak_events if event.datedebut.date() == today]
    today_events.sort(key=lambda e: e.datedebut)

    ongoing_event = next(
        (event for event in today_events if event.datedebut <= now <= event.datefin),
        None,
    )
    if ongoing_event is not None:
        logger.debug("Current GDP event: %s", ongoing_event)
        return ongoing_event

    upcoming_event = next((event for event in today_events if now < event.datedebut), None)
    if (
        upcoming_event is not None
        and profile.preconditioning_enabled
        and now >= upcoming_event.datedebut - profile.preconditioning_lead_time
    ):
        logger.debug("Next GDP event is in preconditioning: %s", upcoming_event)
        return upcoming_event

    recovery_event = max(
        (
            event
            for event in peak_events
            if event.datefin <= now < event.datefin + profile.recovery_duration
        ),
        key=lambda event: event.datefin,
        default=None,
    )
    if recovery_event is not None:
        logger.debug("GDP recovery event: %s", recovery_event)
        return recovery_event

    if upcoming_event is not None:
        logger.debug("Next GDP event: %s", upcoming_event)
        return upcoming_event

    logger.debug("All GDP events for today are finished")
    return None

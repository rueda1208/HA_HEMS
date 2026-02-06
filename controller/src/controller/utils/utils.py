import copy
import datetime
import json
import logging
import os

from logging.handlers import TimedRotatingFileHandler
from typing import Any, Dict, Union

import numpy as np
import yaml

from controller.utils.peak_events import BasePeakEventClient, MockPeakEventClient, PeakEvent, PeakEventClient


logger = logging.getLogger(__name__)
CONFIG_FILE_PATH = os.getenv("CONFIG_FILE_PATH", "/share/controller/config/config.yaml")
LOGS_DIR = os.getenv("LOGS_DIR", "/share/controller/logs")


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

    # Configure the root logger
    logger_level = os.getenv("LOGLEVEL", "DEBUG").upper()
    logging.basicConfig(
        level=logger_level,
        handlers=[
            file_handler,  # Log to file with rotation
            logging.StreamHandler(),  # Optionally log to console
        ],
    )


def update_config_with_zones(zones):
    """
    Update the original config.yaml by adding new zones into hvac_systems
    (only if they don't already exist). Existing zones remain unchanged.

    :param zones: list of zone names (str)
    :param filename: path to config.yaml (updated in place)
    """
    # Load original config
    with open(CONFIG_FILE_PATH, "r") as f:
        config = yaml.safe_load(f)

    # Extract existing hvac_systems (or empty if missing)
    hvac_systems = config.get("hvac_systems", {})

    # Collect existing zone names
    existing_names = set(hvac_systems.keys())

    # Default schedule template
    default_schedule = {
        "weekday": {
            "time_slots": {
                "6h00-22h00": {"target_temp_C": 21},
                "22h00-6h00": {"target_temp_C": 18},
            }
        },
        "weekend": {
            "time_slots": {
                "8h00-23h00": {"target_temp_C": 22},
                "23h00-8h00": {"target_temp_C": 19},
            }
        },
    }

    # Add only new zones
    for zone in zones:
        if zone.startswith("climate.") and zone not in existing_names:
            if "heat_pump" not in zone:
                hvac_systems[zone] = {
                    "heat_pump_impact": 0.0,
                    "flexibility": {"upward": 0.0, "downward": 0.0},
                    "preconditioning": False,
                    "schedule": copy.deepcopy(default_schedule),
                }
            else:
                hvac_systems[zone] = {
                    "heating": {"schedule": copy.deepcopy(default_schedule)},
                    "cooling": {"schedule": copy.deepcopy(default_schedule)},
                }

    # Update config
    config["hvac_systems"] = hvac_systems

    # Overwrite the same file
    with open(CONFIG_FILE_PATH, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)

    logger.debug(f"'{CONFIG_FILE_PATH}' updated successfully with new zones (if any).")


def create_cop_model() -> Dict[str, Any]:
    # Load config
    heat_pump_config_file_path = os.getenv("HEAT_PUMP_CONFIG_FILE_PATH", "/share/controller/config/heat-pump.yaml")
    with open(heat_pump_config_file_path, "r") as f:
        config = yaml.safe_load(f)

    # Load heat_pump_performance_specs
    heat_pump_performance_specs = config.get("heat_pump_performance_specs", {})
    hp_cooling_performance_specs = heat_pump_performance_specs.get("cooling", {}).get("COP_points", {})
    hp_heating_performance_specs = heat_pump_performance_specs.get("heating", {}).get("COP_points", {})

    # Create regression model using heating COP data points
    outside_temperatures_list = [data["outdoor_dry_bulb_C"] for data in hp_cooling_performance_specs.values()]
    cop_values_list = [data["max"] for data in hp_cooling_performance_specs.values()]
    cooling_cop_model = np.poly1d(np.polyfit(outside_temperatures_list, cop_values_list, 2))

    # Create regression model using heating COP data points
    outside_temperatures_list = [data["outdoor_dry_bulb_C"] for data in hp_heating_performance_specs.values()]
    cop_values_list = [data["max"] for data in hp_heating_performance_specs.values()]
    heating_cop_model = np.poly1d(np.polyfit(outside_temperatures_list, cop_values_list, 2))

    return {"cool": cooling_cop_model, "heat": heating_cop_model}


def select_zones_hp_impact(with_impact: bool) -> Dict:
    # Load configuration
    with open(os.getenv("OPTIONS_FILE_PATH", "/data/options.json"), "r") as file_path:
        options_data = json.load(file_path)

    heat_pump_enabled = options_data.get("heat_pump_enabled", False)

    # Load config
    with open(CONFIG_FILE_PATH, "r") as f:
        config = yaml.safe_load(f)

    hvac_systems = config.get("hvac_systems", {})

    result = {}
    for zone, settings in hvac_systems.items():
        # Skip heat pump itself
        if "heat_pump" in zone:
            continue

        # Get heat pump impact setting
        impact = settings.get("heat_pump_impact", 0.0)

        # Heat pump disabled
        if not heat_pump_enabled:
            if with_impact:
                continue
            result[zone] = 0.0
            continue

        # Heat pump enabled
        if (impact > 0.0) == with_impact:
            result[zone] = impact

    return result


def get_target_temperature(
    zone_id: str, gdp_event: PeakEvent | None, hvac_mode: str | None = None
) -> Union[float, None]:
    # Load config
    with open(CONFIG_FILE_PATH, "r") as f:
        config = yaml.safe_load(f)

    hvac_systems = config.get("hvac_systems", {})
    zone_settings = hvac_systems.get(zone_id, {})
    zone_flexibility = zone_settings.get("flexibility", {"upward": 0.0, "downward": 0.0})
    zone_preconditioning = zone_settings.get("preconditioning", False)
    if hvac_mode is None:
        schedule = zone_settings.get("schedule", {})
    else:
        schedule = zone_settings.get(hvac_mode, {}).get("schedule", {})

    # Determine day type and current hour
    now = datetime.datetime.now().astimezone()
    current_hour = now.hour
    current_day_type = "weekday" if now.weekday() < 5 else "weekend"

    # Get initial target temperature from schedule
    init_target_temperature = get_target_from_schedule(current_hour, current_day_type, schedule)

    # Check for GDP events today
    if not gdp_event:
        target_temperature = init_target_temperature
        logger.debug("No GDP events today, using regular schedule")
    else:
        gdp_timestamp_dict = {
            "start": gdp_event.datedebut,
            "end": gdp_event.datefin,
        }

        preconditioning_timestamp_dict = {
            "start": gdp_timestamp_dict["start"] - datetime.timedelta(hours=2),  # Two hours before event
            "end": gdp_timestamp_dict["start"],
        }

        post_event_recovery_timestamp_dict = {
            "start": gdp_timestamp_dict["end"],
            "end": gdp_timestamp_dict["end"] + datetime.timedelta(hours=1),  # One hour after event
        }

        # Apply flexibility adjustment if current hour is within GDP event hours
        if now >= gdp_timestamp_dict["start"] and now < gdp_timestamp_dict["end"]:
            # Negative for lowering temp during event
            target_temperature = init_target_temperature - zone_flexibility.get("downward", 0.0)
        elif (
            zone_preconditioning
            and now >= preconditioning_timestamp_dict["start"]
            and now < preconditioning_timestamp_dict["end"]
        ):
            # Calculate max target temperature during GDP event hours for preconditioning
            start_preconditioning_hour = (preconditioning_timestamp_dict["start"]).hour
            start_gdp_hour = (gdp_timestamp_dict["start"]).hour
            stop_gdp_hour = (gdp_timestamp_dict["end"]).hour

            max_target_temperature_at_gdp_event = (
                get_target_from_schedule(start_gdp_hour, current_day_type, schedule) or 0.0
            )

            for hour in range(start_gdp_hour, stop_gdp_hour):
                max_target_temperature_at_gdp_event = max(
                    max_target_temperature_at_gdp_event,
                    get_target_from_schedule(hour, current_day_type, schedule) or 0.0,
                )

            # Calculate preconditioning flexibility
            zone_flexibility_value = zone_flexibility.get("upward", 0.0)

            # Positive for raising temp during preconditioning
            target_temperature = conditioning_ramping(
                ramping_time=int(
                    (preconditioning_timestamp_dict["end"] - preconditioning_timestamp_dict["start"]).total_seconds()
                ),
                elapsed_time=int((now - preconditioning_timestamp_dict["start"]).total_seconds()),
                initial_value=get_target_from_schedule(start_preconditioning_hour, current_day_type, schedule) or 0.0,
                target_value=zone_flexibility_value + max_target_temperature_at_gdp_event,
            )
        elif now >= post_event_recovery_timestamp_dict["start"] and now < post_event_recovery_timestamp_dict["end"]:
            stop_post_event_hour = (post_event_recovery_timestamp_dict["end"]).hour

            max_target_temperature_post_event_recovery = get_target_from_schedule(
                stop_post_event_hour, current_day_type, schedule
            )  # Target at end of recovery

            init_zone_temperature_after_gdp_event = (
                get_target_from_schedule(
                    (post_event_recovery_timestamp_dict["start"]).hour - 1,  # One hour before recovery
                    current_day_type,
                    schedule,
                )
                or 0.0
            )

            target_temperature = conditioning_ramping(
                ramping_time=int(
                    (
                        post_event_recovery_timestamp_dict["end"] - post_event_recovery_timestamp_dict["start"]
                    ).total_seconds()
                ),
                elapsed_time=int((now - post_event_recovery_timestamp_dict["start"]).total_seconds()),
                initial_value=init_zone_temperature_after_gdp_event,
                target_value=max_target_temperature_post_event_recovery,  # No flexibility during recovery, just return to target
            )
        else:
            target_temperature = init_target_temperature  # No adjustment outside GDP event hours to keep user comfort

    # Get target temperature from schedule and apply flexibility
    if target_temperature is not None:
        logger.debug(f"Zone {zone_id}: target temperature = {target_temperature} °C")
        return target_temperature
    else:
        logger.debug(f"Zone {zone_id}: no target temperature found")
        return None


def get_environment_sensor_id() -> Union[str, None]:
    # Load config
    with open(CONFIG_FILE_PATH, "r") as f:
        config = yaml.safe_load(f)

    environment_sensor_id = config.get("environment_sensor_id", None)
    if environment_sensor_id is None:
        logger.error(f"No environment_sensor_id specified in {CONFIG_FILE_PATH}")
        return None

    return environment_sensor_id


# TODO: Refactor this function to handle hours and minutes in schedule time slots (ex.: 10h30-15h45)
def get_target_from_schedule(current_hour: int, current_day_type: str, schedule: Dict[str, Any]) -> Union[float, None]:
    if current_day_type in schedule:
        time_slots = schedule[current_day_type].get("time_slots", {})
        for slot_range, slot_data in time_slots.items():
            # Parse slot_range like "6h00-22h00"
            start_str, end_str = slot_range.split("-")
            start_hour = int(start_str.split("h")[0])
            end_hour = int(end_str.split("h")[0])

            # Handle overnight ranges (e.g., 22h00-6h00)
            if start_hour < end_hour:
                if start_hour <= current_hour < end_hour:
                    return slot_data.get("target_temp_C")
            else:
                if current_hour >= start_hour or current_hour < end_hour:
                    return slot_data.get("target_temp_C")

    return None  # Default if no schedule found


def conditioning_ramping(ramping_time: int, elapsed_time: int, initial_value: float, target_value: float) -> float:
    # Shorten ramping time by 15 minutes to reach target earlier and heat/cool more efficiently
    ramping_time_short = ramping_time - 900

    # Clamp elapsed time between 0 and ramping_time_short
    if elapsed_time <= 0:
        return round(initial_value, 2)
    if elapsed_time >= ramping_time_short:
        return round(target_value, 2)

    # Linear interpolation
    ratio = elapsed_time / ramping_time_short
    y = initial_value + (target_value - initial_value) * ratio
    return round(y, 2)


def retrieve_gdp_event() -> PeakEvent | None:
    gdp_events_path = os.getenv("MOCK_GDP_EVENTS_PATH", "/share/controller/config/peak-events.json")

    peak_events_client: BasePeakEventClient

    if os.path.exists(gdp_events_path):
        logger.debug("Using GDP events from local file: %s", gdp_events_path)
        peak_events_client = MockPeakEventClient(gdp_events_path)
    else:
        logger.debug("Using GDP events from Hydro-Quebec API")
        peak_events_client = PeakEventClient(os.getenv("HEMS_API_BASE_URL", "http://hems.hydroquebec.lab:8500"))

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

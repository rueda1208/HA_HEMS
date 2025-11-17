import os
import yaml
import copy
import json
import time 
import logging
import requests
import numpy as np
from datetime import datetime
from datetime import datetime, timedelta
from typing import Union, Any, Dict, List
from logging.handlers import TimedRotatingFileHandler

logger = logging.getLogger(__name__)
CONFIG_FILE_PATH = os.getenv("CONFIG_FILE_PATH", "/share/controller/config/config.yaml")
LOGS_DIR = os.getenv("LOGS_DIR", "/share/controller/logs")

def setup_logging(filename: str = "logger.log"):
    # Create a TimedRotatingFileHandler
    file_handler = TimedRotatingFileHandler(
        f"{LOGS_DIR}/{filename}",  # Base log file name
        when="midnight",  # Rotate log at midnight
        interval=1,  # Number of intervals before rotating (1 day here)
        backupCount=7,  # Keep logs for the last 7 days
    )

    # Set the format for the log file
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)5s] %(message)s (%(filename)s:%(lineno)s)"
    )
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
            "time_slots":{
                "6h00-22h00":{"target_temp_C": 21},
                "22h00-6h00":{"target_temp_C": 18},
            }
        },
        "weekend": {
            "time_slots": {
                "8h00-23h00":{"target_temp_C": 22},
                "23h00-8h00":{"target_temp_C": 19},
            }
        }
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

def create_cop_model() -> float:
    # Load config
    with open(CONFIG_FILE_PATH, "r") as f:
        config = yaml.safe_load(f)

    # Load heat_pump_performance_specs
    heat_pump_performance_specs = config.get("heat_pump_performance_specs", {})
    hp_cooling_performance_specs = heat_pump_performance_specs.get("cooling", {}).get("COP_points", {})
    hp_heating_performance_specs = heat_pump_performance_specs.get("heating", {}).get("COP_points", {})

    # Create regression model using heating COP data points
    outside_temperatures_list = [data["outdoor_dry_bulb_C"] for id,data in hp_cooling_performance_specs.items()]
    cop_values_list = [data["max"] for id,data in hp_cooling_performance_specs.items()]
    cooling_cop_model = np.poly1d(np.polyfit(outside_temperatures_list, cop_values_list, 2))
    
    # Create regression model using heating COP data points
    outside_temperatures_list = [data["outdoor_dry_bulb_C"] for id,data in hp_heating_performance_specs.items()]
    cop_values_list = [data["max"] for id,data in hp_heating_performance_specs.items()]
    heating_cop_model = np.poly1d(np.polyfit(outside_temperatures_list, cop_values_list, 2))

    return {"cool":cooling_cop_model,"heat":heating_cop_model}

def select_zones_hp_impact(with_impact: bool) -> Dict:
    # Load config
    with open(CONFIG_FILE_PATH, "r") as f:
        config = yaml.safe_load(f)

    hvac_systems = config.get("hvac_systems", {})
    
    result = {}
    for zone, settings in hvac_systems.items():
        if "heat_pump" in zone:
            continue
        else:
            impact = settings.get("heat_pump_impact", 0.0)
            if (impact > 0.0) == with_impact:
                result[zone] = settings.get("heat_pump_impact", 0.0)
    return result

def get_target_temperature(zone_id: str, gdp_events: List[Dict[str, Any]], hvac_mode: str | None = None) -> Union[float, None]:
    # Load config
    with open(CONFIG_FILE_PATH, "r") as f:
        config = yaml.safe_load(f)

    hvac_systems = config.get("hvac_systems", {})
    zone_settings = hvac_systems.get(zone_id, {})
    zone_flexibility = zone_settings.get("flexibility", {"upward":0.0, "downward":0.0})
    zone_preconditioning = zone_settings.get("preconditioning", False)
    if hvac_mode is None:
        schedule = zone_settings.get("schedule", {})
    else:
        schedule = zone_settings.get(hvac_mode, {}).get("schedule", {})

    # Determine day type and current hour
    now = datetime.now()
    current_hour = now.hour
    current_minute = now.minute
    current_day_type = "weekday" if now.weekday() < 5 else "weekend"

    # Check for GDP events today
    if not gdp_events:
        logger.debug("No GDP events today, using regular schedule")
        flexibility = 0.0
    else:
        # Build list of GDP event hours
        gdp_hours_list = []
        preconditioning_hours_list = []
        post_event_recovery_hours_list = []
        for event in gdp_events:
            if event["plagehoraire"] == "AM":
                gdp_hours_list.extend(range(6, 10))  # 6h00 to 9h59
                preconditioning_hours_list.append(5) if zone_preconditioning else None # 5h00 for preconditioning
                post_event_recovery_hours_list.append(10) if zone_preconditioning else None # 10h00 for post-event recovery
            elif event["plagehoraire"] == "PM":
                gdp_hours_list.extend(range(16, 20))  # 16h00 to 19h59
                preconditioning_hours_list.append(15) if zone_preconditioning else None # 15h00 for preconditioning
                post_event_recovery_hours_list.append(20) if zone_preconditioning else None # 20h00 for post-event recovery
        
        # Apply flexibility adjustment if current hour is within GDP event hours
        if current_hour in gdp_hours_list + preconditioning_hours_list + post_event_recovery_hours_list:
            logger.debug("Current hour is within GDP event or pre/post-conditioning hours, applying flexibility adjustment")
            if current_hour in preconditioning_hours_list:
                current_value = get_target_from_schedule(current_hour, current_day_type, schedule) # Regular schedule
                zone_flexibility = zone_flexibility.get("upward", 0.0) # Positive for raising temp during preconditioning
                flexibility = conditioning_ramping(
                    current_minute = current_minute,
                    current_value = current_value,
                    target_value = current_value + zone_flexibility
                )
            elif current_hour in post_event_recovery_hours_list:
                on_event_value = get_target_from_schedule(current_hour - 1, current_day_type, schedule) # Last hour during event
                current_value = get_target_from_schedule(current_hour, current_day_type, schedule) # Regular schedule
                flexibility = conditioning_ramping(
                    current_minute = current_minute,
                    current_value = on_event_value,
                    target_value = current_value, # No flexibility during recovery, just return to target
                )
            else:
                flexibility = -zone_flexibility.get("downward", 0.0) # Negative for lowering temp during event
        else:
            flexibility = 0.0 # No adjustment outside GDP event hours to keep user comfort

    # Get target temperature from schedule and apply flexibility
    target_temp = get_target_from_schedule(current_hour, current_day_type, schedule)
    if target_temp is not None:
        adjusted_target_temp = target_temp + flexibility
        logger.debug(f"Zone {zone_id}: base target {target_temp} C, flexibility {flexibility} C, adjusted target {adjusted_target_temp} C")
        return adjusted_target_temp
    else:
        logger.debug(f"Zone {zone_id}: no target temperature found in schedule")
        return None

def get_target_from_schedule(current_hour: int, current_day_type: str, schedule: Dict[str, Any]) -> Union[float, None]:
    if current_day_type in schedule:
        time_slots = schedule[current_day_type].get("time_slots", {})
        for slot_range, slot_data in time_slots.items():
            # Parse slot_range like "6h00-22h00"
            start_str, end_str = slot_range.split('-')
            start_hour = int(start_str.split('h')[0])
            end_hour = int(end_str.split('h')[0])

            # Handle overnight ranges (e.g., 22h00-6h00)
            if start_hour < end_hour:
                if start_hour <= current_hour < end_hour:
                    return slot_data.get("target_temp_C") 
            else:
                if current_hour >= start_hour or current_hour < end_hour:
                    return slot_data.get("target_temp_C") 
    
    return None  # Default if no schedule found

def conditioning_ramping(current_minute: float, current_value: float, target_value: float) -> float:
    # Simple linear ramping for preconditioning
    x1 = 0.0
    x2 = 60.0
    y1 = current_value
    y2 = target_value

    # Clamp time between 0 and 60
    t = max(min(current_minute, x2), x1)

    # Linear interpolation
    y = y1 + (y2 - y1) * ((t - x1) / (x2 - x1))
    
    return round(y - current_value,2)

def retrieve_gdp_events(mock: bool | None=None) -> List[Dict[str, Any]]:
    # Retrieve GDP events from Hydro-Quebec API
    if mock:
        logger.info("Using mock GDP events data")
        today = datetime.now().date()
        gdp_events_data = {
            "total_count": 1,
            "results": [
                {"datedebut": f"{today}T11:00:00+00:00", "datefin": f"{today}T14:00:00+00:00", "plagehoraire": "AM"},
                {"datedebut": f"{today}T21:00:00+00:00", "datefin": f"{today+timedelta(days=1)}T01:00:00+00:00", "plagehoraire": "PM"},
            ]
        }
        return gdp_events_data["results"]
    
    # Define the API endpoint
    api_url = os.getenv("HQ_API_URL", "https://donnees.hydroquebec.com/api/explore/v2.1/catalog/datasets/evenements-pointe/records")

    # Set query parameters
    params = {
        "select": "datedebut,datefin,plagehoraire",
        "where": 'offre="CPC-D"',
        "order_by": "datedebut ASC",
        "limit": 20,
    }

    # Retrieve GDP events data
    attempts = 0
    while attempts < 5:
        try:
            response = requests.get(api_url, params=params)
            if response.status_code == 200:
                gdp_events_data = response.json()
                break
            else:
                logger.error(
                    "Error while retrieving GDP events data: %s", response.text
                )
                attempts += 1
                time.sleep(1)
        except Exception as ex:
            logger.error("An error occurred: %s", ex, exc_info=True)
            attempts += 1
            time.sleep(1)

    # Check if there are any GDP events for today
    if gdp_events_data["total_count"] == 0:
        logger.debug("No GDP events available from Hydro-Quebec API")
        return []
    else:
        logger.debug("Retrieved %s GDP events", gdp_events_data["total_count"])
        today = datetime.now().date()
        today_events = [
            event
            for event in gdp_events_data["results"]
            if datetime.strptime(event["datedebut"], "%Y-%m-%dT%H:%M:%S%z").date()
            == today
        ]

        if not today_events:
            logger.debug("No GDP events found for today")
        else:
            logger.debug(
                "Todays's GDP events: %s", json.dumps(today_events, indent=4)
            )
        return today_events
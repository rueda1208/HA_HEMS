import os
import yaml
import copy
import json
import time 
import logging
import requests
import datetime
import numpy as np
from zoneinfo import ZoneInfo
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
    now = datetime.datetime.now()
    current_hour = now.hour
    current_timestamp = now.timestamp()
    current_day_type = "weekday" if now.weekday() < 5 else "weekend"

    # Get initial target temperature from schedule
    init_target_temperature = get_target_from_schedule(current_hour, current_day_type, schedule)

    # Check for GDP events today
    if not gdp_events:
        target_temperature = init_target_temperature
        logger.debug("No GDP events today, using regular schedule")
    else:
        # Build list of GDP event hours
        gdp_timestamp_dict = {
            "start_timestamp": None,
            "end_timestamp": None,
        }
        preconditioning_timestamp_dict = {
            "start_timestamp": None,
            "end_timestamp": None,
        }
        post_event_recovery_timestamp_dict = {
            "start_timestamp": None,
            "end_timestamp": None,
        }
        for event in gdp_events:
            try:
                # GDP event timestamps
                gdp_timestamp_dict["start_timestamp"] = datetime.datetime.strptime(event["datedebut"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
                gdp_timestamp_dict["end_timestamp"] = datetime.datetime.strptime(event["datefin"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
                # Preconditioning timestamps
                if zone_preconditioning:
                    preconditioning_timestamp_dict["start_timestamp"] = gdp_timestamp_dict["start_timestamp"] - 7200 # Two hours before event
                    preconditioning_timestamp_dict["end_timestamp"] = gdp_timestamp_dict["start_timestamp"]
                # Post-event recovery timestamps
                post_event_recovery_timestamp_dict["start_timestamp"] = gdp_timestamp_dict["end_timestamp"]
                post_event_recovery_timestamp_dict["end_timestamp"] = gdp_timestamp_dict["end_timestamp"] + 3600  # One hour after event
            except Exception as ex:
                logger.error("Error parsing GDP event timestamps: %s", ex, exc_info=True)
                continue
        
        # Apply flexibility adjustment if current hour is within GDP event hours
        if current_timestamp >= gdp_timestamp_dict["start_timestamp"] and current_timestamp < gdp_timestamp_dict["end_timestamp"]:
            target_temperature = init_target_temperature - zone_flexibility.get("downward", 0.0) # Negative for lowering temp during event
        elif zone_preconditioning and current_timestamp >= preconditioning_timestamp_dict["start_timestamp"] and current_timestamp < preconditioning_timestamp_dict["end_timestamp"]:
            # Calculate max target temperature during GDP event hours for preconditioning
            start_preconditioning_hour = datetime.datetime.fromtimestamp(preconditioning_timestamp_dict["start_timestamp"]).hour
            start_gdp_hour = datetime.datetime.fromtimestamp(gdp_timestamp_dict["start_timestamp"]).hour
            stop_gdp_hour = datetime.datetime.fromtimestamp(gdp_timestamp_dict["end_timestamp"]).hour
            max_target_temperature_at_gdp_event = get_target_from_schedule(start_gdp_hour, current_day_type, schedule) or 0.0
            for hour in range(start_gdp_hour, stop_gdp_hour):
                max_target_temperature_at_gdp_event = max(
                    max_target_temperature_at_gdp_event,
                    get_target_from_schedule(hour, current_day_type, schedule) or 0.0
                )
            # Calculate preconditioning flexibility
            zone_flexibility_value = zone_flexibility.get("upward", 0.0) # Positive for raising temp during preconditioning
            target_temperature = conditioning_ramping(
                ramping_time = int(preconditioning_timestamp_dict["end_timestamp"] - preconditioning_timestamp_dict["start_timestamp"]),
                elapsed_time = int(current_timestamp - preconditioning_timestamp_dict["start_timestamp"]), 
                initial_value = get_target_from_schedule(start_preconditioning_hour, current_day_type, schedule) or 0.0,
                target_value = zone_flexibility_value + max_target_temperature_at_gdp_event,
            )
        elif current_timestamp >= post_event_recovery_timestamp_dict["start_timestamp"] and current_timestamp < post_event_recovery_timestamp_dict["end_timestamp"]:
            stop_post_event_hour = datetime.datetime.fromtimestamp(post_event_recovery_timestamp_dict["end_timestamp"]).hour
            max_target_temperature_post_event_recovery = get_target_from_schedule(stop_post_event_hour, current_day_type, schedule) # Target at end of recovery
            init_zone_temperature_after_gdp_event = get_target_from_schedule(
                datetime.datetime.fromtimestamp(post_event_recovery_timestamp_dict["start_timestamp"]).hour - 1, # One hour before recovery
                current_day_type,
                schedule
            ) or 0.0
            target_temperature = conditioning_ramping(
                ramping_time = int(post_event_recovery_timestamp_dict["end_timestamp"] - post_event_recovery_timestamp_dict["start_timestamp"]),
                current_timestamp = current_timestamp,
                initial_value = init_zone_temperature_after_gdp_event,
                target_value = max_target_temperature_post_event_recovery, # No flexibility during recovery, just return to target
            )
        else:
            target_temperature = init_target_temperature # No adjustment outside GDP event hours to keep user comfort

    # Get target temperature from schedule and apply flexibility
    if target_temperature is not None:
        logger.debug(f"Zone {zone_id}: target temperature = {target_temperature} °C")
        return target_temperature
    else:
        logger.debug(f"Zone {zone_id}: no target temperature found")
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

def conditioning_ramping(ramping_time:int, elapsed_time: int, initial_value:float, target_value: float) -> float:
    # Clamp elapsed time between 0 and ramping_time
    if elapsed_time <= 0:
        return round(initial_value, 2)
    if elapsed_time >= ramping_time:
        return round(target_value, 2)
        
    # Linear interpolation
    ratio = elapsed_time / ramping_time
    y = initial_value + (target_value - initial_value) * ratio
    return round(y, 2)

def retrieve_gdp_events() -> List[Dict[str, Any]]:
    # Retrieve GDP events from Hydro-Quebec API or mock data
    gdp_events_data = _get_mock_gdp_events()
    if gdp_events_data:
        return gdp_events_data["results"]
        
    # Define the API endpoint
    api_url = os.getenv("HQ_API_URL", "https://donnees.hydroquebec.com/api/explore/v2.1/catalog/datasets/evenements-pointe/records")

    # Set query parameters
    params = {
        "select": "datedebut,datefin,plagehoraire",
        "where": 'offre="CPC-D"',
        "order_by": "datedebut DESC",
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
        today = datetime.datetime.now().date()
        today_events = [
            event
            for event in gdp_events_data["results"]
            if datetime.datetime.strptime(event["datedebut"], "%Y-%m-%dT%H:%M:%S%z").date()
            == today
        ]

        if not today_events:
            logger.debug("No GDP events found for today")
        else:
            logger.debug(
                "Todays's GDP events: %s", json.dumps(today_events, indent=4)
            )
        return today_events
    
def _get_mock_gdp_events() -> List[Dict[str, Any]]:
    # Load configuration
    with open(os.getenv("GDP_EVENTS_PATH", "/data/options.json"), "r") as file_path:
        options_data = json.load(file_path)

    config_gdp_events = options_data["gdp_events"]
    
    if not config_gdp_events:
        # No mock GDP events specified in config
        logger.debug("No mock GDP events data found in config.yaml")
        return []
    else:
        # Use mock GDP events from config
        logger.debug("Using mock GDP events data from config.yaml")
        event_date = datetime.date.fromisoformat(config_gdp_events["date"])
        plage = config_gdp_events["time_spec"].upper()

        # Check if the mock event date matches today's date
        if event_date != datetime.datetime.now().date():
            logger.debug("Mock GDP event date does not match today's date")
            return []

        # Define AM / PM time windows
        def fmt(dt):
            return dt.isoformat() + "+00:00"

        AM_START = datetime.time(11, 0, 0) # Start in UTC
        AM_END   = datetime.time(14, 0, 0) # End in UTC

        PM_START = datetime.time(21, 0, 0) # Start in UTC
        PM_END   = datetime.time(1, 0, 0) # End in UTC (next day)

        results = []

        if plage in ("AM", "AM/PM"):
            results.append({
                "datedebut": fmt(datetime.datetime.combine(event_date, AM_START)),
                "datefin":   fmt(datetime.datetime.combine(event_date, AM_END)),
                "plagehoraire": "AM"
            })

        if plage in ("PM", "AM/PM"):
            # PM crosses midnight → end is next day
            end_date = event_date + datetime.timedelta(days=1)
            results.append({
                "datedebut": fmt(datetime.datetime.combine(event_date, PM_START)),
                "datefin":   fmt(datetime.datetime.combine(end_date, PM_END)),
                "plagehoraire": "PM"
            })

        # Normalize: "11H" → "11:00"
        if plage.endswith("H"):
            hour = int(plage.replace("H", ""))
            date_debut = datetime.datetime.combine(event_date, datetime.time(hour, 0), tzinfo=ZoneInfo("America/Montreal"))
            date_debut_utc = date_debut.astimezone(datetime.timezone.utc)
            results.append({
                "datedebut": date_debut_utc.isoformat(),
                "datefin":   (date_debut_utc + datetime.timedelta(hours=3)).isoformat(),
                "plagehoraire": None
            })

        # Format “HH:MM”
        if ":" in plage:
            hour, minute = [int(x) for x in plage.split(":")]
            results.append({
                "datedebut": fmt(datetime.datetime.combine(event_date, datetime.time(hour, minute))),
                "datefin":   fmt(datetime.datetime.combine(event_date, datetime.time(hour, minute)) + datetime.timedelta(hours=3)),
                "plagehoraire": None
            })

        gdp_events_data = {
            "total_count": len(results),
            "results": results,
        }

        return gdp_events_data
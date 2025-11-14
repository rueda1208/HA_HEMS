import os
import yaml
import copy
import logging
import numpy as np
from datetime import datetime
from typing import Union, Any, Dict, List
from logging.handlers import TimedRotatingFileHandler

CONFIG_FILE_PATH = os.getenv("CONFIG_FILE_PATH", "config/config.yaml")

def setup_logging(filename: str = "logger.log"):
    # Create a TimedRotatingFileHandler
    file_handler = TimedRotatingFileHandler(
        f"logs/{filename}",  # Base log file name
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
    logging.basicConfig(
        level=logging.DEBUG,  # Adjust the level as needed
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
                    "flexibility": 0.0,
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

    print(f"'{CONFIG_FILE_PATH}' updated successfully with new zones (if any).")

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

def get_target_temperature(zone_id: str, hvac_mode:str | None = None) -> Union[float, None]:
    # Load config
    with open(CONFIG_FILE_PATH, "r") as f:
        config = yaml.safe_load(f)

    hvac_systems = config.get("hvac_systems", {})
    zone_settings = hvac_systems.get(zone_id, {})
    if hvac_mode is None:
        schedule = zone_settings.get("schedule", {})
    else:
        schedule = zone_settings.get(hvac_mode, {}).get("schedule", {})
    

    # Determine day type and current hour
    now = datetime.now()
    current_hour = now.hour
    current_day_type = "weekday" if now.weekday() < 5 else "weekend"

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
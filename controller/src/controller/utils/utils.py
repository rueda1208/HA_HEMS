import datetime
import logging
import os

from enum import Enum
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Dict, Tuple, Union

import numpy as np
import yaml

from controller.utils.configuration import ConfigurationClient, MockConfigurationClient, RestConfigurationClient
from controller.utils.peak_events import BasePeakEventClient, MockPeakEventClient, PeakEvent, PeakEventClient


logger = logging.getLogger(__name__)
CONFIG_FILE_PATH = os.getenv("CONFIG_FILE_PATH")
LOGS_DIR = os.getenv("LOGS_DIR", "/share/controller/logs")


class HeatPumpMode(str, Enum):
    HEAT = "heat"
    COOL = "cool"
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

    # Configure the root logger
    logger_level = os.getenv("LOGLEVEL", "DEBUG").upper()
    logging.basicConfig(
        level=logger_level,
        handlers=[
            file_handler,  # Log to file with rotation
            logging.StreamHandler(),  # Optionally log to console
        ],
    )


def create_cop_model() -> Dict[HeatPumpMode, np.poly1d]:
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

    return {HeatPumpMode.COOL: cooling_cop_model, HeatPumpMode.HEAT: heating_cop_model}


def select_zones_hp_impact(with_impact: bool, configuration: Dict[str, Dict]) -> Dict[str, float]:
    heat_pump_enabled = (
        str(
            configuration.get("climate.heat_pump", {}).get("automated_control_enabled", {}).get("value", "false")
        ).lower()
        == "true"
    )

    result = {}
    for device_id, device_configuration in configuration.items():
        # Skip heat pump itself
        if "heat_pump" in device_id:
            continue

        # Get heat pump impact setting
        impact = float(device_configuration.get("heat_pump_impact", {}).get("value", 0.0))

        # Heat pump disabled
        if not heat_pump_enabled:
            if with_impact:
                continue
            result[device_id] = 0.0
            continue

        # Heat pump enabled
        if (impact > 0.0) == with_impact:
            result[device_id] = impact

    return result


def get_target_temperature(
    zone_id: str, configuration: Dict[str, Any], devices_states: Dict[str, Any], gdp_event: PeakEvent | None
) -> Union[float, None]:

    # Determine day type and current hour
    now = datetime.datetime.now().astimezone()
    current_hour = now.hour
    today = now.weekday()  # Current day of the week as an integer (0=Monday, 6=Sunday)
    day_of_week = (today + 1) % 7  # Convert to Sunday=0, Monday=1, ..., Saturday=6

    # Get initial target temperature from schedule or manual override
    init_target_temperature, manual_override = get_target_from_schedule(
        current_hour, day_of_week, devices_states, configuration, zone_id
    )

    if manual_override:
        logger.debug(f"Zone {zone_id}: manual override detected with target temperature = {init_target_temperature} °C")
        return init_target_temperature

    # Check for GDP events today
    if not gdp_event:
        logger.debug("No GDP events today, using regular schedule")
        return init_target_temperature

    # Get target temperature from schedule and apply flexibility
    target_temperature = _get_target_from_gdp_event(
        init_target_temperature, now, day_of_week, devices_states, configuration, zone_id, gdp_event
    )

    logger.debug(f"Zone {zone_id}: target temperature = {target_temperature} °C")
    return target_temperature


def _get_target_from_gdp_event(
    init_target_temperature: float,
    now: datetime.datetime,
    day_of_week: int,
    devices_states: Dict[str, Any],
    configuration: Dict[str, Any],
    zone_id: str,
    gdp_event: PeakEvent,
) -> float:
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
    zone_settings = configuration.get(zone_id, {})
    flexibility_upward = float(zone_settings.get("flexibility_upward", {}).get("value", 0.0))
    flexibility_downward = float(zone_settings.get("flexibility_downward", {}).get("value", 0.0))
    zone_preconditioning = zone_settings.get("preconditioning", {}).get("value", "false").lower() == "true"

    if now >= gdp_timestamp_dict["start"] and now < gdp_timestamp_dict["end"]:
        # Negative for lowering temp during event
        target_temperature = init_target_temperature - flexibility_downward
    elif (
        zone_preconditioning
        and now >= preconditioning_timestamp_dict["start"]
        and now < preconditioning_timestamp_dict["end"]
    ):
        # Calculate max target temperature during GDP event hours for preconditioning
        start_preconditioning_hour = (preconditioning_timestamp_dict["start"]).hour
        start_gdp_hour = (gdp_timestamp_dict["start"]).hour
        stop_gdp_hour = (gdp_timestamp_dict["end"]).hour

        max_target_temperature_at_gdp_event, _ = (
            get_target_from_schedule(start_gdp_hour, day_of_week, devices_states, configuration, zone_id) or 0.0
        )

        for hour in range(start_gdp_hour, stop_gdp_hour):
            max_target_temperature_at_gdp_event = max(
                max_target_temperature_at_gdp_event,
                (get_target_from_schedule(hour, day_of_week, devices_states, configuration, zone_id)[0]) or 0.0,
            )

        # Positive for raising temp during preconditioning
        target_temperature = conditioning_ramping(
            ramping_time=int(
                (preconditioning_timestamp_dict["end"] - preconditioning_timestamp_dict["start"]).total_seconds()
            ),
            elapsed_time=int((now - preconditioning_timestamp_dict["start"]).total_seconds()),
            initial_value=(
                get_target_from_schedule(
                    start_preconditioning_hour, day_of_week, devices_states, configuration, zone_id
                )[0]
            )
            or 0.0,
            target_value=flexibility_upward + max_target_temperature_at_gdp_event,
        )
    elif now >= post_event_recovery_timestamp_dict["start"] and now < post_event_recovery_timestamp_dict["end"]:
        stop_post_event_hour = (post_event_recovery_timestamp_dict["end"]).hour

        max_target_temperature_post_event_recovery, _ = get_target_from_schedule(
            stop_post_event_hour, day_of_week, devices_states, configuration, zone_id
        )

        init_zone_temperature_after_gdp_event = (
            get_target_from_schedule(
                (post_event_recovery_timestamp_dict["start"]).hour - 1,  # One hour before recovery
                day_of_week,
                devices_states,
                configuration,
                zone_id,
            )[0]
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

    return target_temperature


def _time_str_to_minutes(time_string: str) -> int:
    hour_str, minute_str = time_string.split(":")
    return int(hour_str) * 60 + int(minute_str)


# TODO: Refactor this function to handle hours and minutes in schedule time slots (ex.: 10h30-15h45)
def get_target_from_schedule(
    current_hour: int, day_of_week: int, devices_states: Dict[str, Any], configuration: Dict[str, Any], device_id: str
) -> Tuple[float, bool]:
    """
    Retourne la dernière consigne applicable (en °C) à partir de la cédule hebdomadaire ou s'il y a eu une modification
    manuelle récente (ex.: override sur thermostat ou IHD) qui s'applique, le 2e parametre sera True si un override est
    présent.

    - `day_of_week`: 0 = dimanche, 1 = lundi, ..., 6 = samedi
    - `schedule["setpoint"]`: {
          "0": { "22:00": "18", ... },
          "1": { "00:00": "18", "06:00": "21", ... },
          ...
      }

    La recherche se fait en reculant dans le temps (même jour puis jours précédents,
    en bouclant sur la semaine) jusqu'à trouver la dernière entrée applicable.
    """
    device_settings = configuration.get(device_id, {})
    schedule = device_settings.get("schedule", {})

    if "setpoint" not in schedule:
        logger.warning(
            "No schedule found in configuration for device %s, returning default value for target temperature",
            device_id,
        )
    else:
        setpoint_schedule = schedule["setpoint"]

        current_minutes = current_hour * 60

        # On recule sur un maximum de 7 jours (une semaine complète)
        for offset in range(0, 7):
            day = (day_of_week - offset) % 7
            day_key = str(day)
            day_schedule = setpoint_schedule.get(day_key)

            if not day_schedule:
                continue

            # Convertit les entrées "HH:MM": temperature -> minutes: temperature
            converted_schedule: list[tuple[int, float]] = []
            for time_string, target_temperature_raw_value in day_schedule.items():
                minutes = _time_str_to_minutes(time_string)
                target_temperature = float(target_temperature_raw_value)
                converted_schedule.append((minutes, target_temperature))

            if not converted_schedule:
                continue

            # Trie par heure croissante
            converted_schedule.sort(key=lambda x: x[0])

            if offset == 0:
                # Même jour: on ne garde que les entrées <= heure actuelle
                candidates = [
                    schedule_entry for schedule_entry in converted_schedule if schedule_entry[0] <= current_minutes
                ]

                if not candidates:
                    continue

                # Dernière entrée avant ou à l'heure courante
                minutes, target_temperature = candidates[-1]

                # Convert schedule entry time to timestamp for comparison with manual override entries
                schedule_entry_timestamp = (
                    datetime.datetime.combine(
                        datetime.datetime.now().astimezone().date() - datetime.timedelta(days=offset),
                        datetime.time(hour=minutes // 60, minute=minutes % 60),
                    )
                    .astimezone()
                    .timestamp()
                )

                # Check for manual override after this schedule entry
                manual_override_temperature = _get_manual_override_temperature(
                    schedule_entry_timestamp, devices_states, configuration, device_id
                )

                if manual_override_temperature is not None:
                    return manual_override_temperature, True

                return target_temperature, False
            else:
                # Jour précédent dans la semaine: toute heure de ce jour est "avant" maintenant.
                # On prend simplement la dernière entrée de ce jour.
                minutes, target_temperature = converted_schedule[-1]

                # Convert schedule entry time to timestamp for comparison with manual override entries
                schedule_entry_timestamp = (
                    datetime.datetime.combine(
                        datetime.datetime.now().astimezone().date() - datetime.timedelta(days=offset),
                        datetime.time(hour=minutes // 60, minute=minutes % 60),
                    )
                    .astimezone()
                    .timestamp()
                )

                # Check for manual override newer than this schedule entry
                manual_override_temperature = _get_manual_override_temperature(
                    schedule_entry_timestamp, devices_states, configuration, device_id
                )

                if manual_override_temperature is not None:
                    return manual_override_temperature, True

                return target_temperature, False

    # Aucune consigne trouvée sur la semaine ou aucune cédule trouvée pour ce device
    # Retourner le setpoint par default des parametres (qui pourrait être un default, manual override ou metric)
    default_temperature = device_settings.get("setpoint", {}).get("value")
    if default_temperature is None:
        logger.error(
            "No target temperature found in schedule for device %s and no default value set, returning 21C as fallback",
            device_id,
        )
        default_temperature = 21.0

    return default_temperature, False


def _get_manual_override_temperature(
    schedule_entry_timestamp: float, devices_states: Dict[str, Any], configuration: Dict[str, Any], device_id: str
) -> float | None:
    # Check if there is a manual override entry in the configuration for the device_id (from IHD)
    setpoint_configuration = configuration.get(device_id, {}).get("setpoint", {})

    if setpoint_configuration.get("source", {}) == "parameter_override":
        timestamp_value = setpoint_configuration.get("timestamp", 0)
        override_timestamp = datetime.datetime.fromisoformat(timestamp_value).timestamp()
        if override_timestamp > schedule_entry_timestamp:
            # Manual override is newer than the schedule entry, it should take precedence
            override_value = setpoint_configuration.get("value")

            if override_value is not None:
                logger.debug(
                    f"Device {device_id}: manual override from IHD with target temperature = {override_value} °C"
                )
                return float(override_value)

    # TODO Check if there is a manual override done by looking at devices_states (from Home Assistant/Thermostat)
    device_state = devices_states.get(device_id, {})

    return None


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
    configuration_path = os.getenv("MOCK_CONFIGURATION_PATH")

    configuration_client: ConfigurationClient

    if configuration_path is not None:
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

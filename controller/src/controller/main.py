import logging
import os
import time

import requests
import schedule

from sqlalchemy import create_engine

from controller.controller import Controller
from controller.ha_interface.ha_interface import HomeAssistantDeviceInterface
from controller.utils import utils


def main() -> None:
    # Set up logging
    utils.setup_logging("controller.log")
    logger = logging.getLogger(__name__)
    logger.info("Starting controller module ...")

    base_url = os.getenv("BASE_HA_URL", "http://supervisor/core")
    token = str(os.getenv("SUPERVISOR_TOKEN"))
    hems_api_base_url = os.getenv("HEMS_API_BASE_URL", "http://hems-api.hydroquebec.lab:8500")
    building_id = os.getenv("BUILDING_ID")

    # Retrieve the list of devices from Home Assistant.
    ha_interface = HomeAssistantDeviceInterface(base_url, token)

    # Get TimescaleDB connection parameters
    postgres_db_name = os.getenv("POSTGRES_NAME", "homeassistant")
    postgres_db_user = os.getenv("POSTGRES_USER", "postgres")
    postgres_db_host = os.getenv("POSTGRES_HOST", "77b2833f-timescaledb")
    postgres_db_port = os.getenv("POSTGRES_PORT", "5432")
    postgres_db_password = os.getenv("POSTGRES_PASSWORD", "homeassistant")

    # Create database connection URL
    db_url = f"postgresql://{postgres_db_user}:{postgres_db_password}@{postgres_db_host}:{postgres_db_port}/{postgres_db_name}"

    # Create SQLAlchemy engine
    postgres_db_engine = create_engine(db_url)

    controller = Controller(postgres_db_engine)

    # Main control loop
    def _main_loop():
        # Get the state of all the devices in Home Assistant
        devices_states = ha_interface.get_devices_states()

        control_actions = controller.get_control_actions(devices_states)
        ha_interface.execute_control_actions(control_actions, devices_states)

        metric = {
            "metrics": [
                {
                    "name": "home_automation",
                    "fields": {"name": "refresh_status", "value": "success"},
                    "tags": {"device_id": "ha_controller", "metric_type": "event"},
                    "timestamp": int(time.time()),
                }
            ]
        }
        requests.post(f"{hems_api_base_url}/api/devices/{building_id}", json=metric, verify=False)

    try:
        # Execute one time on start
        _main_loop()

        # Schedule the job every N seconds
        schedule.every(120).seconds.do(_main_loop)

        # Run the scheduler in a loop
        while True:
            schedule.run_pending()
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Application interrupted by the user")

    except Exception as ex:
        logger.error("An error occurred: %s", ex, exc_info=True)

    logger.info("Waiting 5 minutes before restarting the module, to avoid overloading the gdp server")
    time.sleep(300)


if __name__ == "__main__":
    main()

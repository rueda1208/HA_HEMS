import logging
import os
import time

import schedule

from controller.ha_interface.ha_interface import HomeAssistantDeviceInterface
from controller.utils import utils


# Set up logging
utils.setup_logging("controller.log")
logger = logging.getLogger(__name__)

HA_CREDENTIALS = {
    "base_url": os.getenv("BASE_HA_URL", "http://supervisor/core"),
    "token": os.getenv("SUPERVISOR_TOKEN"),
}


def main() -> None:
    logger.info("Starting controller module ...")

    # Retrieve the list of devices from Home Assistant.
    ha_interface = HomeAssistantDeviceInterface(**HA_CREDENTIALS)

    # Get list of devices from Home Assistant
    devices_list = ha_interface.get_devices_list()

    # Update config.yaml with any new zones found in Home Assistant
    utils.update_config_with_zones(zones=devices_list)

    # Create heat pump COP model from config data
    heat_pump_cop_models = utils.create_cop_model()

    # Main control loop
    def _main_loop():
        devices_state = ha_interface.get_device_state(devices_id=devices_list)
        control_actions = ha_interface.get_control_actions(
            devices_state=devices_state, heat_pump_cop_models=heat_pump_cop_models
        )
        ha_interface.execute_control_actions(control_actions=control_actions, devices_state=devices_state)

    try:
        # Execute one time on start
        _main_loop()

        # Schedule the job every 5 minutes
        schedule.every().hour.at(":00").do(_main_loop)
        schedule.every().hour.at(":05").do(_main_loop)
        schedule.every().hour.at(":10").do(_main_loop)
        schedule.every().hour.at(":15").do(_main_loop)
        schedule.every().hour.at(":20").do(_main_loop)
        schedule.every().hour.at(":25").do(_main_loop)
        schedule.every().hour.at(":30").do(_main_loop)
        schedule.every().hour.at(":35").do(_main_loop)
        schedule.every().hour.at(":40").do(_main_loop)
        schedule.every().hour.at(":45").do(_main_loop)
        schedule.every().hour.at(":50").do(_main_loop)
        schedule.every().hour.at(":55").do(_main_loop)

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
    log_level = os.getenv("LOGLEVEL", "DEBUG").upper()
    logging_format = "%(asctime)s [%(levelname)5s] %(message)s (%(filename)s:%(lineno)s)"
    if os.getenv("LOCAL_LOG_FILE", False):
        logging.basicConfig(
            filename="/share/controller/logs/controller.log",
            level=log_level,
            format=logging_format,
            filemode="w",
        )
    else:
        logging.basicConfig(level=log_level, format=logging_format)

    main()

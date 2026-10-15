import logging

from controller.application import ControllerRuntime
from controller.utils import utils


logger = logging.getLogger(__name__)


def main() -> None:
    utils.setup_logging("controller.log")
    logger.info("Starting controller module ...")
    ControllerRuntime.from_environment().run()


if __name__ == "__main__":
    main()

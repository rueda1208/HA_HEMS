import logging

from typing import Any, Dict

from controller.base import ControlContext


logger = logging.getLogger(__name__)


class BatteryController:
    def get_control_actions(self, context: ControlContext) -> Dict[str, Any]:
        logger.info(f"Getting control actions for battery device {context.device_id} *** NOT IMPLEMENTED YET ***")
        return {}

import logging

from typing import Any, Dict

from controller.utils.peak_events import PeakEvent


logger = logging.getLogger(__name__)


class WaterHeaterController:
    def get_control_actions(
        self,
        device_id: str,
        device_configuration: Dict[str, Any],
        all_devices_configurations: Dict[str, Any],
        devices_states: Dict[str, Any],
        gdp_event: PeakEvent | None,
    ) -> Dict[str, Any]:
        logger.info(f"Getting control actions for water heater device {device_id} *** NOT IMPLEMENTED YET ***")
        control_actions: Dict[str, Any] = {}
        return control_actions

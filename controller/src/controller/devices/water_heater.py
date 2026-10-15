import logging

from datetime import datetime
from threading import Lock
from typing import Any, Dict

from controller.base import ControlContext, SwitchControlAction
from controller.utils.peak_events import PeakEvent


logger = logging.getLogger(__name__)


class WaterHeaterController:
    def __init__(self) -> None:
        self._cancelled_events: dict[str, tuple[str, str]] = {}
        self._current_events: dict[str, tuple[str, str]] = {}
        self._event_lock = Lock()

    @staticmethod
    def get_switch_entity_id(device_id: str, configuration: Dict[str, Any]) -> str | None:
        configured_entity = configuration.get("switch_entity_id")
        if isinstance(configured_entity, dict):
            configured_entity = configured_entity.get("value")
        if configured_entity is None and device_id.startswith("switch."):
            configured_entity = device_id
        if not isinstance(configured_entity, str) or not configured_entity.startswith("switch."):
            return None
        return configured_entity

    @staticmethod
    def _event_key(event: PeakEvent) -> tuple[str, str]:
        return event.datedebut.isoformat(), event.datefin.isoformat()

    @staticmethod
    def _parse_last_changed(device_state: Dict[str, Any]) -> datetime | None:
        last_changed = device_state.get("last_changed")
        if not isinstance(last_changed, str):
            return None
        try:
            parsed = datetime.fromisoformat(last_changed)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None

    def cancel_gdp_for_event(self, device_id: str, event: PeakEvent) -> None:
        event_key = self._event_key(event)
        with self._event_lock:
            self._cancelled_events[device_id] = event_key
        logger.info(
            "Water heater GDP cancelled for device %s after an ON transition during event %s to %s",
            device_id,
            event.datedebut.isoformat(),
            event.datefin.isoformat(),
        )

    def get_control_actions(self, context: ControlContext) -> Dict[str, Any]:
        switch_entity_id = self.get_switch_entity_id(context.device_id, context.device_configuration)
        if switch_entity_id is None:
            logger.error(
                "Water heater %s has no valid switch_entity_id configuration",
                context.device_id,
            )
            return {}

        device_state = context.devices_states.get(switch_entity_id)
        if not isinstance(device_state, dict):
            logger.error("Water heater switch state %s is missing", switch_entity_id)
            return {}

        current_state = device_state.get("state")
        if current_state not in {"on", "off"}:
            logger.warning("Water heater switch %s has non-controllable state %r", switch_entity_id, current_state)
            return {}

        event = context.gdp_event
        if event is None:
            with self._event_lock:
                previous_event = self._current_events.get(context.device_id)
            if previous_event is None:
                return {}
            event_end = datetime.fromisoformat(previous_event[1])
            if context.now < event_end:
                return {}
            return self._restore_after_event(context.device_id, switch_entity_id, device_state, current_state, event_end)

        event_key = self._event_key(event)
        if context.gdp_device_ids is not None and context.device_id not in context.gdp_device_ids:
            return {}

        with self._event_lock:
            previous_event = self._current_events.get(context.device_id)
        if (
            previous_event is not None
            and previous_event != event_key
            and context.now >= datetime.fromisoformat(previous_event[1])
        ):
            restore_action = self._restore_after_event(
                context.device_id,
                switch_entity_id,
                device_state,
                current_state,
                datetime.fromisoformat(previous_event[1]),
            )
            if restore_action:
                return restore_action

        with self._event_lock:
            if self._current_events.get(context.device_id) != event_key:
                self._current_events[context.device_id] = event_key
                if self._cancelled_events.get(context.device_id) != event_key:
                    self._cancelled_events.pop(context.device_id, None)

        if context.now < event.datedebut:
            return {}

        if context.now >= event.datefin:
            return self._restore_after_event(
                context.device_id,
                switch_entity_id,
                device_state,
                current_state,
                event.datefin,
            )

        with self._event_lock:
            cancelled = self._cancelled_events.get(context.device_id) == event_key
        if cancelled:
            return {}

        if current_state == "on":
            last_changed = self._parse_last_changed(device_state)
            if last_changed is None:
                logger.warning(
                    "Cannot determine whether water heater %s was switched on during GDP; "
                    "leaving it unchanged for safety",
                    switch_entity_id,
                )
                return {}
            if last_changed >= event.datedebut:
                with self._event_lock:
                    self._cancelled_events[context.device_id] = event_key
                logger.info(
                    "Water heater GDP cancelled for %s because its ON state began during the event",
                    context.device_id,
                )
                return {}
            return {
                switch_entity_id: SwitchControlAction(
                    desired_state="off",
                    reason="gdp_event_reduction",
                )
            }

        return {}

    def _restore_after_event(
        self,
        device_id: str,
        switch_entity_id: str,
        device_state: Dict[str, Any],
        current_state: str,
        event_end: datetime,
    ) -> Dict[str, Any]:
        if current_state == "on":
            with self._event_lock:
                self._current_events.pop(device_id, None)
                self._cancelled_events.pop(device_id, None)
            return {}

        last_changed = self._parse_last_changed(device_state)
        if last_changed is None:
            logger.warning(
                "Cannot determine whether water heater %s was switched off after GDP; leaving it unchanged",
                switch_entity_id,
            )
            return {}
        if last_changed >= event_end:
            with self._event_lock:
                self._current_events.pop(device_id, None)
                self._cancelled_events.pop(device_id, None)
            return {}
        return {
            switch_entity_id: SwitchControlAction(
                desired_state="on",
                reason="gdp_event_end",
            )
        }

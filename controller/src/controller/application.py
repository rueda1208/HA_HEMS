import logging
import os
import time
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Any

import requests
from sqlalchemy import create_engine

from controller.base import SetpointOverride, SwitchControlAction
from controller.controller import Controller
from controller.devices.water_heater import WaterHeaterController
from controller.ha_interface.ha_events import (
    HomeAssistantEventListener,
    SetpointStateChange,
    event_timestamp,
    setpoint_state_change_from_event,
)
from controller.ha_interface.ha_interface import HomeAssistantDeviceInterface
from controller.utils.hems_snapshot import (
    HemsSnapshot,
    fetch_hems_snapshot,
    setpoint_entity_ids,
    water_heater_switch_entity_ids,
)
from controller.utils.configuration_logging import log_configuration_changes
from controller.utils.runtime_config import RuntimeConfig
from controller.utils.device_type import DeviceType


logger = logging.getLogger(__name__)


def dispatch_control_actions(
    ha_interface: HomeAssistantDeviceInterface,
    control_actions: dict[str, Any],
    devices_states: dict[str, Any],
    control_mode: str,
    allowlist: set[str] | None,
) -> None:
    if control_mode == "shadow":
        logger.info("SHADOW MODE: proposed control actions: %s", control_actions)
        try:
            ha_interface.save_shadow_control_actions(control_actions)
        except Exception:
            logger.exception("Failed to save shadow control proposals to TimescaleDB")
        return

    if control_mode != "live":
        raise ValueError(f"Unsupported CONTROL_MODE: {control_mode}")
    if allowlist is not None and not allowlist:
        raise ValueError("Live control requires a non-empty CONTROL_ALLOWLIST")

    allowed_actions = (
        control_actions.copy()
        if allowlist is None
        else {entity_id: action for entity_id, action in control_actions.items() if entity_id in allowlist}
    )
    blocked_entity_ids = set(control_actions) - set(allowed_actions)
    logger.info("Live actions: %s; blocked by allowlist: %s", allowed_actions, sorted(blocked_entity_ids))
    if allowed_actions:
        ha_interface.execute_control_actions(allowed_actions, devices_states)


class ControllerRuntime:
    def __init__(
        self,
        config: RuntimeConfig,
        ha_interface: HomeAssistantDeviceInterface,
        controller: Controller,
    ) -> None:
        self.config = config
        self.ha_interface = ha_interface
        self.controller = controller

        self.trigger_queue: Queue[None] = Queue(maxsize=1)
        self.hems_refresh_queue: Queue[str] = Queue(maxsize=1)
        self.stop_event = Event()
        self.snapshot_lock = Lock()
        self.override_lock = Lock()
        self.setpoint_change_queue: Queue[SetpointStateChange | None] = Queue()
        self.snapshot: HemsSnapshot | None = None
        self.ha_setpoint_overrides: dict[str, SetpointOverride] = {}
        self.last_controller_setpoints: dict[str, float] = {}

        self.event_listener = HomeAssistantEventListener(
            base_url=config.base_url,
            token=config.token,
            on_state_changed=self._on_ha_setpoint_changed,
            relevant_entity_ids=None,
            relevant_attribute="temperature",
        )
        self.water_heater_event_listener = HomeAssistantEventListener(
            base_url=config.base_url,
            token=config.token,
            on_state_changed=self._on_ha_water_heater_state_changed,
            relevant_entity_ids=set(),
        )
        self.state_change_writer_thread = Thread(
            target=self._persist_setpoint_state_changes,
            name="ha-setpoint-event-writer",
            daemon=True,
        )
        self.poll_thread = Thread(target=self._poll_hems, name="hems-poller", daemon=True)

    @classmethod
    def from_environment(cls) -> "ControllerRuntime":
        config = RuntimeConfig.from_environment()
        ha_interface = HomeAssistantDeviceInterface(
            config.base_url,
            config.token,
            allow_control=config.control_mode == "live",
        )

        postgres_db_name = os.getenv("POSTGRES_NAME", "homeassistant")
        postgres_db_user = os.getenv("POSTGRES_USER", "postgres")
        postgres_db_host = os.getenv("POSTGRES_HOST", "77b2833f-timescaledb")
        postgres_db_port = os.getenv("POSTGRES_PORT", "5432")
        postgres_db_password = os.getenv("POSTGRES_PASSWORD", "homeassistant")
        db_url = (
            f"postgresql+psycopg2://{postgres_db_user}:{postgres_db_password}"
            f"@{postgres_db_host}:{postgres_db_port}/{postgres_db_name}"
        )
        controller = Controller(create_engine(db_url))
        return cls(config, ha_interface, controller)

    def _request_control_evaluation(self, source: str) -> None:
        try:
            self.trigger_queue.put_nowait(None)
            logger.debug("Queued control evaluation from %s", source)
        except Full:
            logger.debug("Control evaluation already queued; coalescing %s trigger", source)

    def _request_hems_refresh(self, source: str) -> None:
        try:
            self.hems_refresh_queue.put_nowait(source)
            logger.debug("Queued HEMS refresh from %s", source)
        except Full:
            logger.debug("HEMS refresh already queued; coalescing %s trigger", source)

    def _on_ha_setpoint_changed(self, event: dict[str, Any]) -> None:
        data = event.get("event", {}).get("data", {})
        entity_id = data.get("entity_id")
        old_temperature = (data.get("old_state") or {}).get("attributes", {}).get("temperature")
        new_temperature = (data.get("new_state") or {}).get("attributes", {}).get("temperature")
        logger.info("HA setpoint changed: %s %s -> %s", entity_id, old_temperature, new_temperature)

        state_change = setpoint_state_change_from_event(event)
        if state_change is not None:
            self.setpoint_change_queue.put_nowait(state_change)
            changed_entity_id = state_change.entity_id
            override = SetpointOverride(state_change.new_value, state_change.timestamp)
            with self.override_lock:
                commanded_setpoint = self.last_controller_setpoints.get(changed_entity_id)
                if commanded_setpoint is not None and abs(commanded_setpoint - override.value) < 0.05:
                    self.last_controller_setpoints.pop(changed_entity_id, None)
                    logger.debug("Ignoring HA event matching controller command for %s", changed_entity_id)
                else:
                    previous_override = self.ha_setpoint_overrides.get(changed_entity_id)
                    if previous_override is None or override.timestamp > previous_override.timestamp:
                        self.ha_setpoint_overrides[changed_entity_id] = override
        self._request_hems_refresh("ha_setpoint")

    def _on_ha_water_heater_state_changed(self, event: dict[str, Any]) -> None:
        data = event.get("event", {}).get("data", {})
        entity_id = data.get("entity_id")
        old_state = (data.get("old_state") or {}).get("state")
        new_state = (data.get("new_state") or {}).get("state")
        if not isinstance(entity_id, str) or old_state == new_state:
            return

        logger.info("HA water heater switch changed: %s %s -> %s", entity_id, old_state, new_state)
        if new_state == "on":
            changed_at = event_timestamp(event)
            with self.snapshot_lock:
                current_snapshot = self.snapshot
            gdp_event = current_snapshot.gdp_event if current_snapshot is not None else None
            if (
                changed_at is not None
                and gdp_event is not None
                and gdp_event.datedebut <= changed_at < gdp_event.datefin
                and current_snapshot is not None
            ):
                for device_id, configuration in current_snapshot.configurations.items():
                    if (
                        configuration.get("device_type") == DeviceType.WATER_HEATER
                        and WaterHeaterController.get_switch_entity_id(device_id, configuration) == entity_id
                    ):
                        self.controller.cancel_water_heater_gdp(device_id, gdp_event)

        self._request_control_evaluation("water_heater_switch")

    def _persist_setpoint_state_changes(self) -> None:
        while True:
            state_change = self.setpoint_change_queue.get()
            if state_change is None:
                return
            try:
                self.ha_interface.save_setpoint_state_change(
                    state_change.entity_id,
                    state_change.old_value,
                    state_change.new_value,
                    state_change.timestamp,
                )
            except Exception:
                logger.exception("Failed to persist HA setpoint transition for %s", state_change.entity_id)

    def _poll_hems(self) -> None:
        initial_refresh = True
        while not self.stop_event.is_set():
            if initial_refresh:
                refresh_source = "startup"
                initial_refresh = False
            else:
                try:
                    refresh_source = self.hems_refresh_queue.get(timeout=self.config.hems_poll_seconds)
                except Empty:
                    refresh_source = "hems_poll"

            try:
                fresh_snapshot = fetch_hems_snapshot()
                with self.snapshot_lock:
                    previous_configurations = (
                        self.snapshot.configurations if self.snapshot is not None else None
                    )
                log_configuration_changes(
                    previous_configurations,
                    fresh_snapshot.configurations,
                    refresh_source,
                )
                with self.snapshot_lock:
                    self.snapshot = fresh_snapshot
                self.event_listener.set_relevant_entity_ids(
                    setpoint_entity_ids(fresh_snapshot.configurations)
                )
                self.water_heater_event_listener.set_relevant_entity_ids(
                    water_heater_switch_entity_ids(fresh_snapshot.configurations)
                )
                logger.info("HEMS configuration and GDP snapshot refreshed")
            except Exception:
                logger.exception("HEMS polling failed; retaining the last successful snapshot")
            finally:
                self._request_control_evaluation(refresh_source)

    def _evaluate_control(self) -> None:
        with self.snapshot_lock:
            current_snapshot = self.snapshot
        if current_snapshot is None:
            logger.warning("Skipping evaluation until the first HEMS snapshot is available")
            return

        snapshot_age = time.monotonic() - current_snapshot.fetched_at
        if snapshot_age > self.config.max_snapshot_age_seconds:
            logger.error("Skipping control: HEMS snapshot is stale (%.1f seconds old)", snapshot_age)
            return

        devices_states = self.ha_interface.get_devices_states()
        with self.override_lock:
            current_ha_overrides = dict(self.ha_setpoint_overrides)
        control_actions = self.controller.get_control_actions(
            devices_states,
            configurations=current_snapshot.configurations,
            gdp_event=current_snapshot.gdp_event,
            ha_setpoint_overrides=current_ha_overrides,
        )

        commanded_setpoints: dict[str, float] = {}
        if self.config.control_mode == "live":
            for entity_id, action in control_actions.items():
                if self.config.allowlist is not None and entity_id not in self.config.allowlist:
                    continue
                if isinstance(action, SwitchControlAction):
                    continue
                commanded_setpoint = action.get("setpoint") if isinstance(action, dict) else action
                if commanded_setpoint is not None:
                    commanded_setpoints[entity_id] = float(commanded_setpoint)
            with self.override_lock:
                self.last_controller_setpoints.update(commanded_setpoints)

        try:
            dispatch_control_actions(
                self.ha_interface,
                control_actions,
                devices_states,
                self.config.control_mode,
                self.config.allowlist,
            )
        except Exception:
            if commanded_setpoints:
                with self.override_lock:
                    for entity_id, value in commanded_setpoints.items():
                        if self.last_controller_setpoints.get(entity_id) == value:
                            self.last_controller_setpoints.pop(entity_id, None)
            raise

        if self.config.hems_status_metrics_enabled:
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
            requests.post(
                f"{self.config.hems_api_base_url}/api/devices/{self.config.building_id}",
                json=metric,
                verify=False,
                timeout=10,
            )

    def run(self) -> None:
        try:
            self.state_change_writer_thread.start()
            self.event_listener.start()
            self.water_heater_event_listener.start()
            self.poll_thread.start()

            while True:
                try:
                    self.trigger_queue.get(timeout=self.config.reconciliation_seconds)
                except Empty:
                    logger.info("Running periodic HA state reconciliation")

                try:
                    self._evaluate_control()
                except Exception:
                    logger.exception("Control evaluation failed")

        except KeyboardInterrupt:
            logger.info("Application interrupted by the user")
        finally:
            self.stop_event.set()
            self.event_listener.stop()
            self.water_heater_event_listener.stop()
            self.setpoint_change_queue.put_nowait(None)
            self.state_change_writer_thread.join(timeout=10)
            if self.poll_thread.is_alive():
                self.poll_thread.join(timeout=5)

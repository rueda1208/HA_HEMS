import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Any

import requests

from sqlalchemy import create_engine

from controller.controller import Controller
from controller.base import SetpointOverride
from controller.ha_events import HomeAssistantEventListener
from controller.ha_interface.ha_interface import HomeAssistantDeviceInterface
from controller.utils import utils
from controller.utils.device_type import DeviceType
from controller.utils.peak_events import PeakEvent


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HemsSnapshot:
    configurations: dict[str, Any]
    gdp_event: PeakEvent | None
    fetched_at: float


def fetch_hems_snapshot() -> HemsSnapshot:
    return HemsSnapshot(
        configurations=utils.retrieve_device_configuration(),
        gdp_event=utils.retrieve_gdp_event(),
        fetched_at=time.monotonic(),
    )


def setpoint_entity_ids(configurations: dict[str, Any]) -> set[str]:
    return {
        entity_id
        for entity_id, configuration in configurations.items()
        if configuration.get("device_type") in (DeviceType.THERMOSTAT, DeviceType.HEAT_PUMP)
    }


def _get_hems_poll_seconds() -> float:
    return float(os.getenv("HEMS_POLL_SECONDS", "30"))


def _hems_status_metrics_enabled() -> bool:
    return os.getenv("HEMS_STATUS_METRICS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _request_hems_refresh(refresh_queue: Queue[str], source: str) -> None:
    try:
        refresh_queue.put_nowait(source)
        logger.debug("Queued HEMS refresh from %s", source)
    except Full:
        logger.debug("HEMS refresh already queued; coalescing %s trigger", source)


def _setpoint_override_from_event(event: dict[str, Any]) -> tuple[str, SetpointOverride] | None:
    data = event.get("event", {}).get("data", {})
    entity_id = data.get("entity_id")
    new_state = data.get("new_state") or {}
    setpoint = (new_state.get("attributes") or {}).get("temperature")
    timestamp_value = new_state.get("last_updated") or event.get("event", {}).get("time_fired")
    if not isinstance(entity_id, str) or setpoint is None or not isinstance(timestamp_value, str):
        return None

    try:
        timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return None
        return entity_id, SetpointOverride(float(setpoint), timestamp)
    except (TypeError, ValueError):
        return None


def dispatch_control_actions(
    ha_interface: HomeAssistantDeviceInterface,
    control_actions: dict[str, Any],
    devices_states: dict[str, Any],
    control_mode: str,
    allowlist: set[str],
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
    if not allowlist:
        raise ValueError("Live control requires a non-empty CONTROL_ALLOWLIST")

    allowed_actions = {entity_id: action for entity_id, action in control_actions.items() if entity_id in allowlist}
    blocked_entity_ids = set(control_actions) - set(allowed_actions)
    logger.info("Live actions: %s; blocked by allowlist: %s", allowed_actions, sorted(blocked_entity_ids))
    if allowed_actions:
        ha_interface.execute_control_actions(allowed_actions, devices_states)


def main() -> None:
    # Set up logging
    utils.setup_logging("controller.log")
    logger = logging.getLogger(__name__)
    logger.info("Starting controller module ...")

    base_url = os.getenv("BASE_HA_URL", "http://supervisor/core")
    token = os.getenv("SUPERVISOR_TOKEN")
    hems_api_base_url = os.getenv("HEMS_API_BASE_URL", "http://hems-api.hydroquebec.lab:8500")
    building_id = os.getenv("BUILDING_ID")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is required")
    if not building_id:
        raise RuntimeError("BUILDING_ID is required")

    control_mode = os.getenv("CONTROL_MODE", "shadow").strip().lower()
    if control_mode not in {"shadow", "live"}:
        raise ValueError("CONTROL_MODE must be 'shadow' or 'live'")
    allowlist = {item.strip() for item in os.getenv("CONTROL_ALLOWLIST", "").split(",") if item.strip()}
    if control_mode == "live" and not allowlist:
        raise ValueError("CONTROL_ALLOWLIST must contain at least one entity when CONTROL_MODE=live")
    hems_data_source = utils.get_hems_data_source()
    status_metrics_enabled = _hems_status_metrics_enabled() and hems_data_source == "api"
    if hems_data_source == "mock":
        logger.info("Using mock HEMS data; HEMS status metrics are disabled")

    # Retrieve the list of devices from Home Assistant.
    ha_interface = HomeAssistantDeviceInterface(base_url, token, allow_control=control_mode == "live")

    # Get TimescaleDB connection parameters
    postgres_db_name = os.getenv("POSTGRES_NAME", "homeassistant")
    postgres_db_user = os.getenv("POSTGRES_USER", "postgres")
    postgres_db_host = os.getenv("POSTGRES_HOST", "77b2833f-timescaledb")
    postgres_db_port = os.getenv("POSTGRES_PORT", "5432")
    postgres_db_password = os.getenv("POSTGRES_PASSWORD", "homeassistant")

    # Create database connection URL
    db_url = (
        f"postgresql+psycopg2://{postgres_db_user}:{postgres_db_password}"
        f"@{postgres_db_host}:{postgres_db_port}/{postgres_db_name}"
    )

    # Create SQLAlchemy engine
    postgres_db_engine = create_engine(db_url)

    controller = Controller(postgres_db_engine)
    hems_poll_seconds = _get_hems_poll_seconds()
    reconciliation_seconds = float(os.getenv("HA_RECONCILIATION_SECONDS", "900"))
    max_snapshot_age_seconds = float(os.getenv("HEMS_MAX_SNAPSHOT_AGE_SECONDS", "300"))
    if min(hems_poll_seconds, reconciliation_seconds, max_snapshot_age_seconds) <= 0:
        raise ValueError("Polling, reconciliation, and snapshot-age intervals must be greater than zero")

    trigger_queue: Queue[None] = Queue(maxsize=1)
    hems_refresh_queue: Queue[str] = Queue(maxsize=1)
    stop_event = Event()
    snapshot_lock = Lock()
    override_lock = Lock()
    snapshot: HemsSnapshot | None = None
    ha_setpoint_overrides: dict[str, SetpointOverride] = {}
    last_controller_setpoints: dict[str, float] = {}

    def request_control_evaluation(source: str) -> None:
        try:
            trigger_queue.put_nowait(None)
            logger.debug("Queued control evaluation from %s", source)
        except Full:
            logger.debug("Control evaluation already queued; coalescing %s trigger", source)

    def on_ha_setpoint_changed(event: dict[str, Any]) -> None:
        data = event.get("event", {}).get("data", {})
        entity_id = data.get("entity_id")
        old_temperature = (data.get("old_state") or {}).get("attributes", {}).get("temperature")
        new_temperature = (data.get("new_state") or {}).get("attributes", {}).get("temperature")
        logger.info("HA setpoint changed: %s %s -> %s", entity_id, old_temperature, new_temperature)
        candidate = _setpoint_override_from_event(event)
        if candidate is not None:
            changed_entity_id, override = candidate
            with override_lock:
                commanded_setpoint = last_controller_setpoints.get(changed_entity_id)
                if commanded_setpoint is not None and abs(commanded_setpoint - override.value) < 0.05:
                    last_controller_setpoints.pop(changed_entity_id, None)
                    logger.debug("Ignoring HA event matching controller command for %s", changed_entity_id)
                else:
                    previous_override = ha_setpoint_overrides.get(changed_entity_id)
                    if previous_override is None or override.timestamp > previous_override.timestamp:
                        ha_setpoint_overrides[changed_entity_id] = override
        _request_hems_refresh(hems_refresh_queue, "ha_setpoint")

    event_listener = HomeAssistantEventListener(
        base_url=base_url,
        token=token,
        on_state_changed=on_ha_setpoint_changed,
        relevant_entity_ids=None,
        relevant_attribute="temperature",
    )

    def poll_hems() -> None:
        nonlocal snapshot
        initial_refresh = True
        while not stop_event.is_set():
            if initial_refresh:
                refresh_source = "startup"
                initial_refresh = False
            else:
                try:
                    refresh_source = hems_refresh_queue.get(timeout=hems_poll_seconds)
                except Empty:
                    refresh_source = "hems_poll"

            try:
                fresh_snapshot = fetch_hems_snapshot()
                with snapshot_lock:
                    snapshot = fresh_snapshot
                event_listener.set_relevant_entity_ids(setpoint_entity_ids(fresh_snapshot.configurations))
                logger.info("HEMS configuration and GDP snapshot refreshed")
            except Exception:
                logger.exception("HEMS polling failed; retaining the last successful snapshot")
            finally:
                request_control_evaluation(refresh_source)

    poll_thread = Thread(target=poll_hems, name="hems-poller", daemon=True)

    def evaluate_control() -> None:
        with snapshot_lock:
            current_snapshot = snapshot
        if current_snapshot is None:
            logger.warning("Skipping evaluation until the first HEMS snapshot is available")
            return

        snapshot_age = time.monotonic() - current_snapshot.fetched_at
        if snapshot_age > max_snapshot_age_seconds:
            logger.error("Skipping control: HEMS snapshot is stale (%.1f seconds old)", snapshot_age)
            return

        devices_states = ha_interface.get_devices_states()
        with override_lock:
            current_ha_overrides = dict(ha_setpoint_overrides)
        control_actions = controller.get_control_actions(
            devices_states,
            configurations=current_snapshot.configurations,
            gdp_event=current_snapshot.gdp_event,
            ha_setpoint_overrides=current_ha_overrides,
        )

        commanded_setpoints: dict[str, float] = {}
        if control_mode == "live":
            for entity_id, action in control_actions.items():
                if entity_id not in allowlist:
                    continue
                commanded_setpoint = action.get("setpoint") if isinstance(action, dict) else action
                if commanded_setpoint is not None:
                    commanded_setpoints[entity_id] = float(commanded_setpoint)
            with override_lock:
                last_controller_setpoints.update(commanded_setpoints)

        try:
            dispatch_control_actions(ha_interface, control_actions, devices_states, control_mode, allowlist)
        except Exception:
            if commanded_setpoints:
                with override_lock:
                    for entity_id, value in commanded_setpoints.items():
                        if last_controller_setpoints.get(entity_id) == value:
                            last_controller_setpoints.pop(entity_id, None)
            raise

        if status_metrics_enabled:
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
            requests.post(f"{hems_api_base_url}/api/devices/{building_id}", json=metric, verify=False, timeout=10)

    try:
        event_listener.start()
        poll_thread.start()

        while True:
            try:
                trigger_queue.get(timeout=reconciliation_seconds)
            except Empty:
                logger.info("Running periodic HA state reconciliation")

            try:
                evaluate_control()
            except Exception:
                logger.exception("Control evaluation failed")

    except KeyboardInterrupt:
        logger.info("Application interrupted by the user")
    finally:
        stop_event.set()
        event_listener.stop()
        if poll_thread.is_alive():
            poll_thread.join(timeout=5)


if __name__ == "__main__":
    main()

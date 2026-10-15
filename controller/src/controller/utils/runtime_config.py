"""Load and validate process settings for the controller runtime."""

import logging
import os
from dataclasses import dataclass

from controller.utils.configuration import get_hems_data_source


logger = logging.getLogger(__name__)


def parse_control_allowlist(value: str) -> set[str] | None:
    normalized_value = value.strip()
    if normalized_value.lower() == "all":
        return None
    return {entity_id.strip() for entity_id in normalized_value.split(",") if entity_id.strip()}


def get_hems_poll_seconds() -> float:
    return float(os.getenv("HEMS_POLL_SECONDS", "30"))


def hems_status_metrics_enabled() -> bool:
    return os.getenv("HEMS_STATUS_METRICS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    token: str
    hems_api_base_url: str
    building_id: str
    control_mode: str
    allowlist: set[str] | None
    hems_status_metrics_enabled: bool
    hems_poll_seconds: float
    reconciliation_seconds: float
    max_snapshot_age_seconds: float

    @classmethod
    def from_environment(cls) -> "RuntimeConfig":
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
        allowlist = parse_control_allowlist(os.getenv("CONTROL_ALLOWLIST", ""))
        if control_mode == "live" and allowlist == set():
            raise ValueError(
                "CONTROL_ALLOWLIST must be 'all' or contain at least one entity "
                "when CONTROL_MODE=live"
            )
        if control_mode == "live" and allowlist is None:
            logger.warning("CONTROL_ALLOWLIST=all: live control is enabled for all generated actions")

        hems_data_source = get_hems_data_source()
        status_metrics_enabled = hems_status_metrics_enabled() and hems_data_source == "api"
        if hems_data_source == "mock":
            logger.info("Using mock HEMS data; HEMS status metrics are disabled")

        hems_poll_seconds = get_hems_poll_seconds()
        reconciliation_seconds = float(os.getenv("HA_RECONCILIATION_SECONDS", "900"))
        max_snapshot_age_seconds = float(os.getenv("HEMS_MAX_SNAPSHOT_AGE_SECONDS", "300"))
        if min(hems_poll_seconds, reconciliation_seconds, max_snapshot_age_seconds) <= 0:
            raise ValueError("Polling, reconciliation, and snapshot-age intervals must be greater than zero")

        return cls(
            base_url=base_url,
            token=token,
            hems_api_base_url=hems_api_base_url,
            building_id=building_id,
            control_mode=control_mode,
            allowlist=allowlist,
            hems_status_metrics_enabled=status_metrics_enabled,
            hems_poll_seconds=hems_poll_seconds,
            reconciliation_seconds=reconciliation_seconds,
            max_snapshot_age_seconds=max_snapshot_age_seconds,
        )

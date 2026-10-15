"""Log changes between successive HEMS configuration snapshots."""

import json
import logging
from datetime import datetime
from typing import Any


logger = logging.getLogger(__name__)
_MISSING_CONFIG_VALUE = object()
_SENSITIVE_CONFIG_PARTS = ("password", "secret", "token", "credential", "api_key", "private_key", "authorization")


def _is_sensitive_config_path(path: tuple[str, ...]) -> bool:
    normalized_path = ".".join(path).casefold()
    return any(part in normalized_path for part in _SENSITIVE_CONFIG_PARTS)


def _append_config_diff(
    previous: Any,
    current: Any,
    path: tuple[str, ...],
    changes: list[dict[str, Any]],
) -> None:
    if previous == current:
        return

    if path and _is_sensitive_config_path(path):
        changes.append(
            {
                "path": ".".join(path),
                "old": "<redacted>",
                "new": "<redacted>",
            }
        )
        return

    if isinstance(previous, dict) and isinstance(current, dict):
        for key in sorted(previous.keys() | current.keys(), key=str):
            _append_config_diff(
                previous.get(key, _MISSING_CONFIG_VALUE),
                current.get(key, _MISSING_CONFIG_VALUE),
                path + (str(key),),
                changes,
            )
        return

    if previous is _MISSING_CONFIG_VALUE and isinstance(current, dict) and current:
        for key in sorted(current, key=str):
            _append_config_diff(_MISSING_CONFIG_VALUE, current[key], path + (str(key),), changes)
        return

    if current is _MISSING_CONFIG_VALUE and isinstance(previous, dict) and previous:
        for key in sorted(previous, key=str):
            _append_config_diff(previous[key], _MISSING_CONFIG_VALUE, path + (str(key),), changes)
        return

    changes.append(
        {
            "path": ".".join(path) or "$",
            "old": "<missing>" if previous is _MISSING_CONFIG_VALUE else previous,
            "new": "<missing>" if current is _MISSING_CONFIG_VALUE else current,
        }
    )


def _log_initial_parameter_setpoints(configurations: dict[str, Any]) -> None:
    for entity_id, configuration in sorted(configurations.items()):
        if not isinstance(configuration, dict):
            continue
        setpoint = configuration.get("setpoint")
        if isinstance(setpoint, dict) and setpoint.get("source") == "parameter" and setpoint.get("value") is not None:
            logger.info(
                "IHD-side HEMS parameter temperature setpoint loaded for %s: %s C (timestamp=%s)",
                entity_id,
                setpoint["value"],
                setpoint.get("timestamp"),
            )


def log_configuration_changes(
    previous_configurations: dict[str, Any] | None,
    current_configurations: dict[str, Any],
    refresh_source: str,
) -> None:
    if previous_configurations is None:
        _log_initial_parameter_setpoints(current_configurations)
        return

    observed_at = datetime.now().astimezone().isoformat()
    for config_id in sorted(previous_configurations.keys() | current_configurations.keys()):
        previous = previous_configurations.get(config_id, _MISSING_CONFIG_VALUE)
        current = current_configurations.get(config_id, _MISSING_CONFIG_VALUE)
        changes: list[dict[str, Any]] = []
        _append_config_diff(previous, current, (), changes)
        if not changes:
            continue

        operation = (
            "added"
            if previous is _MISSING_CONFIG_VALUE
            else "removed"
            if current is _MISSING_CONFIG_VALUE
            else "updated"
        )
        entity_type = "hub" if config_id.startswith("hub.") else "device"
        logger.info(
            "configuration_changed entity_type=%s config_id=%s operation=%s observed_at=%s "
            "refresh_source=%s changes=%s",
            entity_type,
            config_id,
            operation,
            observed_at,
            refresh_source,
            json.dumps(changes, sort_keys=True, separators=(",", ":"), default=str),
        )

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any, Set
from urllib.parse import urlparse, urlunparse

import websocket


logger = logging.getLogger(__name__)


class HomeAssistantEventListener:
    """Listen for Home Assistant state changes and notify a callback.

    The listener treats WebSocket delivery as an optimization, not a source of truth: callers should retain
    periodic REST reconciliation for reconnects, missed events, or integrations that do not report immediately.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        on_state_changed: Callable[[dict[str, Any]], None],
        relevant_entity_ids: Set[str] | None = None,
        relevant_attribute: str | None = None,
        reconnect_seconds: float = 5.0,
    ) -> None:
        self._websocket_url = self._build_websocket_url(base_url)
        self._token = token
        self._on_state_changed = on_state_changed
        self._relevant_entity_ids = None if relevant_entity_ids is None else frozenset(relevant_entity_ids)
        self._relevant_attribute = relevant_attribute
        self._filter_lock = threading.Lock()
        self._reconnect_seconds = reconnect_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _build_websocket_url(base_url: str) -> str:
        parsed = urlparse(base_url.rstrip("/"))
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = f"{parsed.path.rstrip('/')}/api/websocket"
        return urlunparse((scheme, parsed.netloc, path, "", "", ""))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="ha-websocket-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def set_relevant_entity_ids(self, entity_ids: Set[str]) -> None:
        with self._filter_lock:
            self._relevant_entity_ids = frozenset(entity_ids)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._listen_once()
            except Exception:
                logger.exception("Home Assistant WebSocket listener disconnected")

            if not self._stop_event.is_set():
                self._stop_event.wait(self._reconnect_seconds)

    def _listen_once(self) -> None:
        connection = websocket.create_connection(self._websocket_url, timeout=30)
        try:
            auth_required = self._receive_json(connection)
            if auth_required.get("type") != "auth_required":
                raise RuntimeError(f"Unexpected Home Assistant WebSocket message: {auth_required}")

            connection.send(json.dumps({"type": "auth", "access_token": self._token}))
            auth_result = self._receive_json(connection)
            if auth_result.get("type") != "auth_ok":
                raise RuntimeError(f"Home Assistant WebSocket authentication failed: {auth_result}")

            connection.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
            subscription_result = self._receive_json(connection)
            if subscription_result.get("type") != "result" or not subscription_result.get("success"):
                raise RuntimeError(f"Home Assistant event subscription failed: {subscription_result}")

            connection.settimeout(1.0)
            while not self._stop_event.is_set():
                try:
                    message = self._receive_json(connection)
                except websocket.WebSocketTimeoutException:
                    continue
                if self._is_relevant_state_changed(message):
                    self._on_state_changed(message)
        finally:
            connection.close()

    @staticmethod
    def _receive_json(connection: Any) -> dict[str, Any]:
        message = connection.recv()
        if not message:
            raise ConnectionError("Home Assistant WebSocket returned an empty message")
        return json.loads(message)

    def _is_relevant_state_changed(self, message: dict[str, Any]) -> bool:
        if message.get("type") != "event":
            return False

        event = message.get("event", {})
        if event.get("event_type") != "state_changed":
            return False

        entity_id = event.get("data", {}).get("entity_id")
        if not isinstance(entity_id, str):
            return False

        with self._filter_lock:
            relevant_entity_ids = self._relevant_entity_ids
        if relevant_entity_ids is not None and entity_id not in relevant_entity_ids:
            return False

        if relevant_entity_ids is None:
            entity_prefixes = ("climate.",) if self._relevant_attribute is not None else ("climate.", "weather.")
            if not entity_id.startswith(entity_prefixes):
                return False

        if self._relevant_attribute is not None:
            data = event.get("data", {})
            old_attributes = (data.get("old_state") or {}).get("attributes", {})
            new_attributes = (data.get("new_state") or {}).get("attributes", {})
            return old_attributes.get(self._relevant_attribute) != new_attributes.get(self._relevant_attribute)

        if relevant_entity_ids is not None:
            return True

        return True

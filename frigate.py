"""Frigate MQTT integration for Alarm Guardian."""
from __future__ import annotations

import json
import logging

from homeassistant.core import HomeAssistant
from homeassistant.components import mqtt

from .const import (
    MQTT_TOPIC_FRIGATE_EVENTS,
    CONF_FRIGATE_CAMERAS,
)

_LOGGER = logging.getLogger(__name__)


class FrigateListener:
    """Listens to Frigate MQTT events for person detection."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry,
        correlation_engine,
        escalation_manager,
    ) -> None:
        """Initialize Frigate listener."""
        self.hass = hass
        self.config_entry = config_entry
        self.correlation_engine = correlation_engine
        self.escalation_manager = escalation_manager
        
        self._unsubscribe = None
        self._monitored_cameras = config_entry.data.get(CONF_FRIGATE_CAMERAS, [])

    async def async_setup(self) -> None:
        """Set up MQTT subscription for Frigate events."""
        _LOGGER.info(
            "Setting up Frigate MQTT listener for cameras: %s",
            self._monitored_cameras,
        )

        self._unsubscribe = await mqtt.async_subscribe(
            self.hass,
            MQTT_TOPIC_FRIGATE_EVENTS,
            self._handle_frigate_event,
            qos=0,
        )

        _LOGGER.info("Frigate MQTT listener setup complete")

    async def async_unload(self) -> None:
        """Unload MQTT subscription."""
        if self._unsubscribe:
            self._unsubscribe()
            _LOGGER.info("Frigate MQTT listener unloaded")

    async def _handle_frigate_event(self, msg) -> None:
        """Handle incoming Frigate MQTT event."""
        try:
            payload = json.loads(msg.payload)
        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to parse Frigate MQTT payload: %s", err)
            return

        # Only process 'new' events
        if payload.get("type") != "new":
            return

        # Extract event details
        after = payload.get("after", {})
        label = after.get("label")
        camera = after.get("camera")
        event_id = after.get("id")
        score = after.get("score", 0.0)

        # Only process person detection
        if label != "person":
            return

        # Only process configured cameras
        if camera not in self._monitored_cameras:
            _LOGGER.debug(
                "Ignoring person detection from unconfigured camera: %s",
                camera,
            )
            return

        _LOGGER.info(
            "Person detected: camera=%s, score=%.2f, event_id=%s",
            camera,
            score,
            event_id,
        )

        # Store event ID for escalation
        self.escalation_manager.set_frigate_event_id(event_id)

        # Process person detection in correlation engine
        if self.correlation_engine.is_active:
            confirmed = await self.correlation_engine.process_person_detection(
                camera_name=camera,
                event_id=event_id,
                confidence=score,
            )
            
            if confirmed:
                _LOGGER.warning(
                    "Alarm confirmed by person detection! "
                    "(camera: %s, event: %s)",
                    camera,
                    event_id,
                )
        else:
            _LOGGER.debug(
                "Person detected but correlation window not active "
                "(alarm might be disarmed)"
            )

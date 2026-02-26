"""Frigate MQTT integration for Alarm Guardian."""
from __future__ import annotations

import json
import logging

from homeassistant.core import HomeAssistant
from homeassistant.components import mqtt

from .const import (
    MQTT_TOPIC_FRIGATE_EVENTS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class FrigateListener:
    """Listens to Frigate MQTT events for person detection."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry,
        zone_engine,
        escalation_manager,
    ) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.zone_engine = zone_engine
        self.escalation_manager = escalation_manager
        self._unsubscribe = None

    async def async_setup(self) -> None:
        """Set up MQTT subscription for Frigate events."""
        all_cameras = set()
        for zone in self.zone_engine.zones:
            all_cameras.update(zone.frigate_cameras)

        if not all_cameras:
            _LOGGER.info("Nessuna telecamera Frigate configurata nelle zone, skip MQTT")
            return

        self._unsubscribe = await mqtt.async_subscribe(
            self.hass,
            MQTT_TOPIC_FRIGATE_EVENTS,
            self._handle_frigate_event,
            qos=0,
        )
        _LOGGER.info("Frigate MQTT listener attivo per telecamere: %s", all_cameras)

    async def async_unload(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()

    async def _handle_frigate_event(self, msg) -> None:
        try:
            payload = json.loads(msg.payload)
        except json.JSONDecodeError as err:
            _LOGGER.error("Frigate payload non valido: %s", err)
            return

        if payload.get("type") != "new":
            return

        after = payload.get("after", {})
        if after.get("label") != "person":
            return

        camera = after.get("camera")
        event_id = after.get("id")
        score = after.get("score", 0.0)

        # Verifica che la telecamera appartenga a una zona
        zone = self.zone_engine.get_zone_for_camera(camera)
        if not zone:
            _LOGGER.debug("Telecamera %s non appartiene a nessuna zona, ignorata", camera)
            return

        _LOGGER.info("Person detection: camera=%s, zona='%s', score=%.2f", camera, zone.zone_name, score)

        self.escalation_manager.set_frigate_event_id(event_id)

        # Ottieni la modalità corrente dalla state machine
        data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
        state_machine = data.get("state_machine")
        alarm_mode = state_machine.state.value if state_machine else "armed_away"

        if alarm_mode not in ("armed_away", "armed_home", "pre_alarm"):
            _LOGGER.debug("Person detection ignorata: stato %s non attivo", alarm_mode)
            return

        confirmed = await self.zone_engine.process_person_detection(
            camera_name=camera,
            confidence=score,
            alarm_mode=alarm_mode,
        )

        if confirmed:
            _LOGGER.warning("Allarme confermato da person detection (camera: %s)", camera)

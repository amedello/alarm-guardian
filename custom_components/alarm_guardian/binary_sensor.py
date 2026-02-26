"""Binary sensor platform for Alarm Guardian."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Suffissi che identificano le due entità del FP300
FP300_SUFFIXES = ("_pir_detection", "_presence")


def _find_fp300_pairs(interior_sensors: list[str]) -> list[tuple[str, str, str]]:
    """Trova coppie pir_detection + presence con lo stesso base_name.

    Restituisce lista di (base_name, pir_entity_id, presence_entity_id).
    Funziona anche se è presente solo uno dei due (coppia parziale ignorata).
    """
    # Raggruppa per base_name
    by_base: dict[str, dict[str, str]] = {}
    for entity_id in interior_sensors:
        for suffix in FP300_SUFFIXES:
            if entity_id.endswith(suffix):
                base = entity_id[: -len(suffix)]
                by_base.setdefault(base, {})
                by_base[base][suffix] = entity_id
                break

    pairs = []
    for base, found in by_base.items():
        pir = found.get("_pir_detection")
        presence = found.get("_presence")
        if pir and presence:
            pairs.append((base, pir, presence))
        else:
            _LOGGER.debug(
                "FP300 coppia incompleta per base '%s': trovati %s",
                base,
                list(found.keys()),
            )
    return pairs


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alarm Guardian binary sensors."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]

    entities: list[BinarySensorEntity] = [
        AlarmGuardianHealthSensor(coordinator, config_entry),
        AlarmGuardianJammingSensor(coordinator, config_entry),
    ]

    # Crea sensori combinati FP300 per ogni coppia trovata
    interior_sensors = config_entry.data.get("interior_sensors", [])
    # Backward compat
    if not interior_sensors:
        interior_sensors = config_entry.data.get("motion_sensors", [])

    pairs = _find_fp300_pairs(interior_sensors)
    combined_entities = []
    for base_name, pir_id, presence_id in pairs:
        combined = FP300CombinedSensor(hass, config_entry, base_name, pir_id, presence_id)
        entities.append(combined)
        combined_entities.append(combined)
        _LOGGER.info(
            "Creato sensore combinato FP300: %s (da %s + %s)",
            combined.entity_id_combined,
            pir_id,
            presence_id,
        )

    # Salva i sensori combinati in hass.data per farli usare dal listener in __init__
    data["fp300_combined_sensors"] = {c.entity_id_combined: c for c in combined_entities}

    async_add_entities(entities)


class FP300CombinedSensor(BinarySensorEntity):
    """Sensore combinato per Aqara FP300 (pir_detection OR presence).

    Creato automaticamente dall'integrazione per ogni coppia trovata
    tra i sensori interni configurati. Non richiede configurazione manuale.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        base_name: str,
        pir_entity_id: str,
        presence_entity_id: str,
    ) -> None:
        """Initialize combined FP300 sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._base_name = base_name
        self._pir_entity_id = pir_entity_id
        self._presence_entity_id = presence_entity_id

        # entity_id leggibile: binary_sensor.zona_giorno_fp300_combinato
        short = base_name.replace("binary_sensor.", "")
        self._entity_id_combined = f"binary_sensor.{short}_fp300_combinato"
        self.entity_id = self._entity_id_combined

        self._attr_unique_id = f"{config_entry.entry_id}_fp300_{short}"
        self._attr_name = f"{short.replace('_', ' ').title()} FP300"

        self._state = False
        self._pir_state = False
        self._presence_state = False
        self._unsub_listeners: list = []

    @property
    def entity_id_combined(self) -> str:
        """Restituisce l'entity_id del sensore combinato."""
        return self._entity_id_combined

    @property
    def is_on(self) -> bool:
        """True se pir_detection OR presence è attivo."""
        return self._pir_state or self._presence_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Attributi diagnostici."""
        return {
            "pir_detection": self._pir_state,
            "presence": self._presence_state,
            "pir_source": self._pir_entity_id,
            "presence_source": self._presence_entity_id,
            "sensor_type": "radar_fp300",
        }

    @property
    def icon(self) -> str:
        return "mdi:motion-sensor" if self.is_on else "mdi:motion-sensor-off"

    async def async_added_to_hass(self) -> None:
        """Registra listener sulle due entità sorgente."""
        # Leggi stato iniziale
        for entity_id, attr in [
            (self._pir_entity_id, "_pir_state"),
            (self._presence_entity_id, "_presence_state"),
        ]:
            state = self.hass.states.get(entity_id)
            if state:
                setattr(self, attr, state.state == "on")

        # Listener pir_detection
        @callback
        def _pir_changed(event: Event) -> None:
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            self._pir_state = new_state.state == "on"
            self.async_write_ha_state()

        # Listener presence
        @callback
        def _presence_changed(event: Event) -> None:
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            self._presence_state = new_state.state == "on"
            self.async_write_ha_state()

        self._unsub_listeners.append(
            async_track_state_change_event(
                self.hass, self._pir_entity_id, _pir_changed
            )
        )
        self._unsub_listeners.append(
            async_track_state_change_event(
                self.hass, self._presence_entity_id, _presence_changed
            )
        )
        _LOGGER.debug(
            "FP300CombinedSensor '%s' attivo, ascolta %s e %s",
            self._entity_id_combined,
            self._pir_entity_id,
            self._presence_entity_id,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Rimuovi listener al cleanup."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()


class AlarmGuardianHealthSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor indicating system health status."""

    _attr_has_entity_name = True
    _attr_name = "System Health"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, config_entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_health"
        self._last_low_battery_sensors = set()  # Track battery changes for notifications

    @property
    def is_on(self) -> bool:
        """Return true if there's a problem (inverted for PROBLEM device class)."""
        if not self.coordinator.data:
            return False
        
        # PROBLEM device class: ON = problem, OFF = healthy
        return not self.coordinator.data.get("healthy", True)

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        attrs = self.coordinator.get_health_attributes()
        
        # Add detailed low battery sensors info
        if self.coordinator.data:
            low_battery = self.coordinator.data.get("sensors_low_battery", [])
            attrs["low_battery_sensors"] = [
                {
                    "name": s["name"],
                    "battery": s["battery"],
                    "entity_id": s["entity_id"],
                }
                for s in low_battery
            ]
        
        return attrs

    @property
    def icon(self) -> str:
        """Return icon based on health status."""
        if not self.coordinator.data:
            return "mdi:help-circle"
        
        if self.coordinator.data.get("warming_up"):
            return "mdi:restart"
        
        if self.is_on:
            return "mdi:alert-circle"
        else:
            return "mdi:check-circle"
    
    @callback
    def _handle_coordinator_update(self) -> None:
        """Gestisce i dati aggiornati dal coordinator."""
        # Chiama il parent per aggiornare lo stato
        super()._handle_coordinator_update()
        
        # Controlla nuovi sensori con batteria bassa
        if not self.coordinator.data:
            return
        
        current_low_battery = self.coordinator.data.get("sensors_low_battery", [])
        current_ids = {s["entity_id"] for s in current_low_battery}
        
        # Trova NUOVI sensori con batteria bassa (non visti prima)
        new_low_battery = current_ids - self._last_low_battery_sensors
        
        if new_low_battery:
            # Ottieni dettagli per i nuovi sensori
            new_sensors_details = [
                s for s in current_low_battery 
                if s["entity_id"] in new_low_battery
            ]
            
            _LOGGER.warning(
                "Nuovi sensori con batteria bassa rilevati: %s",
                [s["name"] for s in new_sensors_details]
            )
            
            # Invia notifica
            self.hass.async_create_task(
                self._send_low_battery_notification(new_sensors_details)
            )
        
        # Aggiorna set di tracciamento
        self._last_low_battery_sensors = current_ids
    
    async def _send_low_battery_notification(
        self, 
        low_battery_sensors: list[dict]
    ) -> None:
        """Invia notifica batteria bassa tramite escalation manager."""
        try:
            # Ottieni escalation manager da hass.data
            data = self.hass.data[DOMAIN][self._config_entry.entry_id]
            escalation_manager = data.get("escalation_manager")
            
            if not escalation_manager:
                _LOGGER.error(
                    "Escalation manager non trovato, impossibile inviare alert batteria"
                )
                return
            
            # Invia alert tramite escalation manager
            await escalation_manager.send_low_battery_alert(low_battery_sensors)
            
        except Exception as err:
            _LOGGER.error(
                "Errore nell'invio della notifica batteria bassa: %s", 
                err, 
                exc_info=True
            )


class AlarmGuardianJammingSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor indicating RF jamming detection."""

    _attr_has_entity_name = True
    _attr_name = "RF Jamming Detected"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, config_entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_jamming"
        self._last_jamming_state = False  # Track state changes

    @property
    def is_on(self) -> bool:
        """Return true if jamming is detected."""
        if not self.coordinator.data:
            return False
        
        return self.coordinator.data.get("jamming_detected", False)

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}
        
        offline_sensors = self.coordinator.data.get("sensors_offline", [])
        
        return {
            "jamming_reason": self.coordinator.data.get("jamming_reason"),
            "sensors_offline_count": len(offline_sensors),
            "sensors_offline": offline_sensors,
            "sensors_total": self.coordinator.data.get("sensors_total", 0),
        }

    @property
    def icon(self) -> str:
        """Return icon based on jamming status."""
        if self.is_on:
            return "mdi:wifi-alert"
        else:
            return "mdi:wifi-check"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Call parent to update state
        super()._handle_coordinator_update()
        
        # Check if jamming state changed from False to True
        current_jamming = self.is_on
        
        if current_jamming and not self._last_jamming_state:
            # Jamming detected! Send notification
            _LOGGER.warning("Jamming state changed to ON - triggering notification")
            self.hass.async_create_task(self._send_jamming_notification())
        
        # Update last state
        self._last_jamming_state = current_jamming

    async def _send_jamming_notification(self) -> None:
        """Send jamming notification via escalation manager."""
        try:
            # Get escalation manager from hass.data
            data = self.hass.data[DOMAIN][self._config_entry.entry_id]
            escalation_manager = data.get("escalation_manager")
            
            if not escalation_manager:
                _LOGGER.error("Escalation manager not found, cannot send jamming alert")
                return
            
            # Get jamming details
            jamming_reason = self.coordinator.data.get("jamming_reason", "Unknown reason")
            offline_sensors = self.coordinator.data.get("sensors_offline", [])
            
            # Send alert via escalation manager
            await escalation_manager.send_jamming_alert(
                jamming_reason=jamming_reason,
                offline_sensors=offline_sensors,
            )
            
        except Exception as err:
            _LOGGER.error("Failed to send jamming notification: %s", err, exc_info=True)

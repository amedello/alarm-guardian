"""Coordinator for Alarm Guardian."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from .const import (
    DOMAIN,
    HEALTH_CHECK_INTERVAL,
    BOOT_GRACE_PERIOD,
    CONF_BATTERY_THRESHOLD,
    CONF_JAMMING_MIN_DEVICES,
    CONF_JAMMING_MIN_PERCENT,
)

_LOGGER = logging.getLogger(__name__)


def get_battery_level(hass: HomeAssistant, entity_id: str) -> tuple[float | None, str, str | None]:
    """Get battery level with smart dual-check strategy.
    
    Strategy (Priority Order):
    1. Check for separate sensor entity (sensor.*_battery) - PRIORITY
       - Most common for modern Zigbee sensors
       - More reliable and real-time updates
    
    2. Check for attribute in binary_sensor - FALLBACK
       - Older sensors or specific manufacturers
    
    3. Return None if no battery found - POWERED SENSOR
    
    Args:
        hass: HomeAssistant instance
        entity_id: Binary sensor entity_id
    
    Returns:
        tuple: (battery_level, source_type, source_id)
    """
    base_name = (
        entity_id
        .replace("binary_sensor.", "")
        .replace("_occupancy", "")
        .replace("_contact", "")
        .replace("_pir_detection", "")
        .replace("_presence", "")
        .replace("_motion", "")
    )
    
    # PRIORITY 1: Separate sensor entity, se aggiungi ulteriori device con tipologia di batteria diversa, modifica sotto
    entity_suffixes = ['_battery']
    
    for suffix in entity_suffixes:
        battery_entity_id = f"sensor.{base_name}{suffix}"
        battery_state = hass.states.get(battery_entity_id)
        
        if battery_state and battery_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            try:
                battery = float(battery_state.state)
                if 0 <= battery <= 100:
                    _LOGGER.debug(
                        "Battery for %s: %.1f%% (source: entity '%s')",
                        entity_id, battery, battery_entity_id
                    )
                    return (battery, 'entity', battery_entity_id)
            except (ValueError, TypeError):
                continue
    
    # PRIORITY 2: Attribute in binary_sensor
    state = hass.states.get(entity_id)
    if state:
        attribute_names = ['battery', 'battery_level', 'bat', 'battery_percentage']
        
        for attr_name in attribute_names:
            battery_value = state.attributes.get(attr_name)
            
            if battery_value is not None:
                try:
                    battery = float(battery_value)
                    if 0 <= battery <= 100:
                        _LOGGER.debug(
                            "Battery for %s: %.1f%% (source: attribute '%s')",
                            entity_id, battery, attr_name
                        )
                        return (battery, 'attribute', attr_name)
                except (ValueError, TypeError):
                    continue
    
    # PRIORITY 3: No battery found
    _LOGGER.debug("No battery found for %s (powered sensor or not ready)", entity_id)
    return (None, 'none', None)


class AlarmGuardianCoordinator(DataUpdateCoordinator):
    """Coordinator for Alarm Guardian health monitoring."""

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=HEALTH_CHECK_INTERVAL,
        )
        
        self.config_entry = config_entry
        self.boot_time = datetime.now()
        
        # Sensor tracking
        self._sensor_states: dict[str, Any] = {}
        self._sensor_first_seen: dict[str, datetime] = {}
        self._battery_source_cache: dict[str, tuple[str, str]] = {}

    async def async_setup_battery_monitoring(self) -> None:
        """Configura monitoraggio attivo delle entità batteria dei sensori."""
        from homeassistant.helpers.event import async_track_state_change_event
        
        # Raccoglie tutti gli entity_id dei sensori batteria
        battery_entities = []
        
        for sensor_id in self.all_sensors:
            
            base_name = (
                sensor_id
                .replace("binary_sensor.", "")
                .replace("_occupancy", "")
                .replace("_contact", "")
                .replace("_pir_detection", "")
                .replace("_presence", "")
                .replace("_motion", "")
            )
            
            # Controlla tutti i possibili suffissi delle entità batteria
            for suffix in ['_battery']:
                battery_entity_id = f"sensor.{base_name}{suffix}"
                _LOGGER.warning("battery_entity_id: %s", battery_entity_id)
                # Controlla se l'entità esiste nella state machine
                if self.hass.states.get(battery_entity_id) is not None:
                    battery_entities.append(battery_entity_id)
                    _LOGGER.debug(
                        "Registrato monitoraggio batteria per: %s",
                        battery_entity_id
                    )
        
        if battery_entities:
            # Traccia i cambiamenti di stato per tutte le entità batteria
            async def battery_state_changed(event):
                """Gestisce i cambiamenti di stato dei sensori batteria."""
                entity_id = event.data.get("entity_id")
                new_state = event.data.get("new_state")
                old_state = event.data.get("old_state")
                
                if new_state is None:
                    return
                
                # Log dei cambiamenti di stato
                _LOGGER.debug(
                    "Entità batteria %s cambiata: %s -> %s",
                    entity_id,
                    old_state.state if old_state else "None",
                    new_state.state
                )
                
                # Se la batteria è diventata disponibile, forza un refresh
                if old_state and old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    if new_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                        _LOGGER.info(
                            "Entità batteria %s è tornata disponibile, forzatura refresh coordinator",
                            entity_id
                        )
                        # Forza refresh immediato
                        await self.async_request_refresh()
            
            async_track_state_change_event(
                self.hass,
                battery_entities,
                battery_state_changed
            )
            
            _LOGGER.info(
                "Monitoraggio batterie configurato per %d entità",
                len(battery_entities)
            )
        else:
            _LOGGER.warning(
                "Nessuna entità batteria trovata per il monitoraggio attivo!"
            )

    @property
    def is_warming_up(self) -> bool:
        """Check if system is in boot grace period."""
        return (datetime.now() - self.boot_time) < BOOT_GRACE_PERIOD

    @property
    def contact_sensors(self) -> list[str]:
        """Sensori perimetrali da tutte le zone (con fallback legacy)."""
        zones = (
            self.config_entry.options.get("zones")
            or self.config_entry.data.get("zones")
        )
        if zones:
            sensors = []
            for z in zones:
                sensors.extend(z.get("zone_perimeter_sensors", []))
            return sensors
        # Fallback legacy
        sensors = self.config_entry.data.get("perimeter_sensors", [])
        if not sensors:
            sensors = self.config_entry.data.get("contact_sensors", [])
        return sensors

    @property
    def motion_sensors(self) -> list[str]:
        """Sensori interni da tutte le zone (con fallback legacy)."""
        zones = (
            self.config_entry.options.get("zones")
            or self.config_entry.data.get("zones")
        )
        if zones:
            sensors = []
            for z in zones:
                sensors.extend(z.get("zone_interior_sensors", []))
            return sensors
        # Fallback legacy
        sensors = self.config_entry.data.get("interior_sensors", [])
        if not sensors:
            sensors = self.config_entry.data.get("motion_sensors", [])
        return sensors

    @property
    def all_sensors(self) -> list[str]:
        """Tutti i sensori unici (perimetro + interni, senza duplicati)."""
        return list(dict.fromkeys(self.contact_sensors + self.motion_sensors))

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via health check."""
        try:
            health_data = await self._check_health()
            return health_data
        except Exception as err:
            raise UpdateFailed(f"Error checking health: {err}") from err

    async def _check_health(self) -> dict[str, Any]:
        """Check system health."""
        health_data = {
            "healthy": True,
            "warming_up": self.is_warming_up,
            "sensors_total": len(self.all_sensors),
            "sensors_offline": [],
            "sensors_low_battery": [],
            "sensors_powered": [],
            "battery_min": 100,
            "jamming_detected": False,
            "jamming_reason": None,
        }
        
        offline_sensors = []
        low_battery_sensors = []
        powered_sensors = []
        battery_levels = []
        
        battery_threshold = self.config_entry.data.get(CONF_BATTERY_THRESHOLD, 15)
        
        for entity_id in self.all_sensors:
            # ================================================================
            # FIX: Initialize variables to prevent UnboundLocalError
            # These ensure battery/source variables are always defined
            # even if sensor is offline/unavailable
            # ================================================================
            battery = None
            source_type = 'none'
            source_id = None
            
            state = self.hass.states.get(entity_id)
            
            if state is None:
                _LOGGER.warning("Sensor %s not found in state machine", entity_id)
                offline_sensors.append(entity_id)
                continue
            
            # Check if sensor is unavailable
            if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                if self.is_warming_up:
                    # Durante warm-up: salta SOLO il controllo offline, 
                    # ma prova COMUNQUE a leggere la batteria
                    _LOGGER.debug(
                        "Sensor %s unavailable during warm-up - trying battery check anyway",
                        entity_id
                    )
                    # NON fare continue qui! Procede a controllare la batteria
                else:
                    # Fuori dal warm-up: sensor offline, segnala e salta
                    offline_sensors.append(entity_id)
                    _LOGGER.warning("Sensor %s is unavailable", entity_id)
                    continue
            else:
                # Sensor is available - segna come inizializzato
                if entity_id not in self._sensor_first_seen:
                    self._sensor_first_seen[entity_id] = datetime.now()
                    _LOGGER.info("Sensor %s initialized", entity_id)
            
            # Controllo batteria - Smart Dual-Check
            # Eseguito SEMPRE (anche per sensori unavailable durante warm-up)
            # get_battery_level può leggere da sensor.*_battery anche se binary_sensor è unavailable!
            battery, source_type, source_id = get_battery_level(self.hass, entity_id)
            
            # Cache sorgente batteria
            if source_type != 'none':
                self._battery_source_cache[entity_id] = (source_type, source_id)
            
            if battery is not None:
                battery_levels.append(battery)
                _LOGGER.debug(
                    "Batteria aggiunta ai livelli: %s = %.1f%% (sorgente: %s '%s')",
                    entity_id, battery, source_type, source_id
                )
                
                if battery < battery_threshold:
                    low_battery_sensors.append({
                        "entity_id": entity_id,
                        "name": state.attributes.get("friendly_name", entity_id),
                        "battery": battery,
                        "source_type": source_type,
                        "source_id": source_id,
                    })
                    _LOGGER.warning(
                        "BATTERIA BASSA RILEVATA: %s = %.1f%% (soglia: %d%%, sorgente: %s '%s')",
                        entity_id, battery, battery_threshold, source_type, source_id
                    )
            elif source_type == 'none':
                powered_sensors.append(entity_id)
                _LOGGER.debug("Sensore %s senza batteria (alimentato)", entity_id)
            else:
                # battery è None ma source_type non è 'none' - problema!
                _LOGGER.warning(
                    "Batteria None per %s ma source_type è '%s' - controllare sorgente batteria!",
                    entity_id, source_type
                )
            
            # Update sensor state tracking
            # Now safe to use battery/source variables (always defined)
            self._sensor_states[entity_id] = {
                "state": state.state,
                "last_changed": state.last_changed,
                "battery": battery,
                "battery_source_type": source_type,
                "battery_source_id": source_id,
            }
        
        # Update health data
        health_data["sensors_offline"] = offline_sensors
        health_data["sensors_low_battery"] = low_battery_sensors
        health_data["sensors_powered"] = powered_sensors
        
        if battery_levels:
            battery_min = min(battery_levels)
            health_data["battery_min"] = battery_min
            _LOGGER.info(
                "Batteria minima calcolata: %.1f%% (da %d sensori con batteria)",
                battery_min, len(battery_levels)
            )
        else:
            _LOGGER.warning(
                "Nessun livello batteria trovato! battery_min rimarrà al valore predefinito 100%%. "
                "Sensori totali: %d, Offline: %d, Alimentati: %d",
                len(self.all_sensors), len(offline_sensors), len(powered_sensors)
            )
        
        # Check for jamming
        jamming_detected, jamming_reason = self._check_jamming(
            len(self.all_sensors),
            len(offline_sensors),
        )
        
        health_data["jamming_detected"] = jamming_detected
        health_data["jamming_reason"] = jamming_reason
        
        # Overall health status
        if jamming_detected:
            health_data["healthy"] = False
        elif len(offline_sensors) > 0 and not self.is_warming_up:
            health_data["healthy"] = False
        elif len(low_battery_sensors) > 0:
            health_data["healthy"] = True
        
        _LOGGER.debug("Health check result: %s", health_data)
        
        return health_data

    def _check_jamming(
        self,
        total_sensors: int,
        offline_count: int,
    ) -> tuple[bool, str | None]:
        """Check for RF jamming based on offline sensor count."""
        if total_sensors == 0:
            return False, None
        
        min_devices = self.config_entry.data.get(CONF_JAMMING_MIN_DEVICES, 2)
        min_percent = self.config_entry.data.get(CONF_JAMMING_MIN_PERCENT, 50)
        
        offline_percent = (offline_count / total_sensors) * 100
        
        if self.is_warming_up:
            return False, None
        
        if offline_count >= min_devices and offline_percent >= min_percent:
            reason = (
                f"{offline_count}/{total_sensors} sensors offline "
                f"({offline_percent:.1f}% >= {min_percent}%)"
            )
            _LOGGER.warning("Jamming detected: %s", reason)
            return True, reason
        
        return False, None

    def get_sensor_info(self, entity_id: str) -> dict[str, Any] | None:
        """Get cached sensor information."""
        return self._sensor_states.get(entity_id)

    def is_sensor_available(self, entity_id: str) -> bool:
        """Check if sensor is available."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        return state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    def get_health_attributes(self) -> dict[str, Any]:
        """Get health attributes for sensors."""
        if not self.data:
            return {}
        
        return {
            "healthy": self.data.get("healthy", False),
            "warming_up": self.data.get("warming_up", False),
            "sensors_total": self.data.get("sensors_total", 0),
            "sensors_offline": self.data.get("sensors_offline", []),
            "sensors_low_battery_count": len(self.data.get("sensors_low_battery", [])),
            "sensors_powered_count": len(self.data.get("sensors_powered", [])),
            "battery_min": self.data.get("battery_min", 100),
            "jamming_detected": self.data.get("jamming_detected", False),
            "jamming_reason": self.data.get("jamming_reason"),
        }

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
    base_name = entity_id.replace("binary_sensor.", "")
    
    # Remove common Zigbee2MQTT/ZHA suffixes (if present at end of string)
    # This handles: binary_sensor.porta_contact → sensor.porta_battery
    #               binary_sensor.corridoio_occupancy → sensor.corridoio_battery
    zigbee_suffixes = ['_contact', '_occupancy', '_motion', '_opening', '_presence', '_vibration']
    for suffix in zigbee_suffixes:
        if base_name.endswith(suffix):
            base_name = base_name[:-len(suffix)]
            _LOGGER.debug("Removed Zigbee suffix '%s' from %s → %s", suffix, entity_id, base_name)
            break  # Only one suffix per entity
    
    # PRIORITY 1: Separate sensor entity
    entity_suffixes = ['_battery', '_battery_level', '_bat', '_battery_percentage']
    
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

    @property
    def is_warming_up(self) -> bool:
        """Check if system is in boot grace period."""
        return (datetime.now() - self.boot_time) < BOOT_GRACE_PERIOD

    @property
    def contact_sensors(self) -> list[str]:
        """Get list of perimeter sensor entity IDs (with migration)."""
        sensors = self.config_entry.data.get("perimeter_sensors", [])
        if not sensors:
            sensors = self.config_entry.data.get("contact_sensors", [])
        return sensors

    @property
    def motion_sensors(self) -> list[str]:
        """Get list of interior sensor entity IDs (with migration)."""
        sensors = self.config_entry.data.get("interior_sensors", [])
        if not sensors:
            sensors = self.config_entry.data.get("motion_sensors", [])
        return sensors

    @property
    def all_sensors(self) -> list[str]:
        """Get all sensor entity IDs."""
        return self.contact_sensors + self.motion_sensors

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
                    if entity_id not in self._sensor_first_seen:
                        _LOGGER.debug(
                            "Sensor %s unavailable during warm-up",
                            entity_id
                        )
                        continue
                
                offline_sensors.append(entity_id)
                _LOGGER.warning("Sensor %s is unavailable", entity_id)
                # FIX: Add missing continue to prevent accessing undefined variables
                continue
            else:
                # Sensor is available
                if entity_id not in self._sensor_first_seen:
                    self._sensor_first_seen[entity_id] = datetime.now()
                    _LOGGER.info("Sensor %s initialized", entity_id)
                
                # BATTERY CHECK - Smart Dual-Check
                battery, source_type, source_id = get_battery_level(self.hass, entity_id)
                
                # Cache battery source
                if source_type != 'none':
                    self._battery_source_cache[entity_id] = (source_type, source_id)
                
                if battery is not None:
                    battery_levels.append(battery)
                    
                    if battery < battery_threshold:
                        low_battery_sensors.append({
                            "entity_id": entity_id,
                            "name": state.attributes.get("friendly_name", entity_id),
                            "battery": battery,
                            "source_type": source_type,
                            "source_id": source_id,
                        })
                        _LOGGER.warning(
                            "Sensor %s low battery: %.1f%% (source: %s '%s')",
                            entity_id, battery, source_type, source_id
                        )
                elif source_type == 'none':
                    powered_sensors.append(entity_id)
                    _LOGGER.debug("Sensor %s has no battery (powered)", entity_id)
            
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
            health_data["battery_min"] = min(battery_levels)
        
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

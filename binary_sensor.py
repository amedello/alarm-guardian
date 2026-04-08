"""Binary sensor platform for Alarm Guardian."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alarm Guardian binary sensors."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]

    sensors = [
        AlarmGuardianHealthSensor(coordinator, config_entry),
        AlarmGuardianJammingSensor(coordinator, config_entry),
    ]

    async_add_entities(sensors)


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
        return self.coordinator.get_health_attributes()

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

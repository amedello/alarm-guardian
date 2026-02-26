"""Sensor platform for Alarm Guardian."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ATTR_EVENTS_TODAY,
    ATTR_BATTERY_MIN,
    ATTR_CORRELATION_SCORE,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alarm Guardian sensors."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    state_machine = data["state_machine"]
    zone_engine = data["zone_engine"]

    ml_predictor = data.get("ml_predictor")
    adaptive_manager = data.get("adaptive_manager")

    sensors = [
        AlarmGuardianEventsTodaySensor(coordinator, config_entry),
        AlarmGuardianBatteryMinSensor(coordinator, config_entry),
        AlarmGuardianCorrelationScoreSensor(
            coordinator, config_entry, zone_engine
        ),
        AlarmGuardianStateSensor(coordinator, config_entry, state_machine),
    ]
    
    # Add ML sensors if available
    if ml_predictor:
        sensors.append(
            AlarmGuardianMLStatisticsSensor(coordinator, config_entry, ml_predictor)
        )
    
    # Add adaptive window sensor if available
    if adaptive_manager:
        sensors.append(
            AlarmGuardianAdaptiveWindowSensor(coordinator, config_entry, adaptive_manager)
        )

    async_add_entities(sensors)


class AlarmGuardianEventsTodaySensor(CoordinatorEntity, SensorEntity):
    """Sensor showing number of events today."""

    _attr_has_entity_name = True
    _attr_name = "Events Today"
    _attr_icon = "mdi:bell-ring"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, config_entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_events_today"
        self._database = None
        self._cached_value = 0

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        await super().async_added_to_hass()
        # Get database reference
        data = self.hass.data[DOMAIN][self._config_entry.entry_id]
        self._database = data.get("database")
        # Initial update
        await self._async_update_value()

    async def _async_update_value(self):
        """Update cached value from database."""
        if self._database:
            try:
                self._cached_value = await self._database.get_events_today()
            except Exception as err:
                _LOGGER.error("Failed to get events today: %s", err)
                self._cached_value = 0

    async def async_update(self) -> None:
        """Update the sensor."""
        await self._async_update_value()

    @property
    def native_value(self) -> int:
        """Return the state of the sensor."""
        return self._cached_value

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        # TODO: Add last event details from database
        return {
            "last_event": None,
            "last_event_type": None,
            "last_event_sensor": None,
        }


class AlarmGuardianBatteryMinSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing minimum battery level across all sensors."""

    _attr_has_entity_name = True
    _attr_name = "Battery Minimum"
    _attr_icon = "mdi:battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, config_entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_battery_min"

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("battery_min", 100)

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}
        
        low_battery = self.coordinator.data.get("sensors_low_battery", [])
        
        return {
            "low_battery_count": len(low_battery),
            "low_battery_sensors": [
                {
                    "name": sensor["name"],
                    "battery": sensor["battery"],
                }
                for sensor in low_battery
            ],
        }

    @property
    def icon(self) -> str:
        """Return icon based on battery level."""
        if self.native_value is None:
            return "mdi:battery-unknown"
        
        level = self.native_value
        if level <= 10:
            return "mdi:battery-10"
        elif level <= 20:
            return "mdi:battery-20"
        elif level <= 30:
            return "mdi:battery-30"
        elif level <= 40:
            return "mdi:battery-40"
        elif level <= 50:
            return "mdi:battery-50"
        elif level <= 60:
            return "mdi:battery-60"
        elif level <= 70:
            return "mdi:battery-70"
        elif level <= 80:
            return "mdi:battery-80"
        elif level <= 90:
            return "mdi:battery-90"
        else:
            return "mdi:battery"


class AlarmGuardianCorrelationScoreSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing current correlation score."""

    _attr_has_entity_name = True
    _attr_name = "Correlation Score"
    _attr_icon = "mdi:chart-line"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, config_entry, zone_engine):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._zone_engine = zone_engine
        self._attr_unique_id = f"{config_entry.entry_id}_correlation_score"

    @property
    def native_value(self) -> int:
        """Return the state of the sensor."""
        return int(self._zone_engine.global_score)

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        return self._zone_engine.get_attributes()


class AlarmGuardianStateSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing current alarm state."""

    _attr_has_entity_name = True
    _attr_name = "Alarm State"
    _attr_icon = "mdi:shield"

    def __init__(self, coordinator, config_entry, state_machine):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._state_machine = state_machine
        self._attr_unique_id = f"{config_entry.entry_id}_state"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        return self._state_machine.state_name

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        return self._state_machine.get_state_attributes()

    @property
    def icon(self) -> str:
        """Return icon based on state."""
        state = self._state_machine.state_name
        
        icon_map = {
            "disarmed": "mdi:shield-off",
            "arming": "mdi:shield-sync",
            "armed_away": "mdi:shield-lock",
            "armed_home": "mdi:shield-home",
            "pre_alarm": "mdi:shield-alert",
            "alarm_confirmed": "mdi:shield-alert",
            "fault": "mdi:shield-remove",
        }
        
        return icon_map.get(state, "mdi:shield")


class AlarmGuardianMLStatisticsSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing ML statistics and predictions."""

    _attr_has_entity_name = True
    _attr_name = "ML Statistics"
    _attr_icon = "mdi:brain"

    def __init__(self, coordinator, config_entry, ml_predictor):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._ml_predictor = ml_predictor
        self._attr_unique_id = f"{config_entry.entry_id}_ml_statistics"

    @property
    def native_value(self) -> int:
        """Return sensors analyzed count."""
        stats = self._ml_predictor.get_statistics()
        return stats.get("total_sensors_analyzed", 0)

    @property
    def extra_state_attributes(self) -> dict:
        """Return ML statistics."""
        return self._ml_predictor.get_statistics()


class AlarmGuardianAdaptiveWindowSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing current adaptive correlation window."""

    _attr_has_entity_name = True
    _attr_name = "Adaptive Window"
    _attr_icon = "mdi:timer"
    _attr_native_unit_of_measurement = "s"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, config_entry, adaptive_manager):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._adaptive_manager = adaptive_manager
        self._attr_unique_id = f"{config_entry.entry_id}_adaptive_window"

    @property
    def native_value(self) -> int:
        """Return current adaptive window."""
        # Return current time-based window as default
        config = self._adaptive_manager.get_configuration_summary()
        return config.get("current_time_window", 60)

    @property
    def extra_state_attributes(self) -> dict:
        """Return adaptive configuration."""
        return self._adaptive_manager.get_configuration_summary()

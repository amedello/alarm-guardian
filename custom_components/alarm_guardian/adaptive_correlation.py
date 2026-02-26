"""Adaptive correlation window manager."""
from __future__ import annotations

import logging
from datetime import datetime, time

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class AdaptiveCorrelationManager:
    """Manages adaptive correlation window based on context."""

    def __init__(
        self,
        hass: HomeAssistant,
        base_window: int = 60,
    ) -> None:
        """Initialize adaptive manager."""
        self.hass = hass
        self.base_window = base_window
        
        # Time-based windows
        self._time_windows = {
            "night": 30,      # 22:00 - 06:00 (fast response)
            "morning": 45,    # 06:00 - 09:00
            "day": 60,        # 09:00 - 18:00 (normal)
            "evening": 50,    # 18:00 - 22:00
        }
        
        # Sensor type multipliers
        self._sensor_multipliers = {
            "contact": 1.0,   # Contact sensors: normal window
            "radar": 1.1,     # Radar mmWave (FP300): finestra leggermente più lunga del contact
            "motion": 1.5,    # PIR classico: finestra più lunga (più falsi positivi)
            "person": 0.8,    # Person detection Frigate: finestra corta (alta confidenza)
        }
        
        # Zone priority multipliers
        self._zone_multipliers = {
            "perimeter": 0.7,     # Perimeter: fast (critical)
            "interior_ground": 1.0,  # Ground floor: normal
            "interior_upper": 1.2,   # Upper floor: slower (less critical)
        }
        
        # Weather adjustment (placeholder)
        self._weather_enabled = False
        self._wind_speed_threshold = 30  # km/h

    def calculate_adaptive_window(
        self,
        sensor_type: str,
        sensor_zone: str | None = None,
        ml_false_alarm_rate: float | None = None,
    ) -> int:
        """Calculate adaptive correlation window.
        
        Args:
            sensor_type: Type of sensor (contact, motion, person)
            sensor_zone: Zone of sensor (perimeter, interior_ground, etc)
            ml_false_alarm_rate: ML-predicted false alarm rate (0-100)
        
        Returns:
            Adaptive window in seconds
        """
        # Start with time-based window
        base = self._get_time_based_window()
        
        # Apply sensor type multiplier
        sensor_mult = self._sensor_multipliers.get(sensor_type, 1.0)
        window = base * sensor_mult
        
        # Apply zone multiplier if provided
        if sensor_zone:
            zone_mult = self._zone_multipliers.get(sensor_zone, 1.0)
            window *= zone_mult
        
        # Apply ML adjustment
        if ml_false_alarm_rate is not None:
            ml_mult = self._get_ml_multiplier(ml_false_alarm_rate)
            window *= ml_mult
        
        # Apply weather adjustment
        if self._weather_enabled:
            weather_mult = self._get_weather_multiplier()
            window *= weather_mult
        
        # Enforce limits
        final_window = max(10, min(300, int(window)))
        
        _LOGGER.debug(
            "Adaptive window: base=%d, sensor=%s (%.1fx), zone=%s, ml_rate=%.1f%% → %ds",
            base,
            sensor_type,
            sensor_mult,
            sensor_zone,
            ml_false_alarm_rate or 0,
            final_window,
        )
        
        return final_window

    def _get_time_based_window(self) -> int:
        """Get base window based on time of day."""
        current_time = datetime.now().time()
        
        # Define time ranges
        night_start = time(22, 0)
        morning_start = time(6, 0)
        day_start = time(9, 0)
        evening_start = time(18, 0)
        
        if morning_start <= current_time < day_start:
            period = "morning"
        elif day_start <= current_time < evening_start:
            period = "day"
        elif evening_start <= current_time < night_start:
            period = "evening"
        else:
            period = "night"
        
        window = self._time_windows[period]
        
        _LOGGER.debug("Time-based window: period=%s, window=%ds", period, window)
        
        return window

    def _get_ml_multiplier(self, false_alarm_rate: float) -> float:
        """Get multiplier based on ML false alarm rate.
        
        High false alarm rate → longer window (more tolerance)
        Low false alarm rate → shorter window (faster response)
        """
        if false_alarm_rate > 80:
            return 2.0      # Very unreliable: double window
        elif false_alarm_rate > 60:
            return 1.5
        elif false_alarm_rate > 40:
            return 1.2
        elif false_alarm_rate < 10:
            return 0.8      # Very reliable: shorter window
        elif false_alarm_rate < 20:
            return 0.9
        else:
            return 1.0      # Normal

    def _get_weather_multiplier(self) -> float:
        """Get multiplier based on weather conditions.
        
        Placeholder for future weather integration.
        High wind → longer window (doors/windows rattling)
        """
        if not self._weather_enabled:
            return 1.0
        
        # TODO: Integrate with weather.* entities
        # For now, return default
        return 1.0

    def get_recommended_window_for_sensor(
        self,
        sensor_id: str,
        sensor_type: str,
        ml_predictor=None,
    ) -> int:
        """Get recommended window for specific sensor.
        
        Args:
            sensor_id: Entity ID of sensor
            sensor_type: Type (contact/motion/person)
            ml_predictor: Optional ML predictor instance
        
        Returns:
            Recommended window in seconds
        """
        # Determine zone from entity_id (basic heuristic)
        zone = self._detect_zone_from_entity_id(sensor_id)
        
        # Get ML false alarm rate if predictor available
        ml_rate = None
        if ml_predictor:
            reliability = ml_predictor.get_sensor_reliability(sensor_id)
            ml_rate = reliability.get("false_alarm_rate")
        
        return self.calculate_adaptive_window(
            sensor_type=sensor_type,
            sensor_zone=zone,
            ml_false_alarm_rate=ml_rate,
        )

    def _detect_zone_from_entity_id(self, entity_id: str) -> str | None:
        """Detect zone from entity ID (heuristic).
        
        Examples:
        - "porta_ingresso" → perimeter
        - "finestra_*" → perimeter  
        - "motion_*" → interior
        """
        entity_lower = entity_id.lower()
        
        # Perimeter indicators
        if any(keyword in entity_lower for keyword in [
            "porta", "door", "finestra", "window", "ingresso", "entrance"
        ]):
            return "perimeter"
        
        # Interior indicators
        if any(keyword in entity_lower for keyword in [
            "motion", "movimento", "camera", "bagno", "cucina"
        ]):
            # Check if ground floor or upper
            if any(keyword in entity_lower for keyword in [
                "piano", "upper", "superiore", "camera_da_letto"
            ]):
                return "interior_upper"
            else:
                return "interior_ground"
        
        return None

    def get_configuration_summary(self) -> dict:
        """Get summary of adaptive configuration."""
        return {
            "base_window": self.base_window,
            "time_windows": self._time_windows,
            "sensor_multipliers": self._sensor_multipliers,
            "zone_multipliers": self._zone_multipliers,
            "weather_enabled": self._weather_enabled,
            "current_time_window": self._get_time_based_window(),
        }

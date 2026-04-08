"""Machine Learning predictor for false alarm reduction."""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Optional
from collections import defaultdict

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class MLFalseAlarmPredictor:
    """ML-based false alarm prediction and scoring adjustment."""

    def __init__(self, hass: HomeAssistant, database) -> None:
        """Initialize ML predictor."""
        self.hass = hass
        self.database = database
        
        # Historical data
        self._sensor_patterns: dict[str, dict] = defaultdict(lambda: {
            "total_triggers": 0,
            "false_alarms": 0,
            "confirmed_alarms": 0,
            "hourly_distribution": defaultdict(int),
            "false_alarm_hours": defaultdict(int),
        })
        
        # Time-based patterns
        self._hourly_false_alarm_rate: dict[int, float] = {}
        
        # Weather correlation (placeholder for future)
        self._weather_correlation_enabled = False
        
        # Learning enabled
        self._learning_enabled = True
        
        _LOGGER.info("ML Predictor initialized")

    async def async_setup(self) -> None:
        """Set up predictor by loading historical data."""
        _LOGGER.info("Loading historical data for ML training")
        
        # Load last 90 days of events
        events = await self.database.get_recent_events(limit=10000)
        
        # Analyze patterns
        for event in events:
            await self._analyze_event(event)
        
        # Compute statistics
        self._compute_statistics()
        
        _LOGGER.info(
            "ML training complete. Sensors analyzed: %d",
            len(self._sensor_patterns)
        )

    async def _analyze_event(self, event: dict) -> None:
        """Analyze historical event for pattern learning."""
        event_type = event.get("event_type")
        sensor_id = event.get("sensor_id")
        timestamp_str = event.get("timestamp")
        
        if not sensor_id or not timestamp_str:
            return
        
        try:
            timestamp = datetime.fromisoformat(timestamp_str)
        except (ValueError, TypeError):
            return
        
        hour = timestamp.hour
        
        # Update sensor patterns
        pattern = self._sensor_patterns[sensor_id]
        
        if event_type == "trigger":
            pattern["total_triggers"] += 1
            pattern["hourly_distribution"][hour] += 1
        
        elif event_type == "confirm":
            pattern["confirmed_alarms"] += 1
        
        elif event_type == "timeout":
            # Timeout = false alarm
            pattern["false_alarms"] += 1
            pattern["false_alarm_hours"][hour] += 1

    def _compute_statistics(self) -> None:
        """Compute statistical patterns from historical data."""
        # Compute hourly false alarm rates
        hourly_total = defaultdict(int)
        hourly_false = defaultdict(int)
        
        for sensor_id, pattern in self._sensor_patterns.items():
            for hour, count in pattern["hourly_distribution"].items():
                hourly_total[hour] += count
            
            for hour, count in pattern["false_alarm_hours"].items():
                hourly_false[hour] += count
        
        # Calculate rates
        for hour in range(24):
            total = hourly_total.get(hour, 0)
            false = hourly_false.get(hour, 0)
            
            if total > 0:
                self._hourly_false_alarm_rate[hour] = (false / total) * 100
            else:
                self._hourly_false_alarm_rate[hour] = 0.0
        
        _LOGGER.debug("Hourly false alarm rates computed: %s", self._hourly_false_alarm_rate)

    async def predict_score_adjustment(
        self,
        sensor_id: str,
        sensor_type: str,
        base_score: int,
    ) -> int:
        """Predict score adjustment based on ML analysis.
        
        Returns adjusted score (can be negative for penalty).
        """
        if not self._learning_enabled:
            return base_score
        
        adjustment = 0
        current_hour = datetime.now().hour
        
        # 1. Sensor-specific pattern adjustment
        if sensor_id in self._sensor_patterns:
            pattern = self._sensor_patterns[sensor_id]
            
            total = pattern["total_triggers"]
            false = pattern["false_alarms"]
            
            if total >= 10:  # Need minimum data
                false_rate = (false / total) * 100
                
                # High false alarm rate → penalty
                if false_rate > 80:
                    adjustment -= 30
                    _LOGGER.debug(
                        "Sensor %s has high false alarm rate (%.1f%%), penalty: -30",
                        sensor_id,
                        false_rate,
                    )
                elif false_rate > 60:
                    adjustment -= 20
                elif false_rate > 40:
                    adjustment -= 10
                
                # Low false alarm rate → bonus
                elif false_rate < 10:
                    adjustment += 10
                    _LOGGER.debug(
                        "Sensor %s has low false alarm rate (%.1f%%), bonus: +10",
                        sensor_id,
                        false_rate,
                    )
        
        # 2. Time-based adjustment
        hour_false_rate = self._hourly_false_alarm_rate.get(current_hour, 0)
        
        if hour_false_rate > 70:
            adjustment -= 20
            _LOGGER.debug(
                "High false alarm rate at hour %d (%.1f%%), penalty: -20",
                current_hour,
                hour_false_rate,
            )
        elif hour_false_rate > 50:
            adjustment -= 10
        elif hour_false_rate < 10:
            adjustment += 5
        
        # 3. Sensor type specific
        # Motion sensors are more prone to false alarms
        if sensor_type == "motion":
            # Check if this specific motion sensor is reliable
            if sensor_id in self._sensor_patterns:
                pattern = self._sensor_patterns[sensor_id]
                if pattern["total_triggers"] >= 5:
                    motion_false_rate = (
                        pattern["false_alarms"] / pattern["total_triggers"]
                    ) * 100
                    
                    if motion_false_rate > 90:
                        adjustment -= 15
                        _LOGGER.debug(
                            "Motion sensor %s very unreliable, penalty: -15",
                            sensor_id,
                        )
        
        adjusted_score = base_score + adjustment
        
        if adjustment != 0:
            _LOGGER.info(
                "ML score adjustment: %s %d → %d (adj: %+d)",
                sensor_id,
                base_score,
                adjusted_score,
                adjustment,
            )
        
        return max(0, adjusted_score)  # Don't go negative

    async def learn_from_outcome(
        self,
        sensor_id: str,
        was_false_alarm: bool,
    ) -> None:
        """Learn from alarm outcome for continuous improvement."""
        if not self._learning_enabled:
            return
        
        pattern = self._sensor_patterns[sensor_id]
        current_hour = datetime.now().hour
        
        if was_false_alarm:
            pattern["false_alarms"] += 1
            pattern["false_alarm_hours"][current_hour] += 1
            _LOGGER.debug("Learned false alarm from sensor %s at hour %d", sensor_id, current_hour)
        else:
            pattern["confirmed_alarms"] += 1
            _LOGGER.debug("Learned confirmed alarm from sensor %s", sensor_id)
        
        # Recompute statistics
        self._compute_statistics()

    def get_sensor_reliability(self, sensor_id: str) -> dict:
        """Get reliability metrics for a sensor."""
        if sensor_id not in self._sensor_patterns:
            return {
                "reliability": "unknown",
                "total_triggers": 0,
                "false_alarm_rate": 0.0,
            }
        
        pattern = self._sensor_patterns[sensor_id]
        total = pattern["total_triggers"]
        false = pattern["false_alarms"]
        
        false_rate = (false / total * 100) if total > 0 else 0.0
        
        if total < 5:
            reliability = "insufficient_data"
        elif false_rate < 10:
            reliability = "excellent"
        elif false_rate < 30:
            reliability = "good"
        elif false_rate < 50:
            reliability = "fair"
        elif false_rate < 70:
            reliability = "poor"
        else:
            reliability = "unreliable"
        
        return {
            "reliability": reliability,
            "total_triggers": total,
            "false_alarms": false,
            "confirmed_alarms": pattern["confirmed_alarms"],
            "false_alarm_rate": round(false_rate, 1),
        }

    def get_hourly_risk_assessment(self) -> dict[int, str]:
        """Get risk assessment for each hour of day."""
        risk_by_hour = {}
        
        for hour in range(24):
            rate = self._hourly_false_alarm_rate.get(hour, 0)
            
            if rate < 20:
                risk = "low"
            elif rate < 40:
                risk = "medium"
            elif rate < 60:
                risk = "high"
            else:
                risk = "very_high"
            
            risk_by_hour[hour] = risk
        
        return risk_by_hour

    async def reset(self) -> None:
        """Reset all learned data."""
        _LOGGER.warning("Resetting ML predictor data")
        self._sensor_patterns.clear()
        self._hourly_false_alarm_rate.clear()
        await self.async_setup()

    def get_statistics(self) -> dict:
        """Get ML statistics summary."""
        total_sensors = len(self._sensor_patterns)
        
        excellent = sum(
            1 for s in self._sensor_patterns.values()
            if s["total_triggers"] >= 5
            and (s["false_alarms"] / s["total_triggers"]) < 0.1
        )
        
        poor = sum(
            1 for s in self._sensor_patterns.values()
            if s["total_triggers"] >= 5
            and (s["false_alarms"] / s["total_triggers"]) > 0.7
        )
        
        return {
            "total_sensors_analyzed": total_sensors,
            "excellent_sensors": excellent,
            "poor_sensors": poor,
            "learning_enabled": self._learning_enabled,
            "hourly_false_alarm_rates": dict(self._hourly_false_alarm_rate),
        }

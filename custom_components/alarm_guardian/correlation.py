"""Correlation Engine for Alarm Guardian."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from .const import (
    SCORE_CONTACT_SENSOR,
    SCORE_RADAR_SENSOR,
    SCORE_MOTION_SENSOR,
    SCORE_PERSON_DETECTION,
    SCORE_THRESHOLD_CONFIRM,
    VOLUMETRIC_SENSOR_TYPES,
)

_LOGGER = logging.getLogger(__name__)


class TriggerEvent:
    """Represents a single trigger event."""

    def __init__(
        self,
        entity_id: str,
        entity_name: str,
        sensor_type: str,
        timestamp: datetime,
        score: int,
    ) -> None:
        """Initialize trigger event."""
        self.entity_id = entity_id
        self.entity_name = entity_name
        self.sensor_type = sensor_type  # 'contact', 'motion', 'person'
        self.timestamp = timestamp
        self.score = score

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"TriggerEvent({self.sensor_type}, {self.entity_name}, "
            f"score={self.score}, time={self.timestamp})"
        )


class CorrelationEngine:
    """Correlation engine for multi-trigger alarm confirmation."""

    def __init__(
        self,
        hass: HomeAssistant,
        correlation_window: int = 60,
    ) -> None:
        """Initialize correlation engine."""
        self.hass = hass
        self.correlation_window = correlation_window  # seconds
        
        # Event tracking
        self._events: list[TriggerEvent] = []
        self._total_score = 0
        self._correlation_started_at: Optional[datetime] = None
        self._correlation_timer_handle = None

    @property
    def is_active(self) -> bool:
        """Check if correlation window is active."""
        return self._correlation_started_at is not None

    @property
    def total_score(self) -> int:
        """Get current total correlation score."""
        return self._total_score

    @property
    def events(self) -> list[TriggerEvent]:
        """Get list of events in current correlation window."""
        return self._events.copy()

    @property
    def time_remaining(self) -> Optional[timedelta]:
        """Get time remaining in correlation window."""
        if not self.is_active:
            return None
        
        elapsed = datetime.now() - self._correlation_started_at
        remaining = timedelta(seconds=self.correlation_window) - elapsed
        return remaining if remaining.total_seconds() > 0 else timedelta(0)

    def start_correlation(self, callback_timeout, callback_confirm) -> None:
        """Start correlation window."""
        self._correlation_started_at = datetime.now()
        self._timeout_callback = callback_timeout
        self._confirm_callback = callback_confirm
        
        _LOGGER.info(
            "Correlation window started (%d seconds)",
            self.correlation_window,
        )
        
        # Schedule timeout
        self._correlation_timer_handle = async_call_later(
            self.hass,
            self.correlation_window,
            lambda _: self.hass.async_create_task(self._handle_timeout()),
        )

    def reset_correlation(self) -> None:
        """Reset correlation window."""
        if self._correlation_timer_handle:
            self._correlation_timer_handle.cancel()
            self._correlation_timer_handle = None
        
        self._events.clear()
        self._total_score = 0
        self._correlation_started_at = None
        
        _LOGGER.info("Correlation window reset")

    def extend_correlation(self) -> None:
        """Extend correlation window (reset timer)."""
        if not self.is_active:
            _LOGGER.warning("Cannot extend inactive correlation window")
            return
        
        # Cancel existing timer
        if self._correlation_timer_handle:
            self._correlation_timer_handle.cancel()
        
        # Reset start time
        self._correlation_started_at = datetime.now()
        
        # Schedule new timeout
        self._correlation_timer_handle = async_call_later(
            self.hass,
            self.correlation_window,
            lambda _: self.hass.async_create_task(self._handle_timeout()),
        )
        
        _LOGGER.info("Correlation window extended (reset to %d seconds)", self.correlation_window)

    async def process_contact_trigger(
        self,
        entity_id: str,
        entity_name: str,
    ) -> bool:
        """Process contact sensor trigger (score fisso da const)."""
        event = TriggerEvent(
            entity_id=entity_id,
            entity_name=entity_name,
            sensor_type="contact",
            timestamp=datetime.now(),
            score=SCORE_CONTACT_SENSOR,
        )
        return await self._add_event(event)

    async def process_contact_trigger_with_score(
        self,
        entity_id: str,
        entity_name: str,
        score: int,
    ) -> bool:
        """Process contact sensor trigger con score ML pre-calcolato."""
        event = TriggerEvent(
            entity_id=entity_id,
            entity_name=entity_name,
            sensor_type="contact",
            timestamp=datetime.now(),
            score=score,
        )
        return await self._add_event(event)

    async def process_motion_trigger(
        self,
        entity_id: str,
        entity_name: str,
    ) -> bool:
        """Process motion sensor trigger (score fisso da const)."""
        event = TriggerEvent(
            entity_id=entity_id,
            entity_name=entity_name,
            sensor_type="motion",
            timestamp=datetime.now(),
            score=SCORE_MOTION_SENSOR,
        )
        return await self._add_event(event)

    async def process_motion_trigger_with_score(
        self,
        entity_id: str,
        entity_name: str,
        score: int,
    ) -> bool:
        """Process motion sensor trigger con score ML pre-calcolato."""
        event = TriggerEvent(
            entity_id=entity_id,
            entity_name=entity_name,
            sensor_type="motion",
            timestamp=datetime.now(),
            score=score,
        )
        return await self._add_event(event)

    async def process_person_detection(
        self,
        camera_name: str,
        event_id: str,
        confidence: float,
    ) -> bool:
        """Process Frigate person detection.
        
        Returns True if alarm should be confirmed.
        """
        event = TriggerEvent(
            entity_id=f"frigate_{camera_name}",
            entity_name=f"Camera {camera_name} (person {int(confidence*100)}%)",
            sensor_type="person",
            timestamp=datetime.now(),
            score=SCORE_PERSON_DETECTION,
        )
        
        # Person detection extends correlation window
        if self.is_active:
            self.extend_correlation()
        
        return await self._add_event(event)

    async def _add_event(self, event: TriggerEvent) -> bool:
        """Add event to correlation window.
        
        Regole di conferma:
        1. Score totale >= SCORE_THRESHOLD_CONFIRM (100)
        2. AND: se tutti gli eventi sono volumetrici (motion/radar/person),
                devono essere di tipi DIVERSI tra loro.
                → Un cane che attraversa 2 stanze radar NON conferma.
                → Un PIR + un radar (tipi diversi) SÌ conferma.
                → Un contact + qualsiasi cosa SÌ conferma (contact non è volumetrico).
        """
        self._events.append(event)
        self._total_score += event.score

        _LOGGER.info(
            "Added %s event: %s (score: %d, total: %d/%d)",
            event.sensor_type,
            event.entity_name,
            event.score,
            self._total_score,
            SCORE_THRESHOLD_CONFIRM,
        )

        # Check score threshold
        if self._total_score < SCORE_THRESHOLD_CONFIRM:
            return False

        # Score raggiunto: verifica diversità tipi
        types_in_window = [e.sensor_type for e in self._events]
        unique_types = set(types_in_window)

        # Se c'è almeno un tipo NON volumetrico (es. "contact") → conferma diretta
        non_volumetric = unique_types - VOLUMETRIC_SENSOR_TYPES
        if non_volumetric:
            _LOGGER.warning(
                "Correlation confirmed: score=%d, types=%s (non-volumetric: %s)",
                self._total_score, unique_types, non_volumetric,
            )
        else:
            # Tutti volumetrici: devono essere di tipi DIVERSI
            if len(unique_types) < 2:
                same_type = next(iter(unique_types), "unknown")
                _LOGGER.info(
                    "Score %d raggiunto ma tutti %s dello stesso tipo '%s' → no conferma (cane?)",
                    self._total_score, len(types_in_window), same_type,
                )
                return False
            _LOGGER.warning(
                "Correlation confirmed: score=%d, tipi volumetrici diversi=%s",
                self._total_score, unique_types,
            )

        # Conferma allarme
        if self._correlation_timer_handle:
            self._correlation_timer_handle.cancel()
            self._correlation_timer_handle = None

        if self._confirm_callback:
            await self._confirm_callback()

        return True

    async def _handle_timeout(self) -> None:
        """Handle correlation window timeout."""
        _LOGGER.info(
            "Correlation window timeout (score: %d/%d, events: %d)",
            self._total_score,
            SCORE_THRESHOLD_CONFIRM,
            len(self._events),
        )
        
        # Call timeout callback
        if self._timeout_callback:
            await self._timeout_callback()
        
        # Reset correlation
        self.reset_correlation()

    def get_correlation_attributes(self) -> dict:
        """Get correlation attributes for sensors."""
        return {
            "is_active": self.is_active,
            "total_score": self._total_score,
            "score_threshold": SCORE_THRESHOLD_CONFIRM,
            "window_seconds": self.correlation_window,
            "time_remaining_seconds": (
                int(self.time_remaining.total_seconds())
                if self.time_remaining
                else None
            ),
            "events_count": len(self._events),
            "events": [
                {
                    "type": e.sensor_type,
                    "name": e.entity_name,
                    "score": e.score,
                    "time": e.timestamp.isoformat(),
                }
                for e in self._events
            ],
        }

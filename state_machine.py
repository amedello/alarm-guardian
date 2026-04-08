"""State Machine for Alarm Guardian."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from homeassistant.core import HomeAssistant

from .const import (
    STATE_ALARM_DISARMED,
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMED_HOME,
    STATE_ALARM_ARMING,
    STATE_PRE_ALARM,
    STATE_ALARM_CONFIRMED,
    STATE_FAULT,
    EVENT_TYPE_ARM,
    EVENT_TYPE_DISARM,
    EVENT_TYPE_TRIGGER,
    EVENT_TYPE_CONFIRM,
    EVENT_TYPE_FAULT,
    EVENT_TYPE_RESET,
    EVENT_TYPE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class AlarmState(Enum):
    """Alarm system states."""
    DISARMED = STATE_ALARM_DISARMED
    ARMING = STATE_ALARM_ARMING
    ARMED_AWAY = STATE_ALARM_ARMED_AWAY
    ARMED_HOME = STATE_ALARM_ARMED_HOME
    PRE_ALARM = STATE_PRE_ALARM
    ALARM_CONFIRMED = STATE_ALARM_CONFIRMED
    FAULT = STATE_FAULT


class AlarmStateMachine:
    """Alarm Guardian State Machine."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize state machine."""
        self.hass = hass
        self._state = AlarmState.DISARMED
        self._previous_state: Optional[AlarmState] = None
        self._state_changed_at: datetime = datetime.now()
        self._transition_callbacks: list = []
        
        # Pre-alarm tracking
        self._pre_alarm_started_at: Optional[datetime] = None
        self._first_trigger_sensor: Optional[str] = None
        self._first_trigger_name: Optional[str] = None
        
        # Fault tracking
        self._fault_reason: Optional[str] = None

    @property
    def state(self) -> AlarmState:
        """Get current state."""
        return self._state

    @property
    def state_name(self) -> str:
        """Get current state name."""
        return self._state.value

    @property
    def previous_state(self) -> Optional[AlarmState]:
        """Get previous state."""
        return self._previous_state

    @property
    def is_armed(self) -> bool:
        """Check if alarm is armed."""
        return self._state in (
            AlarmState.ARMED_AWAY,
            AlarmState.ARMED_HOME,
            AlarmState.PRE_ALARM,
            AlarmState.ALARM_CONFIRMED,
        )

    @property
    def is_triggered(self) -> bool:
        """Check if alarm is triggered."""
        return self._state in (AlarmState.PRE_ALARM, AlarmState.ALARM_CONFIRMED)

    @property
    def time_in_state(self) -> timedelta:
        """Get time spent in current state."""
        return datetime.now() - self._state_changed_at

    @property
    def first_trigger_sensor(self) -> Optional[str]:
        """Get first trigger sensor entity_id."""
        return self._first_trigger_sensor

    @property
    def first_trigger_name(self) -> Optional[str]:
        """Get first trigger sensor friendly name."""
        return self._first_trigger_name

    @property
    def fault_reason(self) -> Optional[str]:
        """Get fault reason."""
        return self._fault_reason

    def register_transition_callback(self, callback) -> None:
        """Register callback for state transitions."""
        self._transition_callbacks.append(callback)

    async def _transition(
        self,
        new_state: AlarmState,
        event_type: str,
        trigger_sensor: Optional[str] = None,
        trigger_name: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Execute state transition."""
        if self._state == new_state:
            _LOGGER.debug("Already in state %s, ignoring transition", new_state.value)
            return

        old_state = self._state
        self._previous_state = old_state
        self._state = new_state
        self._state_changed_at = datetime.now()

        _LOGGER.info(
            "State transition: %s -> %s (event: %s)",
            old_state.value,
            new_state.value,
            event_type,
        )

        # Update tracking variables based on new state
        if new_state == AlarmState.PRE_ALARM:
            self._pre_alarm_started_at = datetime.now()
            self._first_trigger_sensor = trigger_sensor
            self._first_trigger_name = trigger_name
        elif new_state == AlarmState.DISARMED:
            self._pre_alarm_started_at = None
            # Reset trigger info to avoid carrying dirty state into next session
            self._first_trigger_sensor = None
            self._first_trigger_name = None
        elif new_state == AlarmState.FAULT:
            self._fault_reason = reason

        # Notify callbacks
        for callback in self._transition_callbacks:
            try:
                await callback(old_state, new_state, event_type, trigger_sensor)
            except Exception as err:
                _LOGGER.error("Error in transition callback: %s", err)

    async def arm_away(self) -> bool:
        """Arm system in away mode."""
        if self._state == AlarmState.DISARMED:
            await self._transition(AlarmState.ARMED_AWAY, EVENT_TYPE_ARM)
            return True
        return False

    async def arm_home(self) -> bool:
        """Arm system in home mode."""
        if self._state == AlarmState.DISARMED:
            await self._transition(AlarmState.ARMED_HOME, EVENT_TYPE_ARM)
            return True
        return False

    async def disarm(self) -> bool:
        """Disarm system."""
        if self._state != AlarmState.DISARMED:
            await self._transition(AlarmState.DISARMED, EVENT_TYPE_DISARM)
            return True
        return False

    async def trigger_pre_alarm(
        self,
        sensor_entity_id: str,
        sensor_name: str,
    ) -> bool:
        """Trigger pre-alarm state."""
        if self._state in (AlarmState.ARMED_AWAY, AlarmState.ARMED_HOME):
            await self._transition(
                AlarmState.PRE_ALARM,
                EVENT_TYPE_TRIGGER,
                trigger_sensor=sensor_entity_id,
                trigger_name=sensor_name,
            )
            return True
        return False

    async def confirm_alarm(self) -> bool:
        """Confirm alarm (second trigger)."""
        if self._state == AlarmState.PRE_ALARM:
            await self._transition(AlarmState.ALARM_CONFIRMED, EVENT_TYPE_CONFIRM)
            return True
        return False

    async def reset_pre_alarm(self) -> bool:
        """Reset pre-alarm (timeout without confirmation)."""
        if self._state == AlarmState.PRE_ALARM:
            # Return to previous armed state
            if self._previous_state == AlarmState.ARMED_HOME:
                target_state = AlarmState.ARMED_HOME
            else:
                target_state = AlarmState.ARMED_AWAY
            
            await self._transition(target_state, EVENT_TYPE_TIMEOUT)
            return True
        return False

    async def set_fault(self, reason: str) -> bool:
        """Set system to fault state."""
        if self._state != AlarmState.FAULT:
            await self._transition(
                AlarmState.FAULT,
                EVENT_TYPE_FAULT,
                reason=reason,
            )
            return True
        return False

    async def clear_fault(self) -> bool:
        """Clear fault state."""
        if self._state == AlarmState.FAULT:
            self._fault_reason = None
            await self._transition(AlarmState.DISARMED, EVENT_TYPE_RESET)
            return True
        return False

    async def sync_with_alarm_panel(self, panel_state: str) -> None:
        """Sync state machine with alarm panel state."""
        _LOGGER.debug("Syncing with alarm panel state: %s", panel_state)
        
        # Map panel states to internal states
        state_map = {
            "disarmed": AlarmState.DISARMED,
            "armed_away": AlarmState.ARMED_AWAY,
            "armed_home": AlarmState.ARMED_HOME,
        }
        
        target_state = state_map.get(panel_state)
        
        if target_state is None:
            _LOGGER.warning("Unknown alarm panel state: %s", panel_state)
            return
        
        # Only sync if we're not in a triggered state
        # (we don't want the panel to override our alarm logic)
        if not self.is_triggered and self._state != target_state:
            event_type = EVENT_TYPE_ARM if target_state != AlarmState.DISARMED else EVENT_TYPE_DISARM
            await self._transition(target_state, event_type)

    # sync_with_tuya removed — use sync_with_alarm_panel directly

    def get_state_attributes(self) -> dict:
        """Get state attributes for sensors."""
        return {
            "state": self.state_name,
            "previous_state": self._previous_state.value if self._previous_state else None,
            "time_in_state_seconds": int(self.time_in_state.total_seconds()),
            "is_armed": self.is_armed,
            "is_triggered": self.is_triggered,
            "first_trigger_sensor": self._first_trigger_sensor,
            "first_trigger_name": self._first_trigger_name,
            "pre_alarm_started_at": (
                self._pre_alarm_started_at.isoformat()
                if self._pre_alarm_started_at
                else None
            ),
            "fault_reason": self._fault_reason,
        }

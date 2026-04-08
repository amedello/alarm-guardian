"""The Alarm Guardian integration."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, EVENT_TYPE_ARM, EVENT_TYPE_DISARM, EVENT_TYPE_TRIGGER, EVENT_TYPE_CONFIRM, CONF_ALARM_PANEL_ENTITY
from .coordinator import AlarmGuardianCoordinator
from .state_machine import AlarmStateMachine
from .correlation import CorrelationEngine
from .escalation import EscalationManager
from .frigate import FrigateListener
from .database import AlarmDatabase
from .ml_predictor import MLFalseAlarmPredictor
from .adaptive_correlation import AdaptiveCorrelationManager
from . import services as alarm_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alarm Guardian from a config entry."""
    _LOGGER.info("Setting up Alarm Guardian integration")

    # Initialize database
    database = AlarmDatabase(hass, entry.entry_id)
    await database.async_setup()

    # Initialize coordinator (health monitor)
    coordinator = AlarmGuardianCoordinator(hass, entry)
    
    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()
    
    if not coordinator.last_update_success:
        raise ConfigEntryNotReady("Failed to fetch initial sensor data")

    # Initialize state machine
    state_machine = AlarmStateMachine(hass)
    
    # Initialize correlation engine
    correlation_window = entry.data.get("correlation_window", 60)
    correlation_engine = CorrelationEngine(hass, correlation_window)

    # Initialize ML predictor
    ml_predictor = MLFalseAlarmPredictor(hass, database)
    await ml_predictor.async_setup()

    # Initialize adaptive correlation manager
    adaptive_manager = AdaptiveCorrelationManager(hass, correlation_window)

    # Initialize escalation manager
    escalation_manager = EscalationManager(hass, entry)

    # Initialize Frigate listener
    frigate_listener = FrigateListener(
        hass,
        entry,
        correlation_engine,
        escalation_manager,
    )
    await frigate_listener.async_setup()

    # Store instances in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "state_machine": state_machine,
        "correlation_engine": correlation_engine,
        "escalation_manager": escalation_manager,
        "frigate_listener": frigate_listener,
        "database": database,
        "ml_predictor": ml_predictor,
        "adaptive_manager": adaptive_manager,
        "config_entry": entry,
    }

    # Register state machine transition callback for database logging
    state_machine.register_transition_callback(
        lambda old, new, event_type, sensor: database_log_transition(
            database, old, new, event_type, sensor
        )
    )

    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await alarm_services.async_setup_services(hass)

    # Start monitoring alarm panel state
    await async_setup_alarm_panel_listener(
        hass, entry, state_machine,
        correlation_engine=correlation_engine,
        escalation_manager=escalation_manager,
    )

    # Start monitoring sensors for triggers
    await async_setup_sensor_listeners(
        hass,
        entry,
        state_machine,
        correlation_engine,
        escalation_manager,
        database,
    )

    _LOGGER.info("Alarm Guardian integration setup complete")

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Alarm Guardian integration")

    # Get data
    data = hass.data[DOMAIN][entry.entry_id]
    
    # Cleanup Frigate listener
    if "frigate_listener" in data:
        await data["frigate_listener"].async_unload()
    
    # Close database
    if "database" in data:
        await data["database"].async_close()

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_setup_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up services for Alarm Guardian."""
    # TODO: Implement custom services (test_escalation, export_events, etc.)
    _LOGGER.debug("Services setup (placeholder)")


async def async_setup_alarm_panel_listener(
    hass: HomeAssistant,
    entry: ConfigEntry,
    state_machine: AlarmStateMachine,
    correlation_engine: "CorrelationEngine | None" = None,
    escalation_manager: "EscalationManager | None" = None,
) -> None:
    """Set up listener for alarm panel state changes."""
    alarm_panel_entity = entry.data.get(CONF_ALARM_PANEL_ENTITY)
    
    if not alarm_panel_entity:
        _LOGGER.warning("No alarm panel entity configured")
        return

    async def alarm_panel_state_changed(event):
        """Handle alarm panel state changes."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        
        panel_state = new_state.state
        _LOGGER.debug("Alarm panel state changed to: %s", panel_state)
        
        # On disarm: reset session state to avoid dirty carry-over
        if panel_state == "disarmed":
            if correlation_engine is not None:
                correlation_engine.reset_correlation()
                _LOGGER.info("Correlation engine reset on disarm")
            if escalation_manager is not None:
                escalation_manager.reset()
                _LOGGER.info("Escalation manager reset on disarm")
        
        # Sync state machine with alarm panel
        await state_machine.sync_with_alarm_panel(panel_state)
        
        # Handle arming/disarming Frigate detection
        await handle_frigate_detection(hass, entry, panel_state)

    # Use async_track_state_change_event (HA 2026 compatible)
    from homeassistant.helpers.event import async_track_state_change_event
    
    async_track_state_change_event(
        hass,
        [alarm_panel_entity],
        alarm_panel_state_changed
    )
    
    _LOGGER.info("Alarm panel listener registered for %s", alarm_panel_entity)


async def handle_frigate_detection(
    hass: HomeAssistant,
    entry: ConfigEntry,
    panel_state: str,
) -> None:
    """Enable/disable Frigate detection based on alarm state."""
    motion_switches = entry.data.get("frigate_motion_switches", [])
    detect_switches = entry.data.get("frigate_detect_switches", [])
    
    all_switches = motion_switches + detect_switches
    
    if not all_switches:
        _LOGGER.debug("No Frigate switches configured, skipping")
        return
    
    if panel_state == "disarmed":
        # Disable detection and motion
        for switch in all_switches:
            try:
                await hass.services.async_call(
                    "switch",
                    "turn_off",
                    {"entity_id": switch},
                    blocking=False,
                )
            except Exception as err:
                _LOGGER.warning("Failed to turn off switch %s: %s", switch, err)
        
        _LOGGER.info("Frigate detection disabled (alarm disarmed)")
    
    elif panel_state in ("armed_away", "armed_home"):
        # Enable detection and motion
        for switch in all_switches:
            try:
                await hass.services.async_call(
                    "switch",
                    "turn_on",
                    {"entity_id": switch},
                    blocking=False,
                )
            except Exception as err:
                _LOGGER.warning("Failed to turn on switch %s: %s", switch, err)
        
        _LOGGER.info("Frigate detection enabled (alarm armed)")


async def database_log_transition(database, old_state, new_state, event_type, sensor):
    """Log state transition to database."""
    await database.log_event(
        event_type=event_type,
        state_from=old_state.value if old_state else None,
        state_to=new_state.value,
        sensor_id=sensor,
    )


async def async_setup_sensor_listeners(
    hass: HomeAssistant,
    entry: ConfigEntry,
    state_machine: AlarmStateMachine,
    correlation_engine: CorrelationEngine,
    escalation_manager: EscalationManager,
    database: AlarmDatabase,
) -> None:
    """Set up listeners for sensor triggers."""
    # Get sensor lists with migration support
    perimeter_sensors = entry.data.get("perimeter_sensors", [])
    interior_sensors = entry.data.get("interior_sensors", [])
    
    # Migration: if old keys exist, use them
    if not perimeter_sensors and "contact_sensors" in entry.data:
        perimeter_sensors = entry.data["contact_sensors"]
        _LOGGER.info("Migrated contact_sensors to perimeter_sensors")
    
    if not interior_sensors and "motion_sensors" in entry.data:
        interior_sensors = entry.data["motion_sensors"]
        _LOGGER.info("Migrated motion_sensors to interior_sensors")
    
    # Get ML and adaptive managers
    data = hass.data[DOMAIN][entry.entry_id]
    ml_predictor = data.get("ml_predictor")
    adaptive_manager = data.get("adaptive_manager")
    
    async def sensor_triggered(event):
        """Handle sensor trigger."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        
        if new_state is None:
            return
        
        # Check if sensor should be monitored based on current state
        is_perimeter = entity_id in perimeter_sensors
        is_interior = entity_id in interior_sensors
        current_state = state_machine.state.value
        
        # Perimeter: monitored in BOTH armed_away and armed_home
        should_monitor_perimeter = is_perimeter and current_state in ("armed_away", "armed_home")
        
        # Interior: monitored ONLY in armed_away
        should_monitor_interior = is_interior and current_state == "armed_away"
        
        if not (should_monitor_perimeter or should_monitor_interior):
            _LOGGER.debug(
                "Sensor %s triggered but not monitored in state %s (perimeter=%s, interior=%s)",
                entity_id, current_state, is_perimeter, is_interior
            )
            return
        
        # CRITICAL FIX: Handle transitions from unknown/unavailable states
        # After HA restart, sensors remain unknown until first update
        # unknown -> open (perimeter) = TRIGGER
        # unknown -> motion detected (interior) = TRIGGER
        # unknown -> closed (perimeter) = NO TRIGGER
        # unknown -> clear (interior) = NO TRIGGER
        
        old_state_value = old_state.state if old_state else None
        new_state_value = new_state.state
        
        # Determine if this is a valid trigger transition
        is_valid_trigger = False
        
        if old_state_value in (STATE_UNKNOWN, STATE_UNAVAILABLE, None):
            # Transition FROM unknown/unavailable state
            if is_perimeter:
                # Perimeter sensor: trigger only if going to "on" (open)
                is_valid_trigger = new_state_value == "on"
                if is_valid_trigger:
                    _LOGGER.info(
                        "Perimeter sensor %s: %s -> on (TRIGGER after HA restart)",
                        entity_id, old_state_value
                    )
            elif is_interior:
                # Interior sensor: trigger only if going to "on" (motion)
                is_valid_trigger = new_state_value == "on"
                if is_valid_trigger:
                    _LOGGER.info(
                        "Interior sensor %s: %s -> on (TRIGGER after HA restart)",
                        entity_id, old_state_value
                    )
        else:
            # Normal transition (not from unknown)
            # Trigger only on "on" state
            is_valid_trigger = new_state_value == "on"
        
        if not is_valid_trigger:
            return
        
        entity_name = new_state.attributes.get("friendly_name", entity_id)
        
        _LOGGER.info("Sensor triggered: %s (perimeter=%s, interior=%s)", 
                     entity_name, is_perimeter, is_interior)
        
        # Determine sensor type for scoring
        sensor_type = "contact" if is_perimeter else "motion"
        
        # Handle first trigger (pre-alarm)
        if state_machine.state.value in ("armed_away", "armed_home"):
            await state_machine.trigger_pre_alarm(entity_id, entity_name)
            
            # Calculate adaptive window if available
            if adaptive_manager and ml_predictor:
                adaptive_window = adaptive_manager.get_recommended_window_for_sensor(
                    entity_id, sensor_type, ml_predictor
                )
                correlation_engine.correlation_window = adaptive_window
                _LOGGER.info("Using adaptive window: %ds", adaptive_window)
            
            # Start correlation window with callbacks
            async def timeout_handler():
                await correlation_timeout_callback(
                    hass, entry, state_machine, escalation_manager, database,
                    ml_predictor, entity_id
                )
            
            async def confirm_handler():
                await correlation_confirm_callback(
                    hass, entry, state_machine, correlation_engine,
                    escalation_manager, database, ml_predictor
                )
            
            correlation_engine.start_correlation(
                callback_timeout=timeout_handler,
                callback_confirm=confirm_handler,
            )
            
            # Add event to correlation with ML adjustment
            base_score = 70 if is_perimeter else 40
            
            if ml_predictor:
                adjusted_score = await ml_predictor.predict_score_adjustment(
                    entity_id, sensor_type, base_score
                )
                # Override base scores in correlation engine
                if is_perimeter:
                    await correlation_engine.process_contact_trigger(entity_id, entity_name)
                    # Adjust score retroactively
                    if correlation_engine._events:
                        correlation_engine._events[-1].score = adjusted_score
                        correlation_engine._total_score = sum(e.score for e in correlation_engine._events)
                else:
                    await correlation_engine.process_motion_trigger(entity_id, entity_name)
                    if correlation_engine._events:
                        correlation_engine._events[-1].score = adjusted_score
                        correlation_engine._total_score = sum(e.score for e in correlation_engine._events)
            else:
                if is_perimeter:
                    await correlation_engine.process_contact_trigger(entity_id, entity_name)
                else:
                    await correlation_engine.process_motion_trigger(entity_id, entity_name)
        
        # Handle subsequent triggers (within correlation window)
        elif state_machine.state.value == "pre_alarm":
            base_score = 70 if is_perimeter else 40
            
            if ml_predictor:
                adjusted_score = await ml_predictor.predict_score_adjustment(
                    entity_id, sensor_type, base_score
                )
                
            if is_perimeter:
                await correlation_engine.process_contact_trigger(entity_id, entity_name)
            else:
                await correlation_engine.process_motion_trigger(entity_id, entity_name)
            
            # Apply ML adjustment
            if ml_predictor and correlation_engine._events:
                correlation_engine._events[-1].score = adjusted_score
                correlation_engine._total_score = sum(e.score for e in correlation_engine._events)
    
    # Register listeners for all sensors using async_track_state_change_event
    from homeassistant.helpers.event import async_track_state_change_event
    
    all_sensors = perimeter_sensors + interior_sensors
    
    # Single listener for all sensors (more efficient)
    async_track_state_change_event(
        hass,
        all_sensors,
        sensor_triggered
    )
    
    _LOGGER.info(
        "Sensor listeners registered: %d perimeter + %d interior = %d total",
        len(perimeter_sensors), len(interior_sensors), len(all_sensors)
    )


async def correlation_timeout_callback(
    hass, entry, state_machine, escalation_manager, database, ml_predictor, sensor_id
):
    """Handle correlation window timeout."""
    _LOGGER.info("Correlation timeout - resetting pre-alarm")
    
    # Learn from false alarm if ML available
    if ml_predictor and sensor_id:
        await ml_predictor.learn_from_outcome(sensor_id, was_false_alarm=True)
    
    # Log to database
    await database.log_event(
        event_type="timeout",
        state_from="pre_alarm",
        state_to=state_machine.previous_state.value if state_machine.previous_state else "armed_away",
        sensor_id=state_machine.first_trigger_sensor,
        sensor_name=state_machine.first_trigger_name,
    )
    
    # Send Telegram notification
    await escalation_manager.send_timeout_notification(
        trigger_sensor=state_machine.first_trigger_sensor,
        trigger_name=state_machine.first_trigger_name,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    
    # Reset state machine
    await state_machine.reset_pre_alarm()


async def correlation_confirm_callback(
    hass, entry, state_machine, correlation_engine, escalation_manager, database, ml_predictor
):
    """Handle alarm confirmation."""
    _LOGGER.warning("Alarm CONFIRMED!")
    
    # Learn from confirmed alarm if ML available
    if ml_predictor and state_machine.first_trigger_sensor:
        await ml_predictor.learn_from_outcome(
            state_machine.first_trigger_sensor,
            was_false_alarm=False
        )
    
    await state_machine.confirm_alarm()
    
    # Log to database
    event_id = await database.log_event(
        event_type=EVENT_TYPE_CONFIRM,
        state_from="pre_alarm",
        state_to="alarm_confirmed",
        sensor_id=state_machine.first_trigger_sensor,
        sensor_name=state_machine.first_trigger_name,
        correlation_score=correlation_engine.total_score,
        notes=f"Events: {len(correlation_engine.events)}",
    )
    
    # Start escalation
    await escalation_manager.start_escalation(
        trigger_sensor=state_machine.first_trigger_sensor,
        trigger_name=state_machine.first_trigger_name,
        correlation_score=correlation_engine.total_score,
    )


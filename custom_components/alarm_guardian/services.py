"""Services for Alarm Guardian."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_FORCE_ARM,
    SERVICE_SILENCE_ALARM,
    SERVICE_TEST_ESCALATION,
    SERVICE_EXPORT_EVENTS,
)

_LOGGER = logging.getLogger(__name__)

# Service schemas
TEST_ESCALATION_SCHEMA = vol.Schema(
    {
        vol.Optional("test_frigate", default=False): cv.boolean,
        vol.Optional("test_database", default=True): cv.boolean,
    }
)

EXPORT_EVENTS_SCHEMA = vol.Schema(
    {
        vol.Optional("days", default=7): cv.positive_int,
        vol.Optional("format", default="csv"): vol.In(["csv", "json"]),
        vol.Optional("path", default="alarm_guardian_export.csv"): cv.string,
    }
)

FORCE_ARM_SCHEMA = vol.Schema(
    {
        vol.Optional("ignore_offline"): vol.All(cv.ensure_list, [cv.entity_id]),
    }
)

MANUAL_TRIGGER_SCHEMA = vol.Schema(
    {
        vol.Optional("reason", default="Manual panic button"): cv.string,
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for Alarm Guardian."""

    async def handle_test_escalation(call: ServiceCall) -> None:
        """Handle test_escalation service."""
        test_frigate = call.data.get("test_frigate", False)
        test_database = call.data.get("test_database", True)

        _LOGGER.info("Testing escalation sequence (frigate=%s, db=%s)", test_frigate, test_database)

        # Get first config entry (assume single instance for now)
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.error("No Alarm Guardian config entry found")
            return

        entry = entries[0]
        data = hass.data[DOMAIN][entry.entry_id]
        
        escalation_manager = data["escalation_manager"]
        database = data["database"]

        # Log test event if requested
        if test_database:
            await database.log_event(
                event_type="test",
                state_from="disarmed",
                state_to="test",
                sensor_id="service.test_escalation",
                sensor_name="Test Escalation Service",
                notes="Manual test escalation triggered",
            )

        # Simulate Frigate event if requested
        if test_frigate:
            escalation_manager.set_frigate_event_id("test_event_123")

        # Run escalation
        await escalation_manager.start_escalation(
            trigger_sensor="service.test_escalation",
            trigger_name="Test Escalation Service",
            correlation_score=999,
        )

        _LOGGER.info("Test escalation completed successfully")

    async def handle_export_events(call: ServiceCall) -> None:
        """Handle export_events service."""
        days = call.data.get("days", 7)
        format_type = call.data.get("format", "csv")
        path = call.data.get("path", "alarm_guardian_export.csv")

        _LOGGER.info("Exporting events: days=%d, format=%s, path=%s", days, format_type, path)

        # Get first config entry
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.error("No Alarm Guardian config entry found")
            return

        entry = entries[0]
        data = hass.data[DOMAIN][entry.entry_id]
        database = data["database"]

        # Build full path
        full_path = hass.config.path(path)

        if format_type == "csv":
            success = await database.export_events(full_path, days)
        else:  # json
            success = await export_events_json(database, full_path, days)

        if success:
            _LOGGER.info("Events exported successfully to %s", full_path)
        else:
            _LOGGER.error("Failed to export events to %s", full_path)

    async def handle_force_arm(call: ServiceCall) -> None:
        """Handle force_arm service."""
        ignore_offline = call.data.get("ignore_offline", [])

        _LOGGER.warning("Force arming alarm (ignoring %d offline sensors)", len(ignore_offline))

        # Get first config entry
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.error("No Alarm Guardian config entry found")
            return

        entry = entries[0]
        data = hass.data[DOMAIN][entry.entry_id]
        
        state_machine = data["state_machine"]
        coordinator = data["coordinator"]

        # Temporarily mark ignored sensors as available
        # (This is a simplified implementation - in production you'd want more robust handling)
        
        # Force arm anyway
        tuya_entity = entry.data.get("tuya_alarm_entity")
        if tuya_entity:
            await hass.services.async_call(
                "alarm_control_panel",
                "alarm_arm_away",
                {"entity_id": tuya_entity},
                blocking=True,
            )
            _LOGGER.info("Force armed via Tuya panel")
        else:
            # Fallback: just update state machine
            await state_machine.arm_away()
            _LOGGER.info("Force armed via state machine")

    async def handle_silence_alarm(call: ServiceCall) -> None:
        """Handle silence_alarm service.
        
        Silenzia la sirena esterna e abortisce le chiamate VoIP in corso,
        senza disarmare il sistema (rimane in alarm_confirmed).
        La sirena Tuya è gestita dalla centrale stessa al disarmo.
        """
        _LOGGER.info("Silencing alarm siren")

        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.error("No Alarm Guardian config entry found")
            return

        entry = entries[0]
        data = hass.data[DOMAIN].get(entry.entry_id, {})
        escalation_manager = data.get("escalation_manager")
        
        if not escalation_manager:
            _LOGGER.error("Escalation manager not found")
            return
        
        # Abortisci chiamate VoIP in corso
        if escalation_manager.is_escalating:
            _LOGGER.info("Aborting VoIP calls")
            escalation_manager.abort()
        
        # Spegni sirena esterna
        await escalation_manager.silence_external_siren()

    async def handle_manual_trigger(call: ServiceCall) -> None:
        """Handle manual_trigger service (panic button)."""
        reason = call.data.get("reason", "Manual panic button")

        _LOGGER.warning("Manual alarm trigger: %s", reason)

        # Get first config entry
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.error("No Alarm Guardian config entry found")
            return

        entry = entries[0]
        data = hass.data[DOMAIN][entry.entry_id]
        
        state_machine = data["state_machine"]
        escalation_manager = data["escalation_manager"]
        database = data["database"]

        # Log manual trigger
        await database.log_event(
            event_type="manual_trigger",
            state_from=state_machine.state_name,
            state_to="alarm_confirmed",
            sensor_id="service.manual_trigger",
            sensor_name="Manual Trigger",
            notes=reason,
        )

        # Force state to confirmed
        await state_machine.confirm_alarm()

        # Start escalation
        await escalation_manager.start_escalation(
            trigger_sensor="service.manual_trigger",
            trigger_name=f"Manual Trigger: {reason}",
            correlation_score=999,
        )

        _LOGGER.info("Manual trigger escalation started")

    async def handle_reset_statistics(call: ServiceCall) -> None:
        """Handle reset_statistics service."""
        _LOGGER.info("Resetting ML statistics")

        # Get first config entry
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.error("No Alarm Guardian config entry found")
            return

        entry = entries[0]
        data = hass.data[DOMAIN][entry.entry_id]
        
        # Reset ML predictor if exists
        if "ml_predictor" in data:
            await data["ml_predictor"].reset()
            _LOGGER.info("ML statistics reset successfully")
        else:
            _LOGGER.warning("No ML predictor found")

    async def handle_clear_fault(call: ServiceCall) -> None:
        """Handle clear_fault service."""
        _LOGGER.info("Clearing system fault")

        # Get first config entry
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.error("No Alarm Guardian config entry found")
            return

        entry = entries[0]
        data = hass.data[DOMAIN][entry.entry_id]
        state_machine = data["state_machine"]

        await state_machine.clear_fault()
        _LOGGER.info("System fault cleared")

    async def handle_force_battery_check(call: ServiceCall) -> None:
        """Gestisce il servizio force_battery_check - forza scansione batterie immediata."""
        _LOGGER.info("Servizio controllo forzato batterie chiamato")
        
        # Ottieni prima config entry
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.error("Nessuna config entry Alarm Guardian trovata")
            return
        
        entry = entries[0]
        data = hass.data[DOMAIN].get(entry.entry_id)
        
        if not data:
            _LOGGER.error("Nessun dato Alarm Guardian trovato")
            return
        
        coordinator = data.get("coordinator")
        
        if not coordinator:
            _LOGGER.error("Coordinator non trovato")
            return
        
        # Forza refresh immediato
        _LOGGER.info("Forzatura refresh coordinator per controllo batterie")
        await coordinator.async_request_refresh()
        
        # Ottieni risultato
        battery_min = coordinator.data.get("battery_min", 100) if coordinator.data else 100
        low_battery = coordinator.data.get("sensors_low_battery", []) if coordinator.data else []
        
        message = f"Controllo batterie completato!\n\n"
        message += f"Batteria minima: {battery_min}%\n"
        message += f"Sensori batteria bassa: {len(low_battery)}\n\n"
        
        if low_battery:
            message += "Sensori con batteria bassa:\n"
            for sensor in low_battery:
                message += f"- {sensor['name']}: {sensor['battery']}%\n"
        
        # Invia come notifica persistente
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Risultato Controllo Batterie",
                "message": message,
                "notification_id": "alarm_guardian_battery_check",
            },
        )
        
        _LOGGER.info("Risultato controllo batterie: min=%s%%, count_basse=%d", battery_min, len(low_battery))
    
    async def handle_test_battery_notification(call: ServiceCall) -> None:
        """Gestisce il servizio test_battery_notification - invia notifica di test."""
        _LOGGER.info("Servizio test notifica batteria chiamato")
        
        # Ottieni prima config entry
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.error("Nessuna config entry Alarm Guardian trovata")
            return
        
        entry = entries[0]
        data = hass.data[DOMAIN].get(entry.entry_id)
        
        if not data:
            _LOGGER.error("Nessun dato Alarm Guardian trovato")
            return
        
        escalation_manager = data.get("escalation_manager")
        
        if not escalation_manager:
            _LOGGER.error("Escalation manager non trovato")
            return
        
        # Crea dati di test per batteria bassa
        test_sensors = [
            {
                "entity_id": "binary_sensor.sensore_test_1",
                "name": "Sensore Test 1",
                "battery": 10.0,
            },
            {
                "entity_id": "binary_sensor.sensore_test_2",
                "name": "Sensore Test 2",
                "battery": 5.0,
            },
        ]
        
        # Invia notifica di test
        await escalation_manager.send_low_battery_alert(test_sensors)
        
        _LOGGER.info("Notifica di test batteria inviata")

    # Register services
    hass.services.async_register(
        DOMAIN,
        SERVICE_TEST_ESCALATION,
        handle_test_escalation,
        schema=TEST_ESCALATION_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_EVENTS,
        handle_export_events,
        schema=EXPORT_EVENTS_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_ARM,
        handle_force_arm,
        schema=FORCE_ARM_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SILENCE_ALARM,
        handle_silence_alarm,
    )

    hass.services.async_register(
        DOMAIN,
        "manual_trigger",
        handle_manual_trigger,
        schema=MANUAL_TRIGGER_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "reset_statistics",
        handle_reset_statistics,
    )

    hass.services.async_register(
        DOMAIN,
        "clear_fault",
        handle_clear_fault,
    )
    
    hass.services.async_register(
        DOMAIN,
        "force_battery_check",
        handle_force_battery_check,
    )
    
    hass.services.async_register(
        DOMAIN,
        "test_battery_notification",
        handle_test_battery_notification,
    )

    _LOGGER.info("Alarm Guardian services registered")


async def export_events_json(database, output_path: str, days: int) -> bool:
    """Export events to JSON format."""
    try:
        events = await database.get_recent_events(limit=1000)
        
        # Filter by days
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=days)
        filtered_events = [
            e for e in events
            if datetime.fromisoformat(e["timestamp"]) >= cutoff
        ]
        
        with open(output_path, 'w') as f:
            json.dump(filtered_events, f, indent=2, default=str)
        
        return True
    except Exception as err:
        _LOGGER.error("Failed to export JSON: %s", err)
        return False

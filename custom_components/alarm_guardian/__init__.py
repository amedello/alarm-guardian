"""The Alarm Guardian integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    DOMAIN,
    EVENT_TYPE_CONFIRM,
    CONF_ENTRY_DELAY,
    CONF_EXIT_DELAY,
    CONF_ZONES,
    DEFAULT_ENTRY_DELAY,
    DEFAULT_EXIT_DELAY,
    BOOT_GRACE_PERIOD,
)
from .coordinator import AlarmGuardianCoordinator
from .state_machine import AlarmStateMachine
from .zone_engine import ZoneEngine, build_zones_from_legacy
from .escalation import EscalationManager
from .frigate import FrigateListener
from .database import AlarmDatabase
from .ml_predictor import MLFalseAlarmPredictor
from .adaptive_correlation import AdaptiveCorrelationManager
from . import services as alarm_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alarm Guardian from a config entry."""
    _LOGGER.info("Setting up Alarm Guardian integration (v3 zone-based)")

    database = AlarmDatabase(hass, entry.entry_id)
    await database.async_setup()

    coordinator = AlarmGuardianCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    if not coordinator.last_update_success:
        raise ConfigEntryNotReady("Failed to fetch initial sensor data")
    await coordinator.async_setup_battery_monitoring()

    state_machine = AlarmStateMachine(hass)
    ml_predictor = MLFalseAlarmPredictor(hass, database)
    await ml_predictor.async_setup()
    adaptive_manager = AdaptiveCorrelationManager(
        hass, entry.data.get("correlation_window", 60)
    )
    escalation_manager = EscalationManager(hass, entry)

    # ── Costruisci ZoneEngine ──────────────────────────────────────────────
    zones_config = _get_zones_config(entry)
    correlation_window = entry.data.get("correlation_window", 60)

    async def on_confirmed():
        await _alarm_confirm_callback(
            hass, entry, state_machine, zone_engine,
            escalation_manager, database, ml_predictor,
        )

    async def on_timeout(zone_name: str):
        await _alarm_timeout_callback(
            hass, entry, state_machine, escalation_manager, database,
            ml_predictor, zone_name,
        )

    zone_engine = ZoneEngine(
        hass=hass,
        zones_config=zones_config,
        correlation_window=correlation_window,
        on_confirmed=on_confirmed,
        on_timeout=on_timeout,
    )

    # ── Frigate listener (passa zone_engine) ──────────────────────────────
    frigate_listener = FrigateListener(hass, entry, zone_engine, escalation_manager)
    await frigate_listener.async_setup()

    # ── Store ─────────────────────────────────────────────────────────────
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "state_machine": state_machine,
        "zone_engine": zone_engine,
        "escalation_manager": escalation_manager,
        "frigate_listener": frigate_listener,
        "database": database,
        "ml_predictor": ml_predictor,
        "adaptive_manager": adaptive_manager,
        "config_entry": entry,
    }

    state_machine.register_transition_callback(
        lambda old, new, event_type, sensor: _db_log(database, old, new, event_type, sensor)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await alarm_services.async_setup_services(hass)
    await _setup_tuya_listener(hass, entry, state_machine, zone_engine)
    await _setup_sensor_listeners(hass, entry, state_machine, zone_engine,
                                  escalation_manager, database, ml_predictor, adaptive_manager)

    _LOGGER.info("Alarm Guardian setup completo: %d zone, %d sensori totali",
                 len(zone_engine.zones), len(zone_engine.all_sensor_ids))

    async def _send_online():
        await asyncio.sleep(BOOT_GRACE_PERIOD.total_seconds())
        try:
            em = hass.data[DOMAIN].get(entry.entry_id, {}).get("escalation_manager")
            if em:
                await em.send_online_notification()
        except Exception as err:
            _LOGGER.warning("Failed to send online notification: %s", err)

    hass.async_create_task(_send_online())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    if "frigate_listener" in data:
        await data["frigate_listener"].async_unload()
    if "database" in data:
        await data["database"].async_close()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


# ── Helpers configurazione ─────────────────────────────────────────────────

def _get_zones_config(entry: ConfigEntry) -> list[dict]:
    """Restituisce la config delle zone, con migration automatica dal formato legacy."""
    zones = entry.data.get(CONF_ZONES)
    if zones:
        return zones
    # Migration da formato piatto pre-zone
    _LOGGER.info("Formato legacy rilevato: migrazione automatica a zona unica 'Casa'")
    return build_zones_from_legacy(entry.data)


# ── Tuya listener ──────────────────────────────────────────────────────────

async def _setup_tuya_listener(
    hass: HomeAssistant,
    entry: ConfigEntry,
    state_machine: AlarmStateMachine,
    zone_engine: ZoneEngine,
) -> None:
    tuya_entity = entry.data.get("tuya_alarm_entity")
    if not tuya_entity:
        _LOGGER.warning("Nessuna entità Tuya configurata")
        return

    async def tuya_state_changed(event):
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        tuya_state = new_state.state
        _LOGGER.debug("Tuya stato → %s", tuya_state)

        data = hass.data[DOMAIN][entry.entry_id]
        escalation_manager = data["escalation_manager"]

        if tuya_state == "disarmed":
            zone_engine.reset()
            if escalation_manager.is_escalating:
                escalation_manager.abort()
            await escalation_manager.silence_external_siren()
            await state_machine.force_disarm()
            await escalation_manager.send_disarm_notification()

        elif tuya_state in ("armed_away", "armed_home"):
            await state_machine.sync_with_tuya(tuya_state)
            await escalation_manager.send_arming_notification(tuya_state, exit_delay=0)

        else:
            await state_machine.sync_with_tuya(tuya_state)

        await _handle_frigate_detection(hass, entry, tuya_state)

    async_track_state_change_event(hass, [tuya_entity], tuya_state_changed)
    _LOGGER.info("Tuya listener registrato per %s", tuya_entity)


async def _handle_frigate_detection(hass, entry, tuya_state):
    from .const import CONF_FRIGATE_MOTION_SWITCHES, CONF_FRIGATE_DETECT_SWITCHES
    motion_sw = entry.data.get(CONF_FRIGATE_MOTION_SWITCHES, [])
    detect_sw = entry.data.get(CONF_FRIGATE_DETECT_SWITCHES, [])
    all_sw = motion_sw + detect_sw
    if not all_sw:
        return
    action = "turn_on" if tuya_state in ("armed_away", "armed_home") else "turn_off"
    for sw in all_sw:
        try:
            await hass.services.async_call("switch", action, {"entity_id": sw}, blocking=False)
        except Exception as err:
            _LOGGER.warning("Switch Frigate %s: %s", sw, err)


# ── Sensor listeners ───────────────────────────────────────────────────────

async def _setup_sensor_listeners(
    hass: HomeAssistant,
    entry: ConfigEntry,
    state_machine: AlarmStateMachine,
    zone_engine: ZoneEngine,
    escalation_manager: EscalationManager,
    database: AlarmDatabase,
    ml_predictor: MLFalseAlarmPredictor,
    adaptive_manager: AdaptiveCorrelationManager,
) -> None:
    """Registra listener per tutti i sensori di tutte le zone."""

    # Sostituisci coppie FP300 con sensori combinati
    from .binary_sensor import _find_fp300_pairs
    _patch_fp300_in_zones(zone_engine, _find_fp300_pairs)

    all_sensors = zone_engine.all_sensor_ids
    if not all_sensors:
        _LOGGER.warning("Nessun sensore configurato nelle zone")
        return

    async def sensor_triggered(event):
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return

        zone = zone_engine.get_zone_for_sensor(entity_id)
        if not zone:
            return

        current_mode = state_machine.state.value
        if not zone.is_active_in_mode(current_mode):
            # Controlla se è perimetrale: i perimetrali scattano anche in armed_home
            is_perimeter = entity_id in zone.perimeter_sensors
            if not (is_perimeter and current_mode == "armed_home"):
                return

        # Valida transizione stato
        old_val = old_state.state if old_state else None
        if old_val not in (STATE_UNKNOWN, STATE_UNAVAILABLE, None):
            if new_state.state != "on":
                return
        else:
            if new_state.state != "on":
                return

        if current_mode not in ("armed_away", "armed_home", "pre_alarm"):
            return

        entity_name = new_state.attributes.get("friendly_name", entity_id)
        sensor_type, base_score = zone_engine.sensor_type_for(entity_id, zone)

        # Score ML
        adjusted_score = base_score
        if ml_predictor:
            try:
                adjusted_score = await ml_predictor.predict_score_adjustment(
                    entity_id, sensor_type, base_score
                )
            except Exception:
                pass

        _LOGGER.info(
            "Sensore '%s' (%s) scattato in zona '%s' [%s] score=%d",
            entity_name, sensor_type, zone.zone_name, current_mode, adjusted_score,
        )

        # Entry delay solo per perimetrali al primo trigger
        if current_mode in ("armed_away", "armed_home") and entity_id in zone.perimeter_sensors:
            entry_delay = int(entry.data.get(CONF_ENTRY_DELAY, DEFAULT_ENTRY_DELAY))
            if entry_delay > 0:
                await escalation_manager.send_entry_delay_notification(entity_name, entry_delay)

                async def _entry_expired():
                    if state_machine.state.value != "pending":
                        return
                    await state_machine.trigger_pre_alarm(entity_id, entity_name)
                    await zone_engine.process_sensor_event(
                        entity_id, entity_name, current_mode, adjusted_score
                    )

                await state_machine.enter_pending(entity_id, entity_name, entry_delay, _entry_expired)
                return

        # Pre-allarme immediato se primo trigger
        if current_mode in ("armed_away", "armed_home"):
            await state_machine.trigger_pre_alarm(entity_id, entity_name)

        await zone_engine.process_sensor_event(
            entity_id, entity_name, current_mode, adjusted_score
        )

    async_track_state_change_event(hass, list(all_sensors), sensor_triggered)
    _LOGGER.info("Listener sensori registrati: %d totali in %d zone",
                 len(all_sensors), len(zone_engine.zones))


def _patch_fp300_in_zones(zone_engine: ZoneEngine, find_pairs_fn) -> None:
    """Sostituisce coppie FP300 nei sensori interni delle zone con i combinati."""
    for zone in zone_engine.zones:
        interior = list(zone.interior_sensors)
        pairs = find_pairs_fn(interior)
        if not pairs:
            continue
        source_ids = set()
        combined_ids = []
        for base_name, pir_id, presence_id in pairs:
            short = base_name.replace("binary_sensor.", "")
            combined = f"binary_sensor.{short}_fp300_combinato"
            source_ids.add(pir_id)
            source_ids.add(presence_id)
            combined_ids.append(combined)
            _LOGGER.info("FP300 zona '%s': %s+%s → %s", zone.zone_name, pir_id, presence_id, combined)
        zone.interior_sensors = {s for s in interior if s not in source_ids} | set(combined_ids)
        # Aggiorna mappa inversa nel zone_engine
        for sid in source_ids:
            zone_engine._sensor_to_zone.pop(sid, None)
        for cid in combined_ids:
            zone_engine._sensor_to_zone[cid] = zone.zone_id


# ── Callbacks allarme ──────────────────────────────────────────────────────

async def _alarm_confirm_callback(
    hass, entry, state_machine, zone_engine,
    escalation_manager, database, ml_predictor,
):
    _LOGGER.warning("ALLARME CONFERMATO")
    if ml_predictor and state_machine.first_trigger_sensor:
        await ml_predictor.learn_from_outcome(state_machine.first_trigger_sensor, was_false_alarm=False)
    await state_machine.confirm_alarm()

    # Raccogli dettaglio zona ed eventi per la notifica
    zone_attributes = _build_zone_attributes(zone_engine)

    await database.log_event(
        event_type=EVENT_TYPE_CONFIRM,
        state_from="pre_alarm",
        state_to="alarm_confirmed",
        sensor_id=state_machine.first_trigger_sensor,
        sensor_name=state_machine.first_trigger_name,
        correlation_score=int(zone_engine.global_score),
    )
    await escalation_manager.start_escalation(
        trigger_sensor=state_machine.first_trigger_sensor,
        trigger_name=state_machine.first_trigger_name,
        correlation_score=int(zone_engine.global_score),
        zone_attributes=zone_attributes,
    )


def _build_zone_attributes(zone_engine) -> dict:
    """Costruisce il dict zone_attributes per la notifica Telegram."""
    # Trova la zona che ha confermato (quella con eventi attivi)
    confirmed_zone = None
    for zone in zone_engine.zones:
        if zone.is_active and zone.events:
            confirmed_zone = zone
            break

    # Se nessuna zona locale ha confermato → conferma globale cross-zona
    confirmed_via = "local" if confirmed_zone else "global"

    # Raccogli tutti gli eventi da tutte le zone attive
    all_events = []
    for zone in zone_engine.zones:
        for ev in zone.events:
            all_events.append({
                "type": ev.sensor_type,
                "name": ev.entity_name,
                "score": ev.score,
                "zone": ev.zone_name,
            })

    # Ordina per timestamp
    all_events.sort(key=lambda e: e.get("zone", ""))

    zone_name = confirmed_zone.zone_name if confirmed_zone else "Cross-zona"

    return {
        "zone_name": zone_name,
        "confirmed_via": confirmed_via,
        "events": all_events,
    }


async def _alarm_timeout_callback(
    hass, entry, state_machine, escalation_manager,
    database, ml_predictor, zone_name: str,
):
    _LOGGER.info("Timeout correlazione zona '%s'", zone_name)
    if ml_predictor and state_machine.first_trigger_sensor:
        await ml_predictor.learn_from_outcome(state_machine.first_trigger_sensor, was_false_alarm=True)
    await database.log_event(
        event_type="timeout",
        state_from="pre_alarm",
        state_to=state_machine.previous_state.value if state_machine.previous_state else "armed_away",
        sensor_id=state_machine.first_trigger_sensor,
        sensor_name=state_machine.first_trigger_name,
    )
    await escalation_manager.send_timeout_notification(
        trigger_sensor=state_machine.first_trigger_sensor,
        trigger_name=state_machine.first_trigger_name,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    await state_machine.reset_pre_alarm()


async def _db_log(database, old_state, new_state, event_type, sensor):
    await database.log_event(
        event_type=event_type,
        state_from=old_state.value if old_state else None,
        state_to=new_state.value,
        sensor_id=sensor,
    )

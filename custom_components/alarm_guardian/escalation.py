"""Escalation manager for Alarm Guardian."""
from __future__ import annotations

import asyncio
import logging
import aiohttp
from datetime import datetime
from typing import Optional

from homeassistant.core import HomeAssistant

from .const import (
    CONF_TELEGRAM_CONFIG_ENTRY,
    CONF_TELEGRAM_TARGET,
    CONF_TELEGRAM_THREAD_ID,
    CONF_VOIP_PRIMARY,
    CONF_VOIP_SECONDARY,
    CONF_SHELL_COMMAND_VOIP,
    CONF_VOIP_CALL_DELAY,
    CONF_TUYA_ALARM_ENTITY,
    CONF_FRIGATE_HOST,
    CONF_FRIGATE_PORT,
    CONF_EXTERNAL_SIREN,
    CONF_VOIP_PROVIDER_TYPE,
    CONF_VOIP_NOTIFY_SERVICE,
    CONF_VOIP_REST_URL,
    CONF_VOIP_REST_METHOD,
    CONF_VOIP_REST_HEADERS,
    CONF_VOIP_REST_BODY,
    CHANNEL_TELEGRAM,
    CHANNEL_VOIP_PRIMARY,
    CHANNEL_VOIP_SECONDARY,
    CHANNEL_FRIGATE,
    CHANNEL_SIREN,
    VIDEO_CLIP_CHECK_INTERVAL,
    VIDEO_CLIP_MAX_WAIT,
    VOIP_PROVIDER_SHELL,
    VOIP_PROVIDER_NOTIFY,
    VOIP_PROVIDER_REST,
    VOIP_PROVIDER_DISABLED,
    BATTERY_ALERT_INTERVAL_HOURS,
)

_LOGGER = logging.getLogger(__name__)


class EscalationManager:
    """Manages alarm escalation sequence."""

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        """Initialize escalation manager."""
        self.hass = hass
        self.config_entry = config_entry
        
        # Escalation tracking
        self._escalation_in_progress = False
        self._escalation_started_at: Optional[datetime] = None
        self._channels_attempted: list[str] = []
        self._channels_success: list[str] = []
        
        # Abort flag: impostato a True quando l'utente disarma durante escalation
        self._abort_requested = False
        
        # Frigate event tracking
        self._current_frigate_event_id: Optional[str] = None
        
        # Battery alert rate limiting: {entity_id: last_sent_datetime}
        self._battery_alert_sent: dict[str, datetime] = {}
        
        # External siren tracking
        self._external_siren_on = False

    @property
    def is_escalating(self) -> bool:
        """Check if escalation is in progress."""
        return self._escalation_in_progress

    def set_frigate_event_id(self, event_id: str) -> None:
        """Set current Frigate event ID for clip/snapshot retrieval."""
        self._current_frigate_event_id = event_id
        _LOGGER.debug("Frigate event ID set: %s", event_id)

    def abort(self) -> None:
        """Abort escalation in progress (called on disarm).
        
        Imposta il flag che viene controllato dopo ogni sleep nelle fasi VoIP.
        Non interrompe immediatamente i task già partiti (telegram/snapshot),
        ma ferma le chiamate VoIP non ancora effettuate.
        """
        if self._escalation_in_progress:
            _LOGGER.warning("Escalation ABORT requested - stopping VoIP calls")
            self._abort_requested = True

    async def start_escalation(
        self,
        trigger_sensor: str,
        trigger_name: str,
        correlation_score: int,
        zone_attributes: dict | None = None,
    ) -> None:
        """Start full escalation sequence.
        
        zone_attributes: dict opzionale con dettaglio della conferma, es:
            {
                "zone_name": "Zona Notte",
                "confirmed_via": "local",   # "local" o "global"
                "events": [
                    {"type": "contact", "name": "Finestra camera", "score": 70},
                    {"type": "motion",  "name": "PIR corridoio",   "score": 40},
                ]
            }
        """
        if self._escalation_in_progress:
            _LOGGER.warning("Escalation already in progress, ignoring")
            return

        self._escalation_in_progress = True
        self._abort_requested = False
        self._escalation_started_at = datetime.now()
        self._channels_attempted = []
        self._channels_success = []

        _LOGGER.warning(
            "Starting alarm escalation sequence (sensor: %s, score: %d)",
            trigger_name, correlation_score,
        )

        try:
            await self._phase_1_immediate(trigger_sensor, trigger_name, correlation_score, zone_attributes)
            await self._phase_2_voip()
            if self._current_frigate_event_id and not self._abort_requested:
                await self._phase_3_frigate_clips()

        except Exception as err:
            _LOGGER.error("Error during escalation: %s", err, exc_info=True)
        finally:
            self._escalation_in_progress = False
            self._abort_requested = False
            _LOGGER.info(
                "Escalation complete. Channels attempted: %s, Successful: %s",
                self._channels_attempted, self._channels_success,
            )

    async def _phase_1_immediate(
        self,
        trigger_sensor: str,
        trigger_name: str,
        correlation_score: int = 0,
        zone_attributes: dict | None = None,
    ) -> None:
        """Phase 1: Immediate notifications."""
        _LOGGER.info("Escalation Phase 1: Immediate notifications")

        success = await self._send_telegram_alert(
            trigger_sensor, trigger_name, correlation_score, zone_attributes
        )
        self._channels_attempted.append(CHANNEL_TELEGRAM)
        if success:
            self._channels_success.append(CHANNEL_TELEGRAM)

        if self._current_frigate_event_id:
            success = await self._send_frigate_snapshot()
            self._channels_attempted.append(CHANNEL_FRIGATE)
            if success:
                self._channels_success.append(CHANNEL_FRIGATE)

        await self._trigger_external_siren(turn_on=True)

        # 3. Trigger Tuya siren
        success = await self._trigger_tuya_siren()
        self._channels_attempted.append(CHANNEL_SIREN)
        if success:
            self._channels_success.append(CHANNEL_SIREN)

        # 4. Trigger external siren (optional)
        await self._trigger_external_siren(turn_on=True)

    async def _phase_2_voip(self) -> None:
        """Phase 2: VoIP calls."""
        _LOGGER.info("Escalation Phase 2: VoIP calls")

        # Wait 10 seconds before first call
        await asyncio.sleep(10)
        
        # Check abort PRIMA di chiamare
        if self._abort_requested:
            _LOGGER.warning("Escalation aborted: skipping primary VoIP call")
            return

        # Primary call
        primary_number = self.config_entry.data.get(CONF_VOIP_PRIMARY)
        if primary_number:
            success = await self._make_voip_call(primary_number, is_primary=True)
            self._channels_attempted.append(CHANNEL_VOIP_PRIMARY)
            if success:
                self._channels_success.append(CHANNEL_VOIP_PRIMARY)

        # Wait for configured delay
        call_delay = self.config_entry.data.get(CONF_VOIP_CALL_DELAY, 90)
        await asyncio.sleep(call_delay)

        # Check abort PRIMA della chiamata secondaria
        if self._abort_requested:
            _LOGGER.warning("Escalation aborted: skipping secondary VoIP call")
            return

        # Secondary call
        secondary_number = self.config_entry.data.get(CONF_VOIP_SECONDARY)
        if secondary_number:
            success = await self._make_voip_call(secondary_number, is_primary=False)
            self._channels_attempted.append(CHANNEL_VOIP_SECONDARY)
            if success:
                self._channels_success.append(CHANNEL_VOIP_SECONDARY)

    async def _phase_3_frigate_clips(self) -> None:
        """Phase 3: Send Frigate video clips."""
        _LOGGER.info("Escalation Phase 3: Frigate clips")

        # Wait 5 more seconds to ensure clip is ready
        await asyncio.sleep(5)

        await self._send_frigate_clip()

    async def _send_telegram_alert(
        self,
        trigger_sensor: str,
        trigger_name: str,
        correlation_score: int = 0,
        zone_attributes: dict | None = None,
    ) -> bool:
        """Send Telegram alert message con dettaglio zona ed eventi."""
        config_entry_id = self.config_entry.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        target_chat_id = self.config_entry.data.get(CONF_TELEGRAM_TARGET)
        thread_id = self.config_entry.data.get(CONF_TELEGRAM_THREAD_ID)

        if not config_entry_id:
            _LOGGER.warning("No Telegram config entry ID configured")
            return False

        # ── Riga zona ────────────────────────────────────────────────────
        zone_name = None
        confirmed_via = None
        events = []
        if zone_attributes:
            zone_name = zone_attributes.get("zone_name")
            confirmed_via = zone_attributes.get("confirmed_via")
            events = zone_attributes.get("events", [])

        # ── Intestazione ─────────────────────────────────────────────────
        message = "🚨 *ALLARME CONFERMATO*\n\n"

        if zone_name:
            message += f"📍 Zona: *{zone_name}*\n"
        message += f"🕐 Ora: {datetime.now().strftime('%H:%M:%S')}\n"

        if confirmed_via == "global":
            message += "🌐 Conferma: _cross-zona_ (ladro in movimento)\n"
        elif confirmed_via == "local":
            message += "🏠 Conferma: _locale_\n"

        # ── Dettaglio eventi ─────────────────────────────────────────────
        if events:
            message += "\n*Sequenza rilevata:*\n"
            type_icons = {
                "contact": "🚪",
                "radar":   "📡",
                "motion":  "👁",
                "person":  "🧍",
            }
            for ev in events:
                icon = type_icons.get(ev.get("type", ""), "•")
                message += f"  {icon} {ev.get('name', ev.get('type', '?'))} _(+{ev.get('score', 0)}pt)_\n"
            message += f"\n📊 Score totale: *{correlation_score}pt*"
        else:
            # Fallback senza eventi (legacy o errore)
            message += f"\n📍 Sensore: *{trigger_name}*"
            if correlation_score:
                message += f"\n📊 Score: *{correlation_score}pt*"

        service_data = {
            "config_entry_id": config_entry_id,
            "message": message,
            "parse_mode": "markdown",
        }
        if target_chat_id:
            service_data["target"] = [target_chat_id]
        if thread_id:
            service_data["thread_id"] = thread_id

        try:
            await self.hass.services.async_call(
                "telegram_bot", "send_message", service_data, blocking=True,
            )
            _LOGGER.info("Telegram alert inviato")
            return True
        except Exception as err:
            _LOGGER.error("Telegram alert fallito: %s", err)
            return False

    async def _send_frigate_snapshot(self) -> bool:
        """Send Frigate snapshot via Telegram."""
        if not self._current_frigate_event_id:
            return False

        config_entry_id = self.config_entry.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        target_chat_id = self.config_entry.data.get(CONF_TELEGRAM_TARGET)
        
        if not config_entry_id:
            return False

        host = self.config_entry.data.get(CONF_FRIGATE_HOST, "192.168.1.109")
        port = self.config_entry.data.get(CONF_FRIGATE_PORT, 5000)
        
        snapshot_url = (
            f"http://{host}:{port}/api/events/"
            f"{self._current_frigate_event_id}/snapshot.jpg"
        )

        service_data = {
            "config_entry_id": config_entry_id,
            "url": snapshot_url,
            "caption": f"📸 Snapshot evento {self._current_frigate_event_id}",
        }

        # Add target if specified
        if target_chat_id:
            service_data["target"] = [target_chat_id]

        thread_id = self.config_entry.data.get(CONF_TELEGRAM_THREAD_ID)
        if thread_id:
            service_data["thread_id"] = thread_id

        try:
            await self.hass.services.async_call(
                "telegram_bot",
                "send_photo",
                service_data,
                blocking=True,
            )
            _LOGGER.info("Frigate snapshot sent successfully")
            return True
        except Exception as err:
            _LOGGER.error("Failed to send Frigate snapshot: %s", err)
            return False

    async def _check_video_clip_ready(self, clip_url: str) -> bool:
        """Check if video clip file is ready by making HEAD request.
        
        Returns True if clip exists and is accessible.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(clip_url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    # Check if file exists (200 OK) and has content
                    if response.status == 200:
                        content_length = response.headers.get("Content-Length")
                        if content_length and int(content_length) > 0:
                            _LOGGER.debug("Video clip ready: %d bytes", int(content_length))
                            return True
            return False
        except Exception as err:
            _LOGGER.debug("Video clip not ready yet: %s", err)
            return False

    async def _send_frigate_clip(self) -> bool:
        """Send Frigate video clip via Telegram with intelligent file check."""
        if not self._current_frigate_event_id:
            return False

        config_entry_id = self.config_entry.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        target_chat_id = self.config_entry.data.get(CONF_TELEGRAM_TARGET)
        
        if not config_entry_id:
            return False

        host = self.config_entry.data.get(CONF_FRIGATE_HOST, "192.168.1.109")
        port = self.config_entry.data.get(CONF_FRIGATE_PORT, 5000)
        
        clip_url = (
            f"http://{host}:{port}/api/events/"
            f"{self._current_frigate_event_id}/clip.mp4"
        )

        # Wait for video clip to be ready with intelligent polling
        _LOGGER.info("Waiting for video clip to be ready...")
        waited = 0
        while waited < VIDEO_CLIP_MAX_WAIT:
            if await self._check_video_clip_ready(clip_url):
                _LOGGER.info("Video clip ready after %d seconds", waited)
                break
            
            await asyncio.sleep(VIDEO_CLIP_CHECK_INTERVAL)
            waited += VIDEO_CLIP_CHECK_INTERVAL
        else:
            _LOGGER.warning(
                "Video clip not ready after %d seconds, sending anyway",
                VIDEO_CLIP_MAX_WAIT
            )

        service_data = {
            "config_entry_id": config_entry_id,
            "url": clip_url,
            "caption": f"🎬 Clip evento {self._current_frigate_event_id}",
        }

        # Add target if specified
        if target_chat_id:
            service_data["target"] = [target_chat_id]

        thread_id = self.config_entry.data.get(CONF_TELEGRAM_THREAD_ID)
        if thread_id:
            service_data["thread_id"] = thread_id

        try:
            await self.hass.services.async_call(
                "telegram_bot",
                "send_video",
                service_data,
                blocking=True,
            )
            _LOGGER.info("Frigate clip sent successfully")
            return True
        except Exception as err:
            _LOGGER.error("Failed to send Frigate clip: %s", err)
            return False

    async def _trigger_tuya_siren(self) -> bool:
        """Trigger Tuya siren via alarm_trigger service.
        
        Note: alarm_trigger only works when alarm is already armed.
        This matches original automation behavior.
        """
        tuya_entity = self.config_entry.data.get(CONF_TUYA_ALARM_ENTITY)
        
        if not tuya_entity:
            _LOGGER.warning("No Tuya alarm entity configured")
            return False

        # Check if alarm is armed (required for alarm_trigger to work properly)
        tuya_state = self.hass.states.get(tuya_entity)
        if not tuya_state:
            _LOGGER.error("Tuya alarm entity %s not found", tuya_entity)
            return False
        
        current_state = tuya_state.state
        
        if current_state not in ("armed_away", "armed_home"):
            _LOGGER.warning(
                "Cannot trigger siren: Tuya alarm is in state '%s' (not armed). "
                "alarm_trigger service requires alarm to be armed first.",
                current_state
            )
            return False

        try:
            await self.hass.services.async_call(
                "alarm_control_panel",
                "alarm_trigger",
                {"entity_id": tuya_entity},
                blocking=True,
            )
            _LOGGER.info("Tuya siren triggered via alarm_trigger (state was: %s)", current_state)
            return True
        except Exception as err:
            _LOGGER.error("Failed to trigger Tuya siren: %s", err)
            return False

    async def _trigger_external_siren(self, turn_on: bool) -> None:
        """Trigger or silence the optional external siren entity."""
        siren_entity = self.config_entry.data.get(CONF_EXTERNAL_SIREN)
        if not siren_entity:
            return
        
        action = "turn_on" if turn_on else "turn_off"
        try:
            await self.hass.services.async_call(
                "siren",
                action,
                {"entity_id": siren_entity},
                blocking=True,
            )
            self._external_siren_on = turn_on
            _LOGGER.info("External siren %s: %s", action, siren_entity)
        except Exception as err:
            _LOGGER.error("Failed to %s external siren %s: %s", action, siren_entity, err)

    async def silence_external_siren(self) -> None:
        """Turn off external siren (called on disarm or silence_alarm service)."""
        if self._external_siren_on:
            await self._trigger_external_siren(turn_on=False)

    async def _make_voip_call(self, number: str, is_primary: bool = True) -> bool:
        """Make VoIP call using the configured provider.
        
        Supporta:
        - shell_command: chiama uno shell_command HA (backward compat con Asterisk)
        - notify_service: chiama un servizio notify.* HA
        - rest_api: effettua una richiesta HTTP a un endpoint esterno
        - disabled: non fa nulla
        """
        provider_type = self.config_entry.data.get(
            CONF_VOIP_PROVIDER_TYPE, VOIP_PROVIDER_SHELL
        )
        call_type = "primary" if is_primary else "secondary"
        _LOGGER.info("Making VoIP call to %s (%s) via provider: %s", number, call_type, provider_type)

        if provider_type == VOIP_PROVIDER_DISABLED:
            _LOGGER.info("VoIP provider disabled, skipping call to %s", number)
            return True  # Non è un errore, è voluto

        elif provider_type == VOIP_PROVIDER_SHELL:
            return await self._voip_via_shell_command(number)

        elif provider_type == VOIP_PROVIDER_NOTIFY:
            return await self._voip_via_notify(number)

        elif provider_type == VOIP_PROVIDER_REST:
            return await self._voip_via_rest(number)

        else:
            _LOGGER.error("Unknown VoIP provider type: %s", provider_type)
            return False

    async def _voip_via_shell_command(self, number: str) -> bool:
        """Make VoIP call via HA shell_command service (Asterisk, etc.)."""
        shell_command = self.config_entry.data.get(
            CONF_SHELL_COMMAND_VOIP,
            "asterisk_call"
        )
        try:
            await self.hass.services.async_call(
                "shell_command",
                shell_command,
                {"number": number},
                blocking=True,
            )
            _LOGGER.info("VoIP shell_command call initiated to %s", number)
            return True
        except Exception as err:
            _LOGGER.error("Failed to make shell_command VoIP call to %s: %s", number, err)
            return False

    async def _voip_via_notify(self, number: str) -> bool:
        """Make VoIP call via HA notify service."""
        notify_service = self.config_entry.data.get(CONF_VOIP_NOTIFY_SERVICE, "")
        if not notify_service:
            _LOGGER.error("No notify service configured for VoIP")
            return False
        
        # notify_service può essere "voip_provider" → chiama notify.voip_provider
        domain, service = ("notify", notify_service) if "." not in notify_service else notify_service.split(".", 1)
        try:
            await self.hass.services.async_call(
                domain,
                service,
                {
                    "message": f"🚨 ALLARME - Chiamata da Alarm Guardian",
                    "target": number,
                },
                blocking=True,
            )
            _LOGGER.info("VoIP notify call initiated to %s via %s.%s", number, domain, service)
            return True
        except Exception as err:
            _LOGGER.error("Failed to make notify VoIP call to %s: %s", number, err)
            return False

    async def _voip_via_rest(self, number: str) -> bool:
        """Make VoIP call via REST API."""
        url = self.config_entry.data.get(CONF_VOIP_REST_URL, "")
        if not url:
            _LOGGER.error("No REST URL configured for VoIP")
            return False
        
        method = self.config_entry.data.get(CONF_VOIP_REST_METHOD, "POST").upper()
        headers_str = self.config_entry.data.get(CONF_VOIP_REST_HEADERS, "{}")
        body_template = self.config_entry.data.get(CONF_VOIP_REST_BODY, '{"number": "{number}"}')
        
        try:
            import json as json_lib
            headers = json_lib.loads(headers_str) if headers_str else {}
            body_str = body_template.replace("{number}", number)
            body = json_lib.loads(body_str)
        except Exception as err:
            _LOGGER.error("Failed to parse VoIP REST config: %s", err)
            return False
        
        try:
            async with aiohttp.ClientSession() as session:
                req = getattr(session, method.lower())
                async with req(
                    url,
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status < 300:
                        _LOGGER.info("VoIP REST call to %s: HTTP %d", number, response.status)
                        return True
                    else:
                        _LOGGER.error("VoIP REST call failed: HTTP %d", response.status)
                        return False
        except Exception as err:
            _LOGGER.error("Failed to make REST VoIP call to %s: %s", number, err)
            return False

    async def send_timeout_notification(
        self,
        trigger_sensor: str,
        trigger_name: str,
        timestamp: str,
    ) -> None:
        """Send notification when correlation timeout occurs (no confirmation)."""
        config_entry_id = self.config_entry.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        target_chat_id = self.config_entry.data.get(CONF_TELEGRAM_TARGET)
        
        if not config_entry_id:
            _LOGGER.warning("No Telegram config entry ID configured")
            return

        message = (
            f"⚠️ *Preallarme scaduto senza conferma*\n\n"
            f"📍 Sensore: *{trigger_name}*\n"
            f"🕐 Timestamp: {timestamp}\n\n"
            f"ℹ️ Nessun secondo trigger rilevato. "
            f"Possibile falso allarme."
        )

        service_data = {
            "config_entry_id": config_entry_id,
            "message": message,
            "parse_mode": "markdown",
        }

        # Add target if specified
        if target_chat_id:
            service_data["target"] = [target_chat_id]

        thread_id = self.config_entry.data.get(CONF_TELEGRAM_THREAD_ID)
        if thread_id:
            service_data["thread_id"] = thread_id

        try:
            await self.hass.services.async_call(
                "telegram_bot",
                "send_message",
                service_data,
                blocking=True,
            )
            _LOGGER.info("Timeout notification sent successfully")
        except Exception as err:
            _LOGGER.error("Failed to send timeout notification: %s", err)

    async def send_jamming_alert(
        self,
        jamming_reason: str,
        offline_sensors: list[str],
    ) -> None:
        """Send Telegram alert when RF jamming is detected."""
        config_entry_id = self.config_entry.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        target_chat_id = self.config_entry.data.get(CONF_TELEGRAM_TARGET)

        if not config_entry_id:
            _LOGGER.warning("No Telegram config entry ID configured for jamming alert")
            return

        # Sensori: usa nomi friendly se disponibili, senza markdown per evitare
        # errori di parsing con underscore negli entity_id
        if offline_sensors:
            sensors_lines = []
            for sensor in offline_sensors[:10]:
                # Prendi friendly_name se disponibile, altrimenti entity_id pulito
                state = self.hass.states.get(sensor)
                name = state.attributes.get("friendly_name", sensor) if state else sensor
                sensors_lines.append(f"• {name}")
            sensors_list = "\n".join(sensors_lines)
            if len(offline_sensors) > 10:
                sensors_list += f"\n• ... e altri {len(offline_sensors) - 10}"
        else:
            sensors_list = "Nessun sensore specificato"

        # Messaggio in testo semplice (no parse_mode) per evitare errori markdown
        message = (
            f"🚨 ATTENZIONE: JAMMING RF RILEVATO\n\n"
            f"⚠️ {jamming_reason}\n\n"
            f"📡 Possibile interferenza RF o attacco di jamming!\n\n"
            f"Sensori offline:\n{sensors_list}\n\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"⚡ Verificare immediatamente il sistema!"
        )

        service_data = {
            "config_entry_id": config_entry_id,
            "message": message,
        }

        if target_chat_id:
            service_data["target"] = [target_chat_id]

        thread_id = self.config_entry.data.get(CONF_TELEGRAM_THREAD_ID)
        if thread_id:
            service_data["thread_id"] = thread_id

        try:
            await self.hass.services.async_call(
                "telegram_bot",
                "send_message",
                service_data,
                blocking=True,
            )
            _LOGGER.warning("Jamming alert sent via Telegram")
        except Exception as err:
            _LOGGER.error("Failed to send jamming alert: %s", err)

    async def send_low_battery_alert(
        self,
        low_battery_sensors: list[dict],
    ) -> None:
        """Send Telegram alert when low battery sensors are detected.
        
        Rate limiting: ogni sensore viene notificato al massimo una volta ogni BATTERY_ALERT_INTERVAL_HOURS ore.
        """
        config_entry_id = self.config_entry.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        target_chat_id = self.config_entry.data.get(CONF_TELEGRAM_TARGET)
        
        if not config_entry_id:
            _LOGGER.warning(
                "Nessun config entry ID Telegram configurato per alert batteria"
            )
            return

        # Filtra solo i sensori non ancora notificati nelle ultime N ore
        now = datetime.now()
        interval_hours = BATTERY_ALERT_INTERVAL_HOURS
        sensors_to_notify = []
        
        for sensor in low_battery_sensors:
            entity_id = sensor.get("entity_id", "")
            last_sent = self._battery_alert_sent.get(entity_id)
            
            if last_sent is None or (now - last_sent).total_seconds() > interval_hours * 3600:
                sensors_to_notify.append(sensor)
            else:
                hours_ago = (now - last_sent).total_seconds() / 3600
                _LOGGER.debug(
                    "Batteria bassa %s già notificata %.1f ore fa, skip",
                    entity_id, hours_ago
                )

        if not sensors_to_notify:
            _LOGGER.debug("Nessun sensore batteria bassa da notificare (tutti in rate limit)")
            return

        # Formatta lista sensori da notificare
        sensors_list = "\n".join([
            f"• {s['name']}: *{s['battery']:.1f}%*" 
            for s in sensors_to_notify[:10]
        ])
        if len(sensors_to_notify) > 10:
            sensors_list += f"\n• ... e altri {len(sensors_to_notify) - 10} sensori"

        message = (
            f"🔋 *ATTENZIONE: BATTERIA BASSA*\n\n"
            f"⚠️ {len(sensors_to_notify)} sensore/i con batteria sotto soglia!\n\n"
            f"*Sensori da controllare:*\n{sensors_list}\n\n"
            f"🕐 Timestamp: {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"🔧 Sostituire le batterie al più presto!"
        )

        service_data = {
            "config_entry_id": config_entry_id,
            "message": message,
            "parse_mode": "markdown",
        }

        if target_chat_id:
            service_data["target"] = [target_chat_id]

        thread_id = self.config_entry.data.get(CONF_TELEGRAM_THREAD_ID)
        if thread_id:
            service_data["thread_id"] = thread_id

        try:
            await self.hass.services.async_call(
                "telegram_bot",
                "send_message",
                service_data,
                blocking=True,
            )
            # Aggiorna il timestamp di invio per i sensori notificati
            for sensor in sensors_to_notify:
                self._battery_alert_sent[sensor.get("entity_id", "")] = now
            _LOGGER.warning(
                "Alert batteria bassa inviato via Telegram per %d sensori",
                len(sensors_to_notify)
            )
        except Exception as err:
            _LOGGER.error("Errore nell'invio alert batteria bassa: %s", err)

    async def send_arming_notification(self, mode: str, exit_delay: int = 0) -> None:
        """Send Telegram notification when alarm is arming."""
        config_entry_id = self.config_entry.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        target_chat_id = self.config_entry.data.get(CONF_TELEGRAM_TARGET)
        if not config_entry_id:
            return

        mode_label = "🏠 Casa" if mode == "armed_home" else "🚗 Fuori casa"
        if exit_delay > 0:
            message = (
                f"🔐 *Sistema in armamento*\n\n"
                f"Modalità: {mode_label}\n"
                f"⏱ Hai *{exit_delay} secondi* per uscire\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}"
            )
        else:
            message = (
                f"✅ *Sistema ARMATO*\n\n"
                f"Modalità: {mode_label}\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}"
            )

        await self._send_telegram_message(message, config_entry_id, target_chat_id)

    async def send_disarm_notification(self) -> None:
        """Send Telegram notification when alarm is disarmed."""
        config_entry_id = self.config_entry.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        target_chat_id = self.config_entry.data.get(CONF_TELEGRAM_TARGET)
        if not config_entry_id:
            return

        message = (
            f"🔓 *Sistema DISARMATO*\n\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        )
        await self._send_telegram_message(message, config_entry_id, target_chat_id)

    async def send_entry_delay_notification(self, sensor_name: str, entry_delay: int) -> None:
        """Send Telegram notification when entry delay starts."""
        config_entry_id = self.config_entry.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        target_chat_id = self.config_entry.data.get(CONF_TELEGRAM_TARGET)
        if not config_entry_id:
            return

        message = (
            f"🔔 *Sensore attivato - Ritardo ingresso*\n\n"
            f"📍 {sensor_name}\n"
            f"⏱ Hai *{entry_delay} secondi* per disarmare\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        )
        await self._send_telegram_message(message, config_entry_id, target_chat_id)

    async def send_online_notification(self) -> None:
        """Send Telegram notification when system comes online after boot."""
        config_entry_id = self.config_entry.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        target_chat_id = self.config_entry.data.get(CONF_TELEGRAM_TARGET)
        if not config_entry_id:
            return

        message = (
            f"✅ *Alarm Guardian online*\n\n"
            f"Il sistema di allarme è operativo.\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self._send_telegram_message(message, config_entry_id, target_chat_id)

    async def _send_telegram_message(
        self, message: str, config_entry_id: str, target_chat_id: Optional[str]
    ) -> bool:
        """Helper per inviare un messaggio Telegram generico."""
        thread_id = self.config_entry.data.get(CONF_TELEGRAM_THREAD_ID)
        service_data: dict = {
            "config_entry_id": config_entry_id,
            "message": message,
            "parse_mode": "markdown",
        }
        if target_chat_id:
            service_data["target"] = [target_chat_id]
        if thread_id:
            service_data["thread_id"] = thread_id
        try:
            await self.hass.services.async_call(
                "telegram_bot", "send_message", service_data, blocking=True
            )
            return True
        except Exception as err:
            _LOGGER.error("Failed to send Telegram message: %s", err)
            return False

    def reset(self) -> None:
        """Reset escalation state."""
        self._escalation_in_progress = False
        self._abort_requested = False
        self._escalation_started_at = None
        self._channels_attempted = []
        self._channels_success = []
        self._current_frigate_event_id = None
        _LOGGER.debug("Escalation state reset")

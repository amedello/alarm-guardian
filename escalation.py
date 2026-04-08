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
    CONF_FRIGATE_HOST,
    CONF_FRIGATE_PORT,
    CHANNEL_TELEGRAM,
    CHANNEL_VOIP_PRIMARY,
    CHANNEL_VOIP_SECONDARY,
    CHANNEL_FRIGATE,
    CHANNEL_SIREN,
    VIDEO_CLIP_CHECK_INTERVAL,
    VIDEO_CLIP_MAX_WAIT,
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
        
        # Frigate event tracking
        self._current_frigate_event_id: Optional[str] = None

    @property
    def is_escalating(self) -> bool:
        """Check if escalation is in progress."""
        return self._escalation_in_progress

    def set_frigate_event_id(self, event_id: str) -> None:
        """Set current Frigate event ID for clip/snapshot retrieval."""
        self._current_frigate_event_id = event_id
        _LOGGER.debug("Frigate event ID set: %s", event_id)

    async def start_escalation(
        self,
        trigger_sensor: str,
        trigger_name: str,
        correlation_score: int,
    ) -> None:
        """Start full escalation sequence."""
        if self._escalation_in_progress:
            _LOGGER.warning("Escalation already in progress, ignoring")
            return

        self._escalation_in_progress = True
        self._escalation_started_at = datetime.now()
        self._channels_attempted = []
        self._channels_success = []

        _LOGGER.warning(
            "Starting alarm escalation sequence (sensor: %s, score: %d)",
            trigger_name,
            correlation_score,
        )

        try:
            # Phase 1: Immediate notifications (T+0s)
            await self._phase_1_immediate(trigger_sensor, trigger_name)
            
            # Phase 2: VoIP calls (T+10s primary, T+100s secondary)
            await self._phase_2_voip()
            
            # Phase 3: Frigate clips (T+105s)
            if self._current_frigate_event_id:
                await self._phase_3_frigate_clips()

        except Exception as err:
            _LOGGER.error("Error during escalation: %s", err, exc_info=True)
        finally:
            self._escalation_in_progress = False
            
            _LOGGER.info(
                "Escalation complete. Channels attempted: %s, Successful: %s",
                self._channels_attempted,
                self._channels_success,
            )

    async def _phase_1_immediate(
        self,
        trigger_sensor: str,
        trigger_name: str,
    ) -> None:
        """Phase 1: Immediate notifications."""
        _LOGGER.info("Escalation Phase 1: Immediate notifications")

        # 1. Telegram notification
        success = await self._send_telegram_alert(trigger_sensor, trigger_name)
        self._channels_attempted.append(CHANNEL_TELEGRAM)
        if success:
            self._channels_success.append(CHANNEL_TELEGRAM)

        # 2. Frigate snapshot (if event available)
        if self._current_frigate_event_id:
            success = await self._send_frigate_snapshot()
            self._channels_attempted.append(CHANNEL_FRIGATE)
            if success:
                self._channels_success.append(CHANNEL_FRIGATE)

        # 3. Trigger alarm panel siren
        success = await self._trigger_alarm_panel_siren()
        self._channels_attempted.append(CHANNEL_SIREN)
        if success:
            self._channels_success.append(CHANNEL_SIREN)

    async def _phase_2_voip(self) -> None:
        """Phase 2: VoIP calls."""
        _LOGGER.info("Escalation Phase 2: VoIP calls")

        # Wait 10 seconds before first call
        await asyncio.sleep(10)

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
    ) -> bool:
        """Send Telegram alert message."""
        config_entry_id = self.config_entry.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        target_chat_id = self.config_entry.data.get(CONF_TELEGRAM_TARGET)
        thread_id = self.config_entry.data.get(CONF_TELEGRAM_THREAD_ID)

        if not config_entry_id:
            _LOGGER.warning("No Telegram config entry ID configured")
            return False

        message = (
            f"🚨 *ALLARME CONFERMATO*\n\n"
            f"📍 Sensore: *{trigger_name}*\n"
            f"🕐 Ora: {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"⚠️ Sistema in allerta"
        )

        service_data = {
            "config_entry_id": config_entry_id,
            "message": message,
            "parse_mode": "markdown",
        }

        # Add target if specified
        if target_chat_id:
            service_data["target"] = [target_chat_id]

        if thread_id:
            service_data["thread_id"] = thread_id

        try:
            await self.hass.services.async_call(
                "telegram_bot",
                "send_message",
                service_data,
                blocking=True,
            )
            _LOGGER.info("Telegram alert sent successfully")
            return True
        except Exception as err:
            _LOGGER.error("Failed to send Telegram alert: %s", err)
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

    async def _trigger_alarm_panel_siren(self) -> bool:
        """Trigger alarm panel siren via alarm_trigger service.
        
        Note: alarm_trigger only works when alarm is already armed.
        This matches original automation behavior.
        """
        from .const import CONF_ALARM_PANEL_ENTITY
        alarm_panel_entity = self.config_entry.data.get(CONF_ALARM_PANEL_ENTITY)
        
        if not alarm_panel_entity:
            _LOGGER.warning("No alarm panel entity configured")
            return False

        # Check if alarm is armed (required for alarm_trigger to work properly)
        panel_state = self.hass.states.get(alarm_panel_entity)
        if not panel_state:
            _LOGGER.error("Alarm panel entity %s not found", alarm_panel_entity)
            return False
        
        current_state = panel_state.state
        
        if current_state not in ("armed_away", "armed_home"):
            _LOGGER.warning(
                "Cannot trigger siren: alarm panel is in state '%s' (not armed). "
                "alarm_trigger service requires alarm to be armed first.",
                current_state
            )
            return False

        try:
            await self.hass.services.async_call(
                "alarm_control_panel",
                "alarm_trigger",
                {"entity_id": alarm_panel_entity},
                blocking=True,
            )
            _LOGGER.info("Alarm panel siren triggered via alarm_trigger (state was: %s)", current_state)
            return True
        except Exception as err:
            _LOGGER.error("Failed to trigger alarm panel siren: %s", err)
            return False

    async def _make_voip_call(self, number: str, is_primary: bool = True) -> bool:
        """Make VoIP call using shell command."""
        shell_command = self.config_entry.data.get(
            CONF_SHELL_COMMAND_VOIP,
            "asterisk_call"
        )

        call_type = "primary" if is_primary else "secondary"
        _LOGGER.info("Making VoIP call to %s (%s)", number, call_type)

        try:
            await self.hass.services.async_call(
                "shell_command",
                shell_command,
                {"number": number},
                blocking=True,
            )
            _LOGGER.info("VoIP call initiated to %s", number)
            return True
        except Exception as err:
            _LOGGER.error("Failed to make VoIP call to %s: %s", number, err)
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

        # Format offline sensors list
        if offline_sensors:
            sensors_list = "\n".join([f"• {sensor}" for sensor in offline_sensors[:10]])
            if len(offline_sensors) > 10:
                sensors_list += f"\n• ... e altri {len(offline_sensors) - 10} sensori"
        else:
            sensors_list = "Nessun sensore specificato"

        message = (
            f"🚨 *ATTENZIONE: JAMMING RF RILEVATO*\n\n"
            f"⚠️ {jamming_reason}\n\n"
            f"📡 Possibile interferenza RF o attacco di jamming!\n\n"
            f"*Sensori Offline:*\n{sensors_list}\n\n"
            f"🕐 Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"⚡ Azione richiesta: verificare immediatamente il sistema!"
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
            _LOGGER.warning("Jamming alert sent via Telegram")
        except Exception as err:
            _LOGGER.error("Failed to send jamming alert: %s", err)

    def reset(self) -> None:
        """Reset escalation state."""
        self._escalation_in_progress = False
        self._escalation_started_at = None
        self._channels_attempted = []
        self._channels_success = []
        self._current_frigate_event_id = None
        _LOGGER.debug("Escalation state reset")

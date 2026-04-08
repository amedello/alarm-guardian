"""Config flow for Alarm Guardian integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_PERIMETER_SENSORS,
    CONF_INTERIOR_SENSORS,
    CONF_CONTACT_SENSORS,  # For migration
    CONF_MOTION_SENSORS,   # For migration
    CONF_FRIGATE_CAMERAS,
    CONF_FRIGATE_HOST,
    CONF_FRIGATE_PORT,
    CONF_FRIGATE_MOTION_SWITCHES,
    CONF_FRIGATE_DETECT_SWITCHES,
    CONF_ALARM_PANEL_ENTITY,
    CONF_TELEGRAM_CONFIG_ENTRY,
    CONF_TELEGRAM_TARGET,
    CONF_TELEGRAM_THREAD_ID,
    CONF_VOIP_PRIMARY,
    CONF_VOIP_SECONDARY,
    CONF_SHELL_COMMAND_VOIP,
    CONF_ARMING_DELAY,
    CONF_CORRELATION_WINDOW,
    CONF_VOIP_CALL_DELAY,
    CONF_BATTERY_THRESHOLD,
    CONF_JAMMING_MIN_DEVICES,
    CONF_JAMMING_MIN_PERCENT,
    DEFAULT_ARMING_DELAY,
    DEFAULT_CORRELATION_WINDOW,
    DEFAULT_VOIP_CALL_DELAY,
    DEFAULT_BATTERY_THRESHOLD,
    DEFAULT_JAMMING_MIN_DEVICES,
    DEFAULT_JAMMING_MIN_PERCENT,
    DEFAULT_FRIGATE_HOST,
    DEFAULT_FRIGATE_PORT,
    DEFAULT_TELEGRAM_CONFIG_ENTRY,
    DEFAULT_TELEGRAM_TARGET,
)

_LOGGER = logging.getLogger(__name__)


def get_telegram_bot_config_entries(hass: HomeAssistant) -> dict[str, str]:
    """Get available Telegram bot config entries from UI integrations.
    
    Returns dict of {config_entry_id: bot_name}
    
    NOTE: This only works for Telegram bots added via UI.
    YAML-configured bots (platform: polling) do NOT create config entries.
    """
    telegram_bots = {}
    
    try:
        # Look for telegram_bot config entries (UI-based only)
        for entry in hass.config_entries.async_entries("telegram_bot"):
            bot_name = entry.title or entry.data.get("username", entry.entry_id[:8])
            telegram_bots[entry.entry_id] = f"{bot_name} ({entry.entry_id[:8]}...)"
            _LOGGER.debug("Found Telegram bot: %s (%s)", bot_name, entry.entry_id)
    except Exception as err:
        _LOGGER.warning("Error getting Telegram bots: %s", err)
    
    return telegram_bots


async def get_telegram_allowed_chat_ids(hass: HomeAssistant, config_entry_id: str) -> list[str]:
    """Get allowed chat IDs for a specific Telegram bot config entry.
    
    Returns list of chat IDs as strings.
    """
    allowed_chat_ids = []
    
    try:
        # Get the config entry
        for entry in hass.config_entries.async_entries("telegram_bot"):
            if entry.entry_id == config_entry_id:
                # Get allowed_chat_ids from config entry data
                chat_ids = entry.data.get("allowed_chat_ids", [])
                allowed_chat_ids = [str(chat_id) for chat_id in chat_ids]
                _LOGGER.debug("Found %d allowed chat IDs for bot %s", len(allowed_chat_ids), config_entry_id)
                break
    except Exception as err:
        _LOGGER.warning("Failed to get allowed chat IDs: %s", err)
    
    return allowed_chat_ids


class AlarmGuardianConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Alarm Guardian."""

    VERSION = 3  # Incremented for Telegram config changes

    def __init__(self) -> None:
        """Initialize config flow."""
        self.data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial step - alarm panel selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.data[CONF_ALARM_PANEL_ENTITY] = user_input[CONF_ALARM_PANEL_ENTITY]
            return await self.async_step_perimeter()

        # Get all alarm_control_panel entities
        alarm_entities = [
            entity_id
            for entity_id in self.hass.states.async_entity_ids("alarm_control_panel")
        ]

        if not alarm_entities:
            return self.async_abort(reason="no_alarm_panel")

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ALARM_PANEL_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="alarm_control_panel",
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "name": "Alarm Guardian",
            },
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> config_entries.FlowResult:
        """Handle import from configuration.yaml (not used but required by HA)."""
        return await self.async_step_user(import_data)

    async def async_step_perimeter(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2: Select PERIMETER sensors (doors/windows - always monitored)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate at least one sensor selected
            if not user_input.get(CONF_PERIMETER_SENSORS):
                errors["base"] = "no_sensors"
            else:
                self.data.update(user_input)
                return await self.async_step_interior()

        # Auto-detect perimeter sensors
        all_binary_sensors = self.hass.states.async_entity_ids("binary_sensor")
        
        perimeter_sensors = [
            entity_id
            for entity_id in all_binary_sensors
            if any(keyword in entity_id for keyword in ["finestra", "porta", "door", "window", "contact"])
        ]

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_PERIMETER_SENSORS,
                    default=perimeter_sensors,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="binary_sensor",
                        multiple=True,
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="perimeter",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "count": len(perimeter_sensors),
            },
        )

    async def async_step_interior(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3: Select INTERIOR sensors (motion - armed_away only)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Interior sensors are optional
            self.data.update(user_input)
            return await self.async_step_frigate()

        # Auto-detect motion sensors
        all_binary_sensors = self.hass.states.async_entity_ids("binary_sensor")
        
        interior_sensors = [
            entity_id
            for entity_id in all_binary_sensors
            if any(keyword in entity_id for keyword in ["motion", "movimento", "occupancy"])
        ]

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_INTERIOR_SENSORS,
                    default=interior_sensors,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="binary_sensor",
                        multiple=True,
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="interior",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "count": len(interior_sensors),
            },
        )

    async def async_step_frigate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 4: Configure Frigate cameras."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_frigate_switches()

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_FRIGATE_CAMERAS,
                    default=["ingresso", "cucina", "garage"],
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["ingresso", "cucina", "garage"],
                        multiple=True,
                        custom_value=True,
                    ),
                ),
                vol.Optional(
                    CONF_FRIGATE_HOST,
                    default=DEFAULT_FRIGATE_HOST,
                ): cv.string,
                vol.Optional(
                    CONF_FRIGATE_PORT,
                    default=DEFAULT_FRIGATE_PORT,
                ): cv.port,
            }
        )

        return self.async_show_form(
            step_id="frigate",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_frigate_switches(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 5: Select Frigate control switches."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_telegram()

        # Auto-detect switch entities
        all_switches = self.hass.states.async_entity_ids("switch")
        
        motion_switches = [s for s in all_switches if "motion" in s.lower()]
        detect_switches = [s for s in all_switches if "detect" in s.lower()]

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_FRIGATE_MOTION_SWITCHES,
                    default=motion_switches,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="switch",
                        multiple=True,
                    ),
                ),
                vol.Optional(
                    CONF_FRIGATE_DETECT_SWITCHES,
                    default=detect_switches,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="switch",
                        multiple=True,
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="frigate_switches",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "motion_count": len(motion_switches),
                "detect_count": len(detect_switches),
            },
        )

    async def async_step_telegram(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 6: Configure Telegram bot (UI integration required)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.data.update(user_input)
            
            # If config_entry_id provided, try to get chat IDs for next step
            config_entry_id = user_input.get(CONF_TELEGRAM_CONFIG_ENTRY)
            if config_entry_id:
                # Check if it's a valid UUID-like string (from dropdown)
                # vs manual entry (could be any string)
                allowed_chat_ids = await get_telegram_allowed_chat_ids(self.hass, config_entry_id)
                
                if allowed_chat_ids:
                    # Bot found with chat IDs → go to target selection
                    return await self.async_step_telegram_target()
            
            # No chat IDs found or manual entry → skip to notifications
            return await self.async_step_notifications()

        # Try to get Telegram bots from UI integrations
        telegram_bots = get_telegram_bot_config_entries(self.hass)
        
        # Build schema based on whether bots were found
        if telegram_bots:
            # DROPDOWN MODE: Bots found
            _LOGGER.info("Found %d Telegram bot(s) configured via UI", len(telegram_bots))
            
            bot_options = [
                selector.SelectOptionDict(value=entry_id, label=bot_name)
                for entry_id, bot_name in telegram_bots.items()
            ]

            data_schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_TELEGRAM_CONFIG_ENTRY,
                        description="Select your Telegram bot",
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=bot_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        ),
                    ),
                }
            )
            
            description = f"Found {len(telegram_bots)} Telegram bot(s). Select one to continue."
        else:
            # MANUAL INPUT MODE: No bots found - fallback to manual entry
            _LOGGER.warning(
                "No Telegram bots found via UI. "
                "User must configure Telegram via Settings > Integrations first, "
                "or enter config_entry_id manually."
            )
            
            data_schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_TELEGRAM_CONFIG_ENTRY,
                        description="Enter your Telegram bot config_entry_id manually",
                    ): cv.string,
                    vol.Optional(
                        CONF_TELEGRAM_TARGET,
                        description="Enter target chat ID (e.g., -1003702552742)",
                    ): cv.string,
                    vol.Optional(
                        CONF_TELEGRAM_THREAD_ID,
                        description="Thread ID (optional, for groups with topics)",
                    ): cv.string,
                }
            )
            
            description = (
                "⚠️ No Telegram bots found via UI integration. "
                "Please configure Telegram bot in Settings > Integrations first. "
                "Alternatively, you can enter the config_entry_id and target chat ID manually below, "
                "or skip this step to configure later."
            )

        return self.async_show_form(
            step_id="telegram",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "info": description,
            },
        )

    async def async_step_telegram_target(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 7: Select Telegram target chat ID (only if bot found via UI)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_notifications()

        # Get config entry ID from previous step
        config_entry_id = self.data.get(CONF_TELEGRAM_CONFIG_ENTRY)
        
        if not config_entry_id:
            # Should not happen, but fallback
            return await self.async_step_notifications()

        # Get allowed chat IDs
        allowed_chat_ids = await get_telegram_allowed_chat_ids(self.hass, config_entry_id)
        
        if not allowed_chat_ids:
            _LOGGER.warning("No allowed chat IDs found for bot %s, will use manual input", config_entry_id)
            
            # No chat IDs found - use manual text input instead of dropdown
            data_schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_TELEGRAM_TARGET,
                        description="Enter target chat ID manually (e.g., -1003702552742)",
                    ): cv.string,
                    vol.Optional(
                        CONF_TELEGRAM_THREAD_ID,
                        description="Thread ID (optional, for groups with topics)",
                    ): cv.string,
                }
            )
            
            return self.async_show_form(
                step_id="telegram_target",
                data_schema=data_schema,
                errors=errors,
                description_placeholders={
                    "info": "No allowed chat IDs found in Telegram bot config. Please enter target chat ID manually.",
                },
            )
        
        # Create selector options
        chat_options = [
            selector.SelectOptionDict(value=chat_id, label=f"Chat ID: {chat_id}")
            for chat_id in allowed_chat_ids
        ]

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TELEGRAM_TARGET,
                    description="Select target chat/group for notifications",
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=chat_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        custom_value=True,  # ← NUOVO! Permette inserimento manuale
                    ),
                ),
                vol.Optional(
                    CONF_TELEGRAM_THREAD_ID,
                    description="Thread ID (optional, for groups with topics)",
                ): cv.string,
            }
        )

        return self.async_show_form(
            step_id="telegram_target",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "chat_count": len(allowed_chat_ids),
                "info": f"Found {len(allowed_chat_ids)} allowed chat(s). Select from dropdown or enter custom chat ID.",
            },
        )

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 8: Configure VoIP notifications."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_timing()

        # Get available shell commands
        shell_commands = []
        if hasattr(self.hass.services, "async_services"):
            services = self.hass.services.async_services()
            if "shell_command" in services:
                shell_commands = list(services["shell_command"].keys())

        data_schema = vol.Schema(
            {
                vol.Required(CONF_VOIP_PRIMARY): cv.string,
                vol.Optional(CONF_VOIP_SECONDARY): cv.string,
                vol.Optional(
                    CONF_SHELL_COMMAND_VOIP,
                    default="asterisk_call",
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=shell_commands if shell_commands else ["asterisk_call"],
                        custom_value=True,
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="notifications",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_timing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 9: Configure timing parameters."""
        if user_input is not None:
            self.data.update(user_input)
            
            # Create config entry
            return self.async_create_entry(
                title="Alarm Guardian",
                data=self.data,
            )

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ARMING_DELAY,
                    default=DEFAULT_ARMING_DELAY,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=120,
                        unit_of_measurement="seconds",
                    ),
                ),
                vol.Optional(
                    CONF_CORRELATION_WINDOW,
                    default=DEFAULT_CORRELATION_WINDOW,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10,
                        max=300,
                        unit_of_measurement="seconds",
                    ),
                ),
                vol.Optional(
                    CONF_VOIP_CALL_DELAY,
                    default=DEFAULT_VOIP_CALL_DELAY,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=20,
                        max=180,
                        unit_of_measurement="seconds",
                    ),
                ),
                vol.Optional(
                    CONF_BATTERY_THRESHOLD,
                    default=DEFAULT_BATTERY_THRESHOLD,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5,
                        max=50,
                        unit_of_measurement="%",
                    ),
                ),
                vol.Optional(
                    CONF_JAMMING_MIN_DEVICES,
                    default=DEFAULT_JAMMING_MIN_DEVICES,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=20,
                    ),
                ),
                vol.Optional(
                    CONF_JAMMING_MIN_PERCENT,
                    default=DEFAULT_JAMMING_MIN_PERCENT,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10,
                        max=100,
                        unit_of_measurement="%",
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="timing",
            data_schema=data_schema,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AlarmGuardianOptionsFlow:
        """Get the options flow for this handler."""
        return AlarmGuardianOptionsFlow(config_entry)


class AlarmGuardianOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Alarm Guardian."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow.
        
        NOTE: self.config_entry is provided by parent class OptionsFlow.
        We don't need to set it manually.
        """
        # ✅ FIX ERRORE 1: Non assegnare self.config_entry
        # È già disponibile come property read-only dalla classe base
        pass

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage the options - allow editing all parameters."""
        if user_input is not None:
            # Update both options and data (for backward compatibility)
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **user_input}
            )
            return self.async_create_entry(title="", data=user_input)

        # Get current values from options or fall back to config data
        def get_value(key, default):
            """Get value from options or data with fallback."""
            if key in self.config_entry.options:
                return self.config_entry.options[key]
            elif key in self.config_entry.data:
                return self.config_entry.data[key]
            return default

        # Try to get Telegram bots for dropdown
        telegram_bots = get_telegram_bot_config_entries(self.hass)
        current_config_entry = get_value(CONF_TELEGRAM_CONFIG_ENTRY, "")
        
        # Build Telegram configuration fields
        telegram_fields = {}
        
        if telegram_bots:
            # Dropdown mode if bots found
            bot_options = [
                selector.SelectOptionDict(value=entry_id, label=bot_name)
                for entry_id, bot_name in telegram_bots.items()
            ]
            
            telegram_fields[
                vol.Optional(
                    CONF_TELEGRAM_CONFIG_ENTRY,
                    default=get_value(CONF_TELEGRAM_CONFIG_ENTRY, ""),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=bot_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                ),
            )
            
            # Try to get chat IDs for current bot
            if current_config_entry:
                allowed_chat_ids = await get_telegram_allowed_chat_ids(self.hass, current_config_entry)
                if allowed_chat_ids:
                    chat_options = [
                        selector.SelectOptionDict(value=chat_id, label=f"Chat ID: {chat_id}")
                        for chat_id in allowed_chat_ids
                    ]
                    
                    telegram_fields[
                        vol.Optional(
                            CONF_TELEGRAM_TARGET,
                            default=get_value(CONF_TELEGRAM_TARGET, ""),
                        )
                    ] = selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=chat_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            custom_value=True,  # ← NUOVO! Permette input manuale
                        ),
                    )
                else:
                    # No chat IDs, use text input
                    telegram_fields[
                        vol.Optional(
                            CONF_TELEGRAM_TARGET,
                            default=get_value(CONF_TELEGRAM_TARGET, ""),
                        )
                    ] = cv.string
        else:
            # Manual input mode if no bots found
            telegram_fields[
                vol.Optional(
                    CONF_TELEGRAM_CONFIG_ENTRY,
                    default=get_value(CONF_TELEGRAM_CONFIG_ENTRY, ""),
                )
            ] = cv.string
            
            telegram_fields[
                vol.Optional(
                    CONF_TELEGRAM_TARGET,
                    default=get_value(CONF_TELEGRAM_TARGET, ""),
                )
            ] = cv.string
        
        # Thread ID always manual
        telegram_fields[
            vol.Optional(
                CONF_TELEGRAM_THREAD_ID,
                default=get_value(CONF_TELEGRAM_THREAD_ID, ""),
            )
        ] = cv.string

        # Build comprehensive options schema
        schema_dict = {
            # ================================================================
            # SENSORS CONFIGURATION (can be modified after initial setup)
            # ================================================================
            vol.Optional(
                CONF_PERIMETER_SENSORS,
                default=get_value(CONF_PERIMETER_SENSORS, []),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="binary_sensor",
                    multiple=True,
                ),
            ),
            vol.Optional(
                CONF_INTERIOR_SENSORS,
                default=get_value(CONF_INTERIOR_SENSORS, []),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="binary_sensor",
                    multiple=True,
                ),
            ),
            
            # ================================================================
            # TELEGRAM CONFIGURATION
            # ================================================================
            **telegram_fields,
            
            # Timing parameters
            vol.Optional(
                CONF_CORRELATION_WINDOW,
                default=get_value(CONF_CORRELATION_WINDOW, DEFAULT_CORRELATION_WINDOW),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10,
                    max=300,
                    unit_of_measurement="seconds",
                ),
            ),
            vol.Optional(
                CONF_VOIP_CALL_DELAY,
                default=get_value(CONF_VOIP_CALL_DELAY, DEFAULT_VOIP_CALL_DELAY),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=20,
                    max=180,
                    unit_of_measurement="seconds",
                ),
            ),
            vol.Optional(
                CONF_BATTERY_THRESHOLD,
                default=get_value(CONF_BATTERY_THRESHOLD, DEFAULT_BATTERY_THRESHOLD),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5,
                    max=50,
                    unit_of_measurement="%",
                ),
            ),
            vol.Optional(
                CONF_JAMMING_MIN_DEVICES,
                default=get_value(CONF_JAMMING_MIN_DEVICES, DEFAULT_JAMMING_MIN_DEVICES),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=20,
                ),
            ),
            vol.Optional(
                CONF_JAMMING_MIN_PERCENT,
                default=get_value(CONF_JAMMING_MIN_PERCENT, DEFAULT_JAMMING_MIN_PERCENT),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10,
                    max=100,
                    unit_of_measurement="%",
                ),
            ),
        }

        data_schema = vol.Schema(schema_dict)

        return self.async_show_form(step_id="init", data_schema=data_schema)

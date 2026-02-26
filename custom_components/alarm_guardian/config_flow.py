"""Config flow for Alarm Guardian integration."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers import entity_registry as er, device_registry as dr, area_registry as ar
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_TUYA_ALARM_ENTITY,
    CONF_TELEGRAM_CONFIG_ENTRY,
    CONF_TELEGRAM_TARGET,
    CONF_TELEGRAM_THREAD_ID,
    CONF_VOIP_PRIMARY,
    CONF_VOIP_SECONDARY,
    CONF_SHELL_COMMAND_VOIP,
    CONF_ENTRY_DELAY,
    CONF_EXIT_DELAY,
    CONF_CORRELATION_WINDOW,
    CONF_VOIP_CALL_DELAY,
    CONF_BATTERY_THRESHOLD,
    CONF_JAMMING_MIN_DEVICES,
    CONF_JAMMING_MIN_PERCENT,
    CONF_EXTERNAL_SIREN,
    CONF_VOIP_PROVIDER_TYPE,
    CONF_VOIP_NOTIFY_SERVICE,
    CONF_VOIP_REST_URL,
    CONF_VOIP_REST_METHOD,
    CONF_VOIP_REST_HEADERS,
    CONF_VOIP_REST_BODY,
    CONF_FRIGATE_HOST,
    CONF_FRIGATE_PORT,
    CONF_FRIGATE_MOTION_SWITCHES,
    CONF_FRIGATE_DETECT_SWITCHES,
    CONF_ZONES,
    ZONE_ID,
    ZONE_NAME,
    ZONE_HA_AREAS,
    ZONE_PERIMETER_SENSORS,
    ZONE_INTERIOR_SENSORS,
    ZONE_FRIGATE_CAMERAS,
    ZONE_INTERIOR_SENSORS_BOTH,
    ZONE_INTERIOR_SENSORS_AWAY,
    ZONE_INTERIOR_SENSORS_HOME,
    ZONE_FRIGATE_CAMERAS_BOTH,
    ZONE_FRIGATE_CAMERAS_AWAY,
    ZONE_FRIGATE_CAMERAS_HOME,
    ZONE_PROFILE,
    ZONE_ARMED_MODES,
    ZONE_PROFILES,
    ZONE_PROFILE_PERIMETER_ONLY,
    ZONE_PROFILE_PERIMETER_PLUS,
    ZONE_PROFILE_RICH,
    ZONE_PROFILE_VOLUMETRIC_DIVERSE,
    DEFAULT_ENTRY_DELAY,
    DEFAULT_EXIT_DELAY,
    DEFAULT_CORRELATION_WINDOW,
    DEFAULT_VOIP_CALL_DELAY,
    DEFAULT_BATTERY_THRESHOLD,
    DEFAULT_JAMMING_MIN_DEVICES,
    DEFAULT_JAMMING_MIN_PERCENT,
    DEFAULT_FRIGATE_HOST,
    DEFAULT_FRIGATE_PORT,
    DEFAULT_TELEGRAM_CONFIG_ENTRY,
    DEFAULT_TELEGRAM_TARGET,
    VOIP_PROVIDER_SHELL,
    VOIP_PROVIDER_NOTIFY,
    VOIP_PROVIDER_REST,
    VOIP_PROVIDER_DISABLED,
    FP300_SUFFIXES,
)

_LOGGER = logging.getLogger(__name__)

# ── Helpers Telegram ───────────────────────────────────────────────────────

def get_telegram_bot_config_entries(hass: HomeAssistant) -> dict[str, str]:
    """Restituisce bot Telegram disponibili: UI e YAML/polling."""
    bots = {}
    try:
        for entry in hass.config_entries.async_entries("telegram_bot"):
            name = entry.title or entry.data.get("username", entry.entry_id[:8])
            bots[entry.entry_id] = f"{name} ({entry.entry_id[:8]}...)"
    except Exception as err:
        _LOGGER.warning("Errore lettura bot Telegram UI: %s", err)
    if not bots:
        try:
            if hass.data.get("telegram_bot"):
                bots["__yaml__"] = "Bot Telegram (YAML/Polling)"
        except Exception:
            pass
    return bots


def get_telegram_chat_ids_from_yaml(hass: HomeAssistant) -> list[str]:
    """Legge allowed_chat_ids dal bot YAML."""
    chat_ids = []
    try:
        tb_data = hass.data.get("telegram_bot")
        if tb_data:
            if isinstance(tb_data, dict):
                for value in tb_data.values():
                    if hasattr(value, "allowed_chat_ids"):
                        chat_ids.extend([str(i) for i in value.allowed_chat_ids])
                    elif isinstance(value, dict):
                        chat_ids.extend([str(i) for i in value.get("allowed_chat_ids", [])])
            elif hasattr(tb_data, "allowed_chat_ids"):
                chat_ids.extend([str(i) for i in tb_data.allowed_chat_ids])
    except Exception as err:
        _LOGGER.debug("Chat IDs YAML: %s", err)
    # Deduplica
    seen, unique = set(), []
    for c in chat_ids:
        if c not in seen:
            seen.add(c); unique.append(c)
    return unique


async def get_telegram_allowed_chat_ids(hass: HomeAssistant, config_entry_id: str) -> list[str]:
    """Restituisce chat IDs per un bot (UI o YAML)."""
    if config_entry_id == "__yaml__":
        return get_telegram_chat_ids_from_yaml(hass)
    chat_ids = []
    try:
        for entry in hass.config_entries.async_entries("telegram_bot"):
            if entry.entry_id == config_entry_id:
                chat_ids = [str(c) for c in entry.data.get("allowed_chat_ids", [])]
                break
    except Exception as err:
        _LOGGER.warning("Chat IDs UI: %s", err)
    return chat_ids


# ── Helper: auto-classifica sensori da aree HA ────────────────────────────

def _get_frigate_cameras_from_switches(hass: HomeAssistant, entry_data: dict) -> list[str]:
    """Estrae i nomi delle telecamere Frigate dagli switch già configurati.
    
    Gli switch `switch.{camera}_detect` e `switch.{camera}_motion` sono stati
    selezionati nello step Frigate globale — il nome telecamera si ottiene
    strippando il suffisso.
    """
    cameras: list[str] = []
    seen: set[str] = set()

    detect_switches = entry_data.get(CONF_FRIGATE_DETECT_SWITCHES, [])
    motion_switches = entry_data.get(CONF_FRIGATE_MOTION_SWITCHES, [])

    for switch_id in detect_switches + motion_switches:
        # switch.giardino_detect → giardino
        # switch.ingresso_motion → ingresso
        name = switch_id.replace("switch.", "")
        for suffix in ("_detect", "_motion"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        if name and name not in seen:
            seen.add(name)
            cameras.append(name)

    return cameras


def _classify_sensors_from_areas(
    hass: HomeAssistant, area_ids: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """Restituisce (perimeter, interior, frigate_cameras) dai device nelle aree selezionate.

    Classificazione automatica basata su device_class:
    - door / window / garage_door / opening → perimetrale
    - motion / occupancy / presence         → interno
    I FP300 (_pir_detection, _presence) vanno negli interni.
    Le telecamere Frigate vengono lette dagli switch globali, non dalle aree.
    """
    perimeter, interior = [], []

    try:
        entity_registry = er.async_get(hass)
        device_registry = dr.async_get(hass)
    except Exception:
        return perimeter, interior, []

    perimeter_classes = {"door", "window", "garage_door", "opening", "lock"}
    interior_classes = {"motion", "occupancy", "presence"}

    for entity in entity_registry.entities.values():
        if entity.domain != "binary_sensor":
            continue
        if not entity.area_id and entity.device_id:
            device = device_registry.async_get(entity.device_id)
            if not device or device.area_id not in area_ids:
                continue
        elif entity.area_id not in area_ids:
            continue

        eid = entity.entity_id
        dc = (entity.original_device_class or "").lower()

        if dc in perimeter_classes:
            perimeter.append(eid)
        elif dc in interior_classes or any(s in eid for s in FP300_SUFFIXES):
            interior.append(eid)

    return perimeter, interior, []  # telecamere gestite separatamente


def _suggest_profile(
    perimeter: list[str], interior: list[str], cameras: list[str]
) -> str:
    """Suggerisce il profilo di conferma in base ai sensori disponibili."""
    if cameras:
        return ZONE_PROFILE_RICH
    if perimeter and interior:
        return ZONE_PROFILE_PERIMETER_PLUS
    if perimeter and not interior:
        return ZONE_PROFILE_PERIMETER_ONLY
    return ZONE_PROFILE_VOLUMETRIC_DIVERSE


# ═══════════════════════════════════════════════════════════════════════════
# CONFIG FLOW
# ═══════════════════════════════════════════════════════════════════════════

class AlarmGuardianConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Flusso di configurazione Alarm Guardian.

    Ordine:
    1. Tuya
    2. Telegram
    3. VoIP (numeri + provider)
    4. Timing
    5. Frigate (globale: host/port/switch)
    6. Sirena esterna
    ── LOOP ZONE ──
    Z1. Nome zona + aree HA
    Z2. Device rilevati (perimetro / interni / cam) — modifica manuale
    Z3. Profilo conferma + modalità armo
    ZX. "Aggiungere un'altra zona?"
    """

    VERSION = 3

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self._zones: list[dict[str, Any]] = []
        self._current_zone: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: Tuya alarm panel."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_telegram()

        alarm_entities = [
            e.entity_id
            for e in self.hass.states.async_all()
            if e.entity_id.startswith("alarm_control_panel.")
        ]
        if not alarm_entities:
            return self.async_abort(reason="no_alarm_panel")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_TUYA_ALARM_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="alarm_control_panel")
                ),
            }),
            errors=errors,
        )

    async def async_step_telegram(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2: Telegram bot e chat ID."""
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_voip()

        bots = get_telegram_bot_config_entries(self.hass)
        if not bots:
            schema = vol.Schema({
                vol.Optional(CONF_TELEGRAM_CONFIG_ENTRY, default=""): cv.string,
                vol.Optional(CONF_TELEGRAM_TARGET, default=""): cv.string,
                vol.Optional(CONF_TELEGRAM_THREAD_ID): cv.string,
            })
        else:
            bot_options = [selector.SelectOptionDict(value=k, label=v) for k, v in bots.items()]
            first_bot = next(iter(bots))
            chat_ids = await get_telegram_allowed_chat_ids(self.hass, first_bot)
            if chat_ids:
                chat_selector = selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[selector.SelectOptionDict(value=c, label=c) for c in chat_ids],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                )
            else:
                chat_selector = selector.TextSelector()

            schema = vol.Schema({
                vol.Required(CONF_TELEGRAM_CONFIG_ENTRY, default=first_bot): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=bot_options, mode=selector.SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_TELEGRAM_TARGET, default=chat_ids[0] if chat_ids else ""): chat_selector,
                vol.Optional(CONF_TELEGRAM_THREAD_ID): cv.string,
            })

        return self.async_show_form(step_id="telegram", data_schema=schema)

    async def async_step_voip(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 3: VoIP — numeri e provider."""
        if user_input is not None:
            self.data.update(user_input)
            provider = user_input.get(CONF_VOIP_PROVIDER_TYPE, VOIP_PROVIDER_SHELL)
            if provider == VOIP_PROVIDER_NOTIFY:
                return await self.async_step_voip_notify()
            if provider == VOIP_PROVIDER_REST:
                return await self.async_step_voip_rest()
            return await self.async_step_timing()

        shell_cmds = []
        try:
            svcs = self.hass.services.async_services()
            shell_cmds = list(svcs.get("shell_command", {}).keys())
        except Exception:
            pass

        schema = vol.Schema({
            vol.Required(CONF_VOIP_PRIMARY): cv.string,
            vol.Optional(CONF_VOIP_SECONDARY): cv.string,
            vol.Required(CONF_VOIP_PROVIDER_TYPE, default=VOIP_PROVIDER_SHELL): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=VOIP_PROVIDER_SHELL),
                        selector.SelectOptionDict(value=VOIP_PROVIDER_NOTIFY),
                        selector.SelectOptionDict(value=VOIP_PROVIDER_REST),
                        selector.SelectOptionDict(value=VOIP_PROVIDER_DISABLED),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_SHELL_COMMAND_VOIP, default="asterisk_call"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=shell_cmds if shell_cmds else ["asterisk_call"],
                    custom_value=True,
                )
            ),
        })
        return self.async_show_form(step_id="voip", data_schema=schema)

    async def async_step_voip_notify(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_timing()
        notify_svcs = []
        try:
            svcs = self.hass.services.async_services()
            notify_svcs = [f"notify.{k}" for k in svcs.get("notify", {}).keys()]
        except Exception:
            pass
        schema = vol.Schema({
            vol.Required(CONF_VOIP_NOTIFY_SERVICE): selector.SelectSelector(
                selector.SelectSelectorConfig(options=notify_svcs if notify_svcs else ["notify.voip"], custom_value=True)
            ),
        })
        return self.async_show_form(step_id="voip_notify", data_schema=schema)

    async def async_step_voip_rest(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_timing()
        schema = vol.Schema({
            vol.Required(CONF_VOIP_REST_URL): cv.string,
            vol.Optional(CONF_VOIP_REST_METHOD, default="POST"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["POST", "GET", "PUT"], mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional(CONF_VOIP_REST_HEADERS, default="{}"): cv.string,
            vol.Optional(CONF_VOIP_REST_BODY, default='{"number": "{number}"}'): cv.string,
        })
        return self.async_show_form(step_id="voip_rest", data_schema=schema)

    async def async_step_timing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 4: Timing globale."""
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_frigate()
        schema = vol.Schema({
            vol.Optional(CONF_ENTRY_DELAY, default=DEFAULT_ENTRY_DELAY): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=5, unit_of_measurement="s", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_EXIT_DELAY, default=DEFAULT_EXIT_DELAY): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=5, unit_of_measurement="s", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_CORRELATION_WINDOW, default=DEFAULT_CORRELATION_WINDOW): selector.NumberSelector(
                selector.NumberSelectorConfig(min=15, max=300, step=15, unit_of_measurement="s", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_VOIP_CALL_DELAY, default=DEFAULT_VOIP_CALL_DELAY): selector.NumberSelector(
                selector.NumberSelectorConfig(min=30, max=300, step=30, unit_of_measurement="s", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_BATTERY_THRESHOLD, default=DEFAULT_BATTERY_THRESHOLD): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=50, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_JAMMING_MIN_DEVICES, default=DEFAULT_JAMMING_MIN_DEVICES): vol.Coerce(int),
            vol.Optional(CONF_JAMMING_MIN_PERCENT, default=DEFAULT_JAMMING_MIN_PERCENT): vol.Coerce(int),
        })
        return self.async_show_form(step_id="timing", data_schema=schema)

    async def async_step_frigate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 5: Frigate globale (host, port, switch on/off)."""
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_siren()
        schema = vol.Schema({
            vol.Optional(CONF_FRIGATE_HOST, default=DEFAULT_FRIGATE_HOST): cv.string,
            vol.Optional(CONF_FRIGATE_PORT, default=DEFAULT_FRIGATE_PORT): vol.Coerce(int),
            vol.Optional(CONF_FRIGATE_MOTION_SWITCHES, default=[]): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch", multiple=True)
            ),
            vol.Optional(CONF_FRIGATE_DETECT_SWITCHES, default=[]): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch", multiple=True)
            ),
        })
        return self.async_show_form(step_id="frigate", data_schema=schema)

    async def async_step_siren(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 6: Sirena esterna opzionale."""
        if user_input is not None:
            self.data.update(user_input)
            # Avvia il loop zone
            return await self.async_step_zone_name()
        schema = vol.Schema({
            vol.Optional(CONF_EXTERNAL_SIREN): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="siren")
            ),
        })
        return self.async_show_form(step_id="siren", data_schema=schema)

    # ── LOOP ZONE ────────────────────────────────────────────────────────────

    async def async_step_zone_name(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Z1: Nome zona e aree HA."""
        if user_input is not None:
            self._current_zone = {
                ZONE_ID: str(uuid.uuid4()),
                ZONE_NAME: user_input["zone_name"],
                ZONE_HA_AREAS: user_input.get("zone_ha_areas", []),
            }
            return await self.async_step_zone_devices()

        # Recupera aree HA disponibili
        try:
            area_reg = ar.async_get(self.hass)
            area_options = [
                selector.SelectOptionDict(value=a.id, label=a.name)
                for a in area_reg.async_list_areas()
            ]
        except Exception:
            area_options = []

        zone_num = len(self._zones) + 1
        schema = vol.Schema({
            vol.Required("zone_name", default=f"Zona {zone_num}"): cv.string,
            vol.Optional("zone_ha_areas", default=[]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=area_options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(
            step_id="zone_name",
            data_schema=schema,
            description_placeholders={"zone_num": str(zone_num)},
        )

    async def async_step_zone_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Z2a: Sensori perimetrali e interni attivi in entrambe le modalità (home + away)."""
        if user_input is not None:
            self._current_zone[ZONE_PERIMETER_SENSORS] = user_input.get("perimeter_sensors", [])
            self._current_zone[ZONE_INTERIOR_SENSORS_BOTH] = user_input.get("interior_sensors_both", [])
            self._current_zone[ZONE_FRIGATE_CAMERAS_BOTH] = user_input.get("frigate_cameras_both", [])
            return await self.async_step_zone_devices_away()

        area_ids = self._current_zone.get(ZONE_HA_AREAS, [])
        auto_perim, auto_int, _ = _classify_sensors_from_areas(self.hass, area_ids)
        available_cameras = _get_frigate_cameras_from_switches(self.hass, self.data)
        cam_options = [selector.SelectOptionDict(value=c, label=c) for c in available_cameras]

        schema = vol.Schema({
            vol.Optional("perimeter_sensors", default=auto_perim): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Optional("interior_sensors_both", default=[]): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Optional("frigate_cameras_both", default=[]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=cam_options if cam_options else [],
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(
            step_id="zone_devices",
            data_schema=schema,
            description_placeholders={"zone_name": self._current_zone.get(ZONE_NAME, "")},
        )

    async def async_step_zone_devices_away(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Z2b: Sensori attivi SOLO quando fuori casa (armed_away)."""
        if user_input is not None:
            self._current_zone[ZONE_INTERIOR_SENSORS_AWAY] = user_input.get("interior_sensors_away", [])
            self._current_zone[ZONE_FRIGATE_CAMERAS_AWAY] = user_input.get("frigate_cameras_away", [])
            return await self.async_step_zone_profile()

        available_cameras = _get_frigate_cameras_from_switches(self.hass, self.data)
        # Escludi telecamere già scelte come "both"
        already_both = set(self._current_zone.get(ZONE_FRIGATE_CAMERAS_BOTH, []))
        cam_options = [
            selector.SelectOptionDict(value=c, label=c)
            for c in available_cameras if c not in already_both
        ]

        schema = vol.Schema({
            vol.Optional("interior_sensors_away", default=[]): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Optional("frigate_cameras_away", default=[]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=cam_options if cam_options else [],
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(
            step_id="zone_devices_away",
            data_schema=schema,
            description_placeholders={"zone_name": self._current_zone.get(ZONE_NAME, "")},
        )

    async def async_step_zone_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Z3: Profilo di conferma e modalità armo."""
        if user_input is not None:
            self._current_zone[ZONE_PROFILE] = user_input[ZONE_PROFILE]
            self._current_zone[ZONE_ARMED_MODES] = user_input[ZONE_ARMED_MODES]
            self._zones.append(self._current_zone)
            self._current_zone = {}
            return await self.async_step_zone_add_another()

        # Suggerisci profilo automaticamente
        all_interior = (
            self._current_zone.get(ZONE_INTERIOR_SENSORS_BOTH, [])
            + self._current_zone.get(ZONE_INTERIOR_SENSORS_AWAY, [])
            + self._current_zone.get(ZONE_INTERIOR_SENSORS_HOME, [])
        )
        all_cameras = (
            self._current_zone.get(ZONE_FRIGATE_CAMERAS_BOTH, [])
            + self._current_zone.get(ZONE_FRIGATE_CAMERAS_AWAY, [])
            + self._current_zone.get(ZONE_FRIGATE_CAMERAS_HOME, [])
        )
        suggested = _suggest_profile(
            self._current_zone.get(ZONE_PERIMETER_SENSORS, []),
            all_interior,
            all_cameras,
        )

        schema = vol.Schema({
            vol.Required(ZONE_PROFILE, default=suggested): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=ZONE_PROFILE_PERIMETER_ONLY),
                        selector.SelectOptionDict(value=ZONE_PROFILE_PERIMETER_PLUS),
                        selector.SelectOptionDict(value=ZONE_PROFILE_RICH),
                        selector.SelectOptionDict(value=ZONE_PROFILE_VOLUMETRIC_DIVERSE),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(ZONE_ARMED_MODES, default=["armed_away"]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="armed_away"),
                        selector.SelectOptionDict(value="armed_home"),
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        })
        return self.async_show_form(
            step_id="zone_profile",
            data_schema=schema,
            description_placeholders={"zone_name": self._current_zone.get(ZONE_NAME, "")},
        )

    async def async_step_zone_add_another(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """ZX: Aggiungere un'altra zona?"""
        if user_input is not None:
            if user_input.get("add_another"):
                return await self.async_step_zone_name()
            # Fine: crea config entry
            self.data[CONF_ZONES] = self._zones
            return self.async_create_entry(title="Alarm Guardian", data=self.data)

        zones_summary = ", ".join(z[ZONE_NAME] for z in self._zones)
        schema = vol.Schema({
            vol.Required("add_another", default=False): selector.BooleanSelector(),
        })
        return self.async_show_form(
            step_id="zone_add_another",
            data_schema=schema,
            description_placeholders={"zones_configured": zones_summary},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return AlarmGuardianOptionsFlow(config_entry)


# ═══════════════════════════════════════════════════════════════════════════
# OPTIONS FLOW
# ═══════════════════════════════════════════════════════════════════════════

class AlarmGuardianOptionsFlow(config_entries.OptionsFlow):
    """Options flow: modifica impostazioni globali e zone."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        self._pending: dict[str, Any] = {}
        self._zones: list[dict[str, Any]] = list(
            config_entry.options.get(CONF_ZONES)
            or config_entry.data.get(CONF_ZONES, [])
        )
        self._editing_zone_idx: int | None = None
        self._current_zone: dict[str, Any] = {}

    def _get(self, key: str, default: Any = None) -> Any:
        return self._entry.options.get(key, self._entry.data.get(key, default))

    def _save(self, new_data: dict[str, Any]) -> config_entries.ConfigFlowResult:
        merged = {**self._entry.data, **self._entry.options, **new_data}
        return self.async_create_entry(title="", data=merged)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Menu principale opzioni."""
        if user_input is not None:
            section = user_input.get("section")
            if section == "notifications":
                return await self.async_step_opt_notifications()
            if section == "voip":
                return await self.async_step_opt_voip()
            if section == "timing":
                return await self.async_step_opt_timing()
            if section == "frigate":
                return await self.async_step_opt_frigate()
            if section == "siren":
                return await self.async_step_opt_siren()
            if section == "zones":
                return await self.async_step_opt_zones()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("section"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="notifications"),
                            selector.SelectOptionDict(value="voip"),
                            selector.SelectOptionDict(value="timing"),
                            selector.SelectOptionDict(value="frigate"),
                            selector.SelectOptionDict(value="siren"),
                            selector.SelectOptionDict(value="zones"),
                        ],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            }),
        )

    async def async_step_opt_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        bots = get_telegram_bot_config_entries(self.hass)
        current_bot = self._get(CONF_TELEGRAM_CONFIG_ENTRY, "")
        chat_ids = await get_telegram_allowed_chat_ids(self.hass, current_bot) if current_bot else []
        if bots:
            bot_opts = [selector.SelectOptionDict(value=k, label=v) for k, v in bots.items()]
            bot_sel = selector.SelectSelector(selector.SelectSelectorConfig(options=bot_opts, mode=selector.SelectSelectorMode.DROPDOWN))
        else:
            bot_sel = selector.TextSelector()
        if chat_ids:
            chat_sel = selector.SelectSelector(selector.SelectSelectorConfig(
                options=[selector.SelectOptionDict(value=c, label=c) for c in chat_ids],
                mode=selector.SelectSelectorMode.DROPDOWN, custom_value=True,
            ))
        else:
            chat_sel = selector.TextSelector()
        schema = vol.Schema({
            vol.Optional(CONF_TELEGRAM_CONFIG_ENTRY, default=current_bot): bot_sel,
            vol.Optional(CONF_TELEGRAM_TARGET, default=self._get(CONF_TELEGRAM_TARGET, "")): chat_sel,
            vol.Optional(CONF_TELEGRAM_THREAD_ID, default=self._get(CONF_TELEGRAM_THREAD_ID, "")): cv.string,
        })
        return self.async_show_form(step_id="opt_notifications", data_schema=schema)

    async def async_step_opt_voip(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._pending.update(user_input)
            provider = user_input.get(CONF_VOIP_PROVIDER_TYPE, VOIP_PROVIDER_SHELL)
            if provider == VOIP_PROVIDER_NOTIFY:
                return await self.async_step_opt_voip_notify()
            if provider == VOIP_PROVIDER_REST:
                return await self.async_step_opt_voip_rest()
            return self._save(self._pending)
        shell_cmds = list(self.hass.services.async_services().get("shell_command", {}).keys()) or ["asterisk_call"]
        schema = vol.Schema({
            vol.Required(CONF_VOIP_PRIMARY, default=self._get(CONF_VOIP_PRIMARY, "")): cv.string,
            vol.Optional(CONF_VOIP_SECONDARY, default=self._get(CONF_VOIP_SECONDARY, "")): cv.string,
            vol.Required(CONF_VOIP_PROVIDER_TYPE, default=self._get(CONF_VOIP_PROVIDER_TYPE, VOIP_PROVIDER_SHELL)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=VOIP_PROVIDER_SHELL),
                        selector.SelectOptionDict(value=VOIP_PROVIDER_NOTIFY),
                        selector.SelectOptionDict(value=VOIP_PROVIDER_REST),
                        selector.SelectOptionDict(value=VOIP_PROVIDER_DISABLED),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_SHELL_COMMAND_VOIP, default=self._get(CONF_SHELL_COMMAND_VOIP, "asterisk_call")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=shell_cmds, custom_value=True)
            ),
        })
        return self.async_show_form(step_id="opt_voip", data_schema=schema)

    async def async_step_opt_voip_notify(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._pending.update(user_input)
            return self._save(self._pending)
        notify_svcs = [f"notify.{k}" for k in self.hass.services.async_services().get("notify", {}).keys()] or ["notify.voip"]
        schema = vol.Schema({
            vol.Required(CONF_VOIP_NOTIFY_SERVICE, default=self._get(CONF_VOIP_NOTIFY_SERVICE, "")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=notify_svcs, custom_value=True)
            ),
        })
        return self.async_show_form(step_id="opt_voip_notify", data_schema=schema)

    async def async_step_opt_voip_rest(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._pending.update(user_input)
            return self._save(self._pending)
        schema = vol.Schema({
            vol.Required(CONF_VOIP_REST_URL, default=self._get(CONF_VOIP_REST_URL, "")): cv.string,
            vol.Optional(CONF_VOIP_REST_METHOD, default=self._get(CONF_VOIP_REST_METHOD, "POST")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["POST", "GET", "PUT"], mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional(CONF_VOIP_REST_HEADERS, default=self._get(CONF_VOIP_REST_HEADERS, "{}")): cv.string,
            vol.Optional(CONF_VOIP_REST_BODY, default=self._get(CONF_VOIP_REST_BODY, '{"number": "{number"}')): cv.string,
        })
        return self.async_show_form(step_id="opt_voip_rest", data_schema=schema)

    async def async_step_opt_timing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        schema = vol.Schema({
            vol.Optional(CONF_ENTRY_DELAY, default=self._get(CONF_ENTRY_DELAY, DEFAULT_ENTRY_DELAY)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=5, unit_of_measurement="s", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_EXIT_DELAY, default=self._get(CONF_EXIT_DELAY, DEFAULT_EXIT_DELAY)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=5, unit_of_measurement="s", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_CORRELATION_WINDOW, default=self._get(CONF_CORRELATION_WINDOW, DEFAULT_CORRELATION_WINDOW)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=15, max=300, step=15, unit_of_measurement="s", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_VOIP_CALL_DELAY, default=self._get(CONF_VOIP_CALL_DELAY, DEFAULT_VOIP_CALL_DELAY)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=30, max=300, step=30, unit_of_measurement="s", mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_BATTERY_THRESHOLD, default=self._get(CONF_BATTERY_THRESHOLD, DEFAULT_BATTERY_THRESHOLD)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=50, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.SLIDER)
            ),
        })
        return self.async_show_form(step_id="opt_timing", data_schema=schema)

    async def async_step_opt_frigate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        schema = vol.Schema({
            vol.Optional(CONF_FRIGATE_HOST, default=self._get(CONF_FRIGATE_HOST, DEFAULT_FRIGATE_HOST)): cv.string,
            vol.Optional(CONF_FRIGATE_PORT, default=self._get(CONF_FRIGATE_PORT, DEFAULT_FRIGATE_PORT)): vol.Coerce(int),
            vol.Optional(CONF_FRIGATE_MOTION_SWITCHES, default=self._get(CONF_FRIGATE_MOTION_SWITCHES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch", multiple=True)
            ),
            vol.Optional(CONF_FRIGATE_DETECT_SWITCHES, default=self._get(CONF_FRIGATE_DETECT_SWITCHES, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch", multiple=True)
            ),
        })
        return self.async_show_form(step_id="opt_frigate", data_schema=schema)

    async def async_step_opt_siren(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        schema = vol.Schema({
            vol.Optional(CONF_EXTERNAL_SIREN, default=self._get(CONF_EXTERNAL_SIREN, "")): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="siren")
            ),
        })
        return self.async_show_form(step_id="opt_siren", data_schema=schema)

    # ── Gestione zone nell'options flow ──────────────────────────────────────

    async def async_step_opt_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Menu zone: lista esistenti + azioni."""
        if user_input is not None:
            action = user_input.get("zone_action")
            if action == "add":
                self._editing_zone_idx = None
                self._current_zone = {}
                return await self.async_step_zone_name()
            if action and action.startswith("edit:"):
                idx = int(action.split(":")[1])
                self._editing_zone_idx = idx
                self._current_zone = dict(self._zones[idx])
                return await self.async_step_zone_devices()
            if action and action.startswith("delete:"):
                idx = int(action.split(":")[1])
                self._zones.pop(idx)
                merged = {**self._entry.data, **self._entry.options, CONF_ZONES: self._zones}
                return self.async_create_entry(title="", data=merged)

        zone_options = [selector.SelectOptionDict(value="add")]
        for i, z in enumerate(self._zones):
            zone_options.append(selector.SelectOptionDict(value=f"edit:{i}", label=f"✏️ Modifica: {z[ZONE_NAME]}"))
            zone_options.append(selector.SelectOptionDict(value=f"delete:{i}", label=f"🗑️ Elimina: {z[ZONE_NAME]}"))

        schema = vol.Schema({
            vol.Required("zone_action"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=zone_options, mode=selector.SelectSelectorMode.LIST)
            )
        })
        return self.async_show_form(step_id="opt_zones", data_schema=schema)

    async def async_step_zone_name(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Options: nome e aree zona (add/edit)."""
        if user_input is not None:
            self._current_zone[ZONE_NAME] = user_input["zone_name"]
            self._current_zone[ZONE_HA_AREAS] = user_input.get("zone_ha_areas", [])
            if ZONE_ID not in self._current_zone:
                self._current_zone[ZONE_ID] = str(uuid.uuid4())
            return await self.async_step_zone_devices()
        try:
            area_reg = ar.async_get(self.hass)
            area_options = [selector.SelectOptionDict(value=a.id, label=a.name) for a in area_reg.async_list_areas()]
        except Exception:
            area_options = []
        schema = vol.Schema({
            vol.Required("zone_name", default=self._current_zone.get(ZONE_NAME, f"Zona {len(self._zones)+1}")): cv.string,
            vol.Optional("zone_ha_areas", default=self._current_zone.get(ZONE_HA_AREAS, [])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=area_options, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
        })
        return self.async_show_form(step_id="zone_name", data_schema=schema)

    async def async_step_zone_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Options Z2a: Sensori attivi in entrambe le modalità (home + away)."""
        if user_input is not None:
            self._current_zone[ZONE_PERIMETER_SENSORS] = user_input.get("perimeter_sensors", [])
            self._current_zone[ZONE_INTERIOR_SENSORS_BOTH] = user_input.get("interior_sensors_both", [])
            self._current_zone[ZONE_FRIGATE_CAMERAS_BOTH] = user_input.get("frigate_cameras_both", [])
            return await self.async_step_zone_devices_away()

        area_ids = self._current_zone.get(ZONE_HA_AREAS, [])
        auto_p, auto_i, _ = _classify_sensors_from_areas(self.hass, area_ids)

        # Retrocompatibilità: se la zona era configurata con le chiavi legacy, prepopola _both
        default_int_both = self._current_zone.get(
            ZONE_INTERIOR_SENSORS_BOTH,
            self._current_zone.get(ZONE_INTERIOR_SENSORS, auto_i)
        )

        available_cameras = _get_frigate_cameras_from_switches(
            self.hass, {**self._entry.data, **self._entry.options}
        )
        cam_options = [selector.SelectOptionDict(value=c, label=c) for c in available_cameras]
        default_cams_both = self._current_zone.get(
            ZONE_FRIGATE_CAMERAS_BOTH,
            self._current_zone.get(ZONE_FRIGATE_CAMERAS, [])
        )

        schema = vol.Schema({
            vol.Optional("perimeter_sensors", default=self._current_zone.get(ZONE_PERIMETER_SENSORS, auto_p)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Optional("interior_sensors_both", default=default_int_both): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Optional("frigate_cameras_both", default=default_cams_both): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=cam_options if cam_options else [],
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(
            step_id="zone_devices",
            data_schema=schema,
            description_placeholders={"zone_name": self._current_zone.get(ZONE_NAME, "")},
        )

    async def async_step_zone_devices_away(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Options Z2b: Sensori attivi SOLO quando fuori casa (armed_away)."""
        if user_input is not None:
            self._current_zone[ZONE_INTERIOR_SENSORS_AWAY] = user_input.get("interior_sensors_away", [])
            self._current_zone[ZONE_FRIGATE_CAMERAS_AWAY] = user_input.get("frigate_cameras_away", [])
            return await self.async_step_zone_profile()

        available_cameras = _get_frigate_cameras_from_switches(
            self.hass, {**self._entry.data, **self._entry.options}
        )
        already_both = set(self._current_zone.get(ZONE_FRIGATE_CAMERAS_BOTH, []))
        cam_options = [
            selector.SelectOptionDict(value=c, label=c)
            for c in available_cameras if c not in already_both
        ]

        schema = vol.Schema({
            vol.Optional("interior_sensors_away", default=self._current_zone.get(ZONE_INTERIOR_SENSORS_AWAY, [])): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Optional("frigate_cameras_away", default=self._current_zone.get(ZONE_FRIGATE_CAMERAS_AWAY, [])): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=cam_options if cam_options else [],
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(
            step_id="zone_devices_away",
            data_schema=schema,
            description_placeholders={"zone_name": self._current_zone.get(ZONE_NAME, "")},
        )

    async def async_step_zone_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Options: profilo zona."""
        if user_input is not None:
            self._current_zone[ZONE_PROFILE] = user_input[ZONE_PROFILE]
            self._current_zone[ZONE_ARMED_MODES] = user_input[ZONE_ARMED_MODES]
            if self._editing_zone_idx is not None:
                self._zones[self._editing_zone_idx] = self._current_zone
            else:
                self._zones.append(self._current_zone)
            self._editing_zone_idx = None
            self._current_zone = {}
            merged = {**self._entry.data, **self._entry.options, CONF_ZONES: self._zones}
            return self.async_create_entry(title="", data=merged)

        all_interior = (
            self._current_zone.get(ZONE_INTERIOR_SENSORS_BOTH, [])
            + self._current_zone.get(ZONE_INTERIOR_SENSORS_AWAY, [])
            + self._current_zone.get(ZONE_INTERIOR_SENSORS_HOME, [])
        )
        all_cameras = (
            self._current_zone.get(ZONE_FRIGATE_CAMERAS_BOTH, [])
            + self._current_zone.get(ZONE_FRIGATE_CAMERAS_AWAY, [])
            + self._current_zone.get(ZONE_FRIGATE_CAMERAS_HOME, [])
        )
        suggested = _suggest_profile(
            self._current_zone.get(ZONE_PERIMETER_SENSORS, []),
            all_interior,
            all_cameras,
        )
        current_profile = self._current_zone.get(ZONE_PROFILE, suggested)
        current_modes = self._current_zone.get(ZONE_ARMED_MODES, ["armed_away"])

        schema = vol.Schema({
            vol.Required(ZONE_PROFILE, default=current_profile): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=ZONE_PROFILE_PERIMETER_ONLY),
                        selector.SelectOptionDict(value=ZONE_PROFILE_PERIMETER_PLUS),
                        selector.SelectOptionDict(value=ZONE_PROFILE_RICH),
                        selector.SelectOptionDict(value=ZONE_PROFILE_VOLUMETRIC_DIVERSE),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(ZONE_ARMED_MODES, default=current_modes): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="armed_away"),
                        selector.SelectOptionDict(value="armed_home"),
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        })
        return self.async_show_form(
            step_id="zone_profile",
            data_schema=schema,
            description_placeholders={"zone_name": self._current_zone.get(ZONE_NAME, "")},
        )

"""Constants for Alarm Guardian integration."""
from datetime import timedelta
from typing import Final

DOMAIN: Final = "alarm_guardian"

# ── Configurazione globale ──────────────────────────────────────────────────
CONF_TUYA_ALARM_ENTITY: Final = "tuya_alarm_entity"
CONF_TELEGRAM_CONFIG_ENTRY: Final = "telegram_config_entry_id"
CONF_TELEGRAM_TARGET: Final = "telegram_target_chat_id"
CONF_TELEGRAM_THREAD_ID: Final = "telegram_thread_id"
CONF_VOIP_PRIMARY: Final = "voip_primary_number"
CONF_VOIP_SECONDARY: Final = "voip_secondary_number"
CONF_SHELL_COMMAND_VOIP: Final = "shell_command_voip"
CONF_VOIP_PROVIDER_TYPE: Final = "voip_provider_type"
CONF_VOIP_NOTIFY_SERVICE: Final = "voip_notify_service"
CONF_VOIP_REST_URL: Final = "voip_rest_url"
CONF_VOIP_REST_METHOD: Final = "voip_rest_method"
CONF_VOIP_REST_HEADERS: Final = "voip_rest_headers"
CONF_VOIP_REST_BODY: Final = "voip_rest_body"
CONF_FRIGATE_HOST: Final = "frigate_host"
CONF_FRIGATE_PORT: Final = "frigate_port"
CONF_FRIGATE_MOTION_SWITCHES: Final = "frigate_motion_switches"
CONF_FRIGATE_DETECT_SWITCHES: Final = "frigate_detect_switches"
CONF_EXTERNAL_SIREN: Final = "external_siren_entity"
CONF_ENTRY_DELAY: Final = "entry_delay"
CONF_EXIT_DELAY: Final = "exit_delay"
CONF_CORRELATION_WINDOW: Final = "correlation_window"
CONF_VOIP_CALL_DELAY: Final = "voip_call_delay"
CONF_BATTERY_THRESHOLD: Final = "battery_threshold"
CONF_JAMMING_MIN_DEVICES: Final = "jamming_min_devices"
CONF_JAMMING_MIN_PERCENT: Final = "jamming_min_percent"

# ── Configurazione zone ─────────────────────────────────────────────────────
CONF_ZONES: Final = "zones"

# Chiavi di ogni zona (dict)
ZONE_ID: Final = "zone_id"
ZONE_NAME: Final = "zone_name"
ZONE_HA_AREAS: Final = "zone_ha_areas"
ZONE_PERIMETER_SENSORS: Final = "zone_perimeter_sensors"
ZONE_INTERIOR_SENSORS: Final = "zone_interior_sensors"
ZONE_FRIGATE_CAMERAS: Final = "zone_frigate_cameras"
ZONE_PROFILE: Final = "zone_profile"
ZONE_ARMED_MODES: Final = "zone_armed_modes"

# Profili di conferma zona
ZONE_PROFILE_PERIMETER_ONLY: Final = "perimeter_only"
ZONE_PROFILE_PERIMETER_PLUS: Final = "perimeter_plus"
ZONE_PROFILE_RICH: Final = "rich"
ZONE_PROFILE_VOLUMETRIC_DIVERSE: Final = "volumetric_diverse"

ZONE_PROFILES: Final = [
    ZONE_PROFILE_PERIMETER_ONLY,
    ZONE_PROFILE_PERIMETER_PLUS,
    ZONE_PROFILE_RICH,
    ZONE_PROFILE_VOLUMETRIC_DIVERSE,
]

# Soglia locale per profilo
ZONE_PROFILE_THRESHOLDS: Final = {
    ZONE_PROFILE_PERIMETER_ONLY: 140,
    ZONE_PROFILE_PERIMETER_PLUS: 100,
    ZONE_PROFILE_RICH: 100,
    ZONE_PROFILE_VOLUMETRIC_DIVERSE: 100,
}

# ── Deprecated (migration) ──────────────────────────────────────────────────
CONF_PERIMETER_SENSORS: Final = "perimeter_sensors"
CONF_INTERIOR_SENSORS: Final = "interior_sensors"
CONF_CONTACT_SENSORS: Final = "contact_sensors"
CONF_MOTION_SENSORS: Final = "motion_sensors"
CONF_FRIGATE_CAMERAS: Final = "frigate_cameras"
CONF_ARMING_DELAY: Final = "arming_delay"

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_ENTRY_DELAY: Final = 30
DEFAULT_EXIT_DELAY: Final = 30
DEFAULT_CORRELATION_WINDOW: Final = 60
DEFAULT_VOIP_CALL_DELAY: Final = 90
DEFAULT_BATTERY_THRESHOLD: Final = 15
DEFAULT_JAMMING_MIN_DEVICES: Final = 2
DEFAULT_JAMMING_MIN_PERCENT: Final = 50
DEFAULT_FRIGATE_HOST: Final = "192.168.1.109"
DEFAULT_FRIGATE_PORT: Final = 5000
DEFAULT_TELEGRAM_CONFIG_ENTRY: Final = ""
DEFAULT_TELEGRAM_TARGET: Final = ""

# ── VoIP provider ───────────────────────────────────────────────────────────
VOIP_PROVIDER_SHELL: Final = "shell_command"
VOIP_PROVIDER_NOTIFY: Final = "notify_service"
VOIP_PROVIDER_REST: Final = "rest_api"
VOIP_PROVIDER_DISABLED: Final = "disabled"
VOIP_PROVIDER_TYPES: Final = [
    VOIP_PROVIDER_SHELL,
    VOIP_PROVIDER_NOTIFY,
    VOIP_PROVIDER_REST,
    VOIP_PROVIDER_DISABLED,
]

# ── Scoring correlazione ────────────────────────────────────────────────────
SCORE_CONTACT_SENSOR: Final = 70
SCORE_RADAR_SENSOR: Final = 60
SCORE_MOTION_SENSOR: Final = 40
SCORE_PERSON_DETECTION: Final = 30

# Soglia globale cross-zona (percorso B — ladro che cambia zona)
SCORE_THRESHOLD_GLOBAL: Final = 200

# Moltiplicatore cross-zona: evento in zona diversa dalla prima
CROSS_ZONE_MULTIPLIER: Final = 1.5

VOLUMETRIC_SENSOR_TYPES: Final = frozenset({"motion", "radar", "person"})

# ── Timing ──────────────────────────────────────────────────────────────────
HEALTH_CHECK_INTERVAL: Final = timedelta(seconds=30)
BOOT_GRACE_PERIOD: Final = timedelta(minutes=5)
BATTERY_ALERT_INTERVAL_HOURS: Final = 24

# ── Alarm states ────────────────────────────────────────────────────────────
STATE_ALARM_DISARMED: Final = "disarmed"
STATE_ALARM_ARMED_AWAY: Final = "armed_away"
STATE_ALARM_ARMED_HOME: Final = "armed_home"
STATE_ALARM_ARMING: Final = "arming"
STATE_ALARM_PENDING: Final = "pending"
STATE_ALARM_TRIGGERED: Final = "triggered"
STATE_PRE_ALARM: Final = "pre_alarm"
STATE_ALARM_CONFIRMED: Final = "alarm_confirmed"
STATE_FAULT: Final = "fault"

# ── Event types ─────────────────────────────────────────────────────────────
EVENT_TYPE_ARM: Final = "arm"
EVENT_TYPE_DISARM: Final = "disarm"
EVENT_TYPE_TRIGGER: Final = "trigger"
EVENT_TYPE_CONFIRM: Final = "confirm"
EVENT_TYPE_FAULT: Final = "fault"
EVENT_TYPE_RESET: Final = "reset"
EVENT_TYPE_TIMEOUT: Final = "timeout"
EVENT_TYPE_JAMMING: Final = "jamming"
EVENT_TYPE_ENTRY_DELAY: Final = "entry_delay"
EVENT_TYPE_EXIT_DELAY: Final = "exit_delay"
EVENT_TYPE_ABORT: Final = "abort"

# ── Escalation channels ──────────────────────────────────────────────────────
CHANNEL_TELEGRAM: Final = "telegram"
CHANNEL_VOIP_PRIMARY: Final = "voip_primary"
CHANNEL_VOIP_SECONDARY: Final = "voip_secondary"
CHANNEL_FRIGATE: Final = "frigate"
CHANNEL_SIREN: Final = "siren"

# ── Services ─────────────────────────────────────────────────────────────────
SERVICE_FORCE_ARM: Final = "force_arm"
SERVICE_SILENCE_ALARM: Final = "silence_alarm"
SERVICE_TEST_ESCALATION: Final = "test_escalation"
SERVICE_EXPORT_EVENTS: Final = "export_events"

# ── Attributes ───────────────────────────────────────────────────────────────
ATTR_SENSORS_ACTIVE: Final = "sensors_active"
ATTR_SENSORS_FAULTED: Final = "sensors_faulted"
ATTR_SENSORS_OFFLINE: Final = "sensors_offline"
ATTR_LOW_BATTERY: Final = "low_battery"
ATTR_LAST_TRIGGER: Final = "last_trigger"
ATTR_LAST_TRIGGER_TIME: Final = "last_trigger_time"
ATTR_CORRELATION_SCORE: Final = "correlation_score"
ATTR_HEALTH_STATUS: Final = "health_status"
ATTR_JAMMING_DETECTED: Final = "jamming_detected"
ATTR_EVENTS_TODAY: Final = "events_today"
ATTR_BATTERY_MIN: Final = "battery_min"

# ── MQTT / Frigate ────────────────────────────────────────────────────────────
MQTT_TOPIC_FRIGATE_EVENTS: Final = "frigate/events"
FRIGATE_MOTION_SWITCH_PATTERN: Final = "switch.{camera}_motion"
FRIGATE_DETECT_SWITCH_PATTERN: Final = "switch.{camera}_detect"
VIDEO_CLIP_CHECK_INTERVAL: Final = 2
VIDEO_CLIP_MAX_WAIT: Final = 30

# FP300
FP300_SUFFIXES: Final = ("_pir_detection", "_presence")

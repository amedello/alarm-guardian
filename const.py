"""Constants for Alarm Guardian integration."""
from datetime import timedelta
from typing import Final

# Domain
DOMAIN: Final = "alarm_guardian"

# Configuration keys
CONF_PERIMETER_SENSORS: Final = "perimeter_sensors"
CONF_INTERIOR_SENSORS: Final = "interior_sensors"
CONF_CONTACT_SENSORS: Final = "contact_sensors"
CONF_MOTION_SENSORS: Final = "motion_sensors"
CONF_FRIGATE_CAMERAS: Final = "frigate_cameras"
CONF_FRIGATE_HOST: Final = "frigate_host"
CONF_FRIGATE_PORT: Final = "frigate_port"
CONF_FRIGATE_MOTION_SWITCHES: Final = "frigate_motion_switches"
CONF_FRIGATE_DETECT_SWITCHES: Final = "frigate_detect_switches"
CONF_ALARM_PANEL_ENTITY: Final = "alarm_panel_entity"
CONF_TELEGRAM_CONFIG_ENTRY: Final = "telegram_config_entry_id"
CONF_TELEGRAM_TARGET: Final = "telegram_target_chat_id"  # NEW: specific target chat ID
CONF_TELEGRAM_THREAD_ID: Final = "telegram_thread_id"
CONF_VOIP_PRIMARY: Final = "voip_primary_number"
CONF_VOIP_SECONDARY: Final = "voip_secondary_number"
CONF_SHELL_COMMAND_VOIP: Final = "shell_command_voip"
CONF_ARMING_DELAY: Final = "arming_delay"
CONF_CORRELATION_WINDOW: Final = "correlation_window"
CONF_VOIP_CALL_DELAY: Final = "voip_call_delay"
CONF_BATTERY_THRESHOLD: Final = "battery_threshold"
CONF_JAMMING_MIN_DEVICES: Final = "jamming_min_devices"
CONF_JAMMING_MIN_PERCENT: Final = "jamming_min_percent"

# Defaults
DEFAULT_ARMING_DELAY: Final = 30
DEFAULT_CORRELATION_WINDOW: Final = 60
DEFAULT_VOIP_CALL_DELAY: Final = 90
DEFAULT_BATTERY_THRESHOLD: Final = 15
DEFAULT_JAMMING_MIN_DEVICES: Final = 2
DEFAULT_JAMMING_MIN_PERCENT: Final = 50
DEFAULT_FRIGATE_HOST: Final = "192.168.1.109"
DEFAULT_FRIGATE_PORT: Final = 5000
DEFAULT_TELEGRAM_CONFIG_ENTRY: Final = ""  # Will be selected from dropdown
DEFAULT_TELEGRAM_TARGET: Final = ""  # Will be selected from dropdown

# Update intervals
HEALTH_CHECK_INTERVAL: Final = timedelta(seconds=30)
BOOT_GRACE_PERIOD: Final = timedelta(minutes=5)

# Alarm states (mirroring HA alarm states)
STATE_ALARM_DISARMED: Final = "disarmed"
STATE_ALARM_ARMED_AWAY: Final = "armed_away"
STATE_ALARM_ARMED_HOME: Final = "armed_home"
STATE_ALARM_ARMING: Final = "arming"
STATE_ALARM_PENDING: Final = "pending"
STATE_ALARM_TRIGGERED: Final = "triggered"

# Internal states
STATE_PRE_ALARM: Final = "pre_alarm"
STATE_ALARM_CONFIRMED: Final = "alarm_confirmed"
STATE_FAULT: Final = "fault"

# Event types for database
EVENT_TYPE_ARM: Final = "arm"
EVENT_TYPE_DISARM: Final = "disarm"
EVENT_TYPE_TRIGGER: Final = "trigger"
EVENT_TYPE_CONFIRM: Final = "confirm"
EVENT_TYPE_FAULT: Final = "fault"
EVENT_TYPE_RESET: Final = "reset"
EVENT_TYPE_TIMEOUT: Final = "timeout"
EVENT_TYPE_JAMMING: Final = "jamming"  # NEW: for jamming events

# Escalation channels
CHANNEL_TELEGRAM: Final = "telegram"
CHANNEL_VOIP_PRIMARY: Final = "voip_primary"
CHANNEL_VOIP_SECONDARY: Final = "voip_secondary"
CHANNEL_FRIGATE: Final = "frigate"
CHANNEL_SIREN: Final = "siren"

# Correlation scoring
SCORE_CONTACT_SENSOR: Final = 70
SCORE_MOTION_SENSOR: Final = 40
SCORE_PERSON_DETECTION: Final = 30
SCORE_THRESHOLD_CONFIRM: Final = 100

# Services
SERVICE_FORCE_ARM: Final = "force_arm"
SERVICE_SILENCE_ALARM: Final = "silence_alarm"
SERVICE_TEST_ESCALATION: Final = "test_escalation"
SERVICE_EXPORT_EVENTS: Final = "export_events"

# Attributes
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

# MQTT topics
MQTT_TOPIC_FRIGATE_EVENTS: Final = "frigate/events"

# Frigate switches patterns
FRIGATE_MOTION_SWITCH_PATTERN: Final = "switch.{camera}_motion"
FRIGATE_DETECT_SWITCH_PATTERN: Final = "switch.{camera}_detect"

# Video clip check settings
VIDEO_CLIP_CHECK_INTERVAL: Final = 2  # Check every 2 seconds
VIDEO_CLIP_MAX_WAIT: Final = 30  # Max 30 seconds wait for video

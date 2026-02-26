# Alarm Guardian 🛡️

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/amedello/alarm-guardian.svg)](https://github.com/amedello/alarm-guardian/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Custom integration for Home Assistant that adds **multi-zone alarm correlation** on top of any `alarm_control_panel` entity — Tuya, Bosch, DSC, Paradox, Visonic, alarmo, mqtt-alarm-panel, and more.

Instead of triggering on the first sensor event, Alarm Guardian waits for a **confirmation pattern** before raising the alarm — eliminating false positives from pets, drafts, or single sensor glitches.

---

## Features

- 🗺️ **Multi-zone architecture** — define zones with independent confirmation profiles
- 🔗 **Two-level correlation** — local per-zone scoring + global cross-zone bonus
- 🧠 **4 confirmation profiles** — perimeter-only, perimeter+volumetric, rich (with Frigate), diverse-volumetric
- 📡 **Frigate NVR integration** — person detection via MQTT contributes to zone score
- 📱 **Telegram notifications** — detailed alert with zone name, sensor sequence and score
- 📞 **VoIP calls** — Asterisk shell, notify service, or REST API
- 🔋 **Battery monitoring** — low battery alerts with 24h rate limiting
- 📻 **RF jamming detection** — alerts when multiple sensors go offline simultaneously
- 🔊 **External siren** support
- ⏱️ **Entry/exit delays** — configurable per installation
- 🤖 **ML false alarm learning** — adapts score weights based on confirmed vs. false alarms
- 🌍 **5 languages** — Italian, English, German, French, Spanish

---

## Requirements

- Home Assistant 2024.1 or newer
- Any alarm panel exposed as `alarm_control_panel` in Home Assistant (Tuya, Bosch, DSC, Paradox, Visonic, alarmo, mqtt-alarm-panel, etc.)
- MQTT broker (for Frigate integration, optional)
- Telegram bot (for notifications)

---

## Installation

### Via HACS (recommended)

1. Open HACS → Integrations
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/amedello/alarm-guardian` with category **Integration**
4. Search for **Alarm Guardian** and install
5. Restart Home Assistant
6. Go to **Settings → Integrations → Add Integration** and search for **Alarm Guardian**

### Manual

1. Download the [latest release](https://github.com/amedello/alarm-guardian/releases/latest)
2. Copy the `alarm_guardian` folder to `/config/custom_components/`
3. Restart Home Assistant
4. Go to **Settings → Integrations → Add Integration** and search for **Alarm Guardian**

---

## Configuration

The integration is configured entirely via the UI (config flow). No YAML required.

### Setup steps

1. **Alarm panel** — select any `alarm_control_panel` entity
2. **Telegram** — select bot and chat ID
3. **VoIP** — phone numbers and provider (Asterisk, Twilio, REST, or disabled)
4. **Timing** — entry/exit delays, correlation window, battery threshold
5. **Frigate** — host, port, motion/detect switches (optional)
6. **External siren** — siren entity (optional)
7. **Zones** — repeat for each zone:
   - Name and Home Assistant areas
   - Sensors (auto-detected from areas, manually adjustable)
   - Confirmation profile and armed modes

### Confirmation profiles

| Profile | Rule | Typical use |
|---|---|---|
| **Perimeter only** | 2+ contact sensors | Small apartment, no PIR |
| **Perimeter + volumetric** | 1 contact + 1 PIR/radar | Standard house |
| **Rich** | Contact OR Frigate person, with volumetric boost | House with cameras |
| **Diverse volumetric** | 2 different volumetric types (radar + PIR) | Interior zone, pets present |

### Cross-zone scoring

When a sensor triggers in a different zone from the first event, its score is multiplied by **1.5×**. This allows a slow-moving intruder to confirm the alarm even if no single zone reaches its local threshold.

---

## Services

| Service | Description |
|---|---|
| `alarm_guardian.test_escalation` | Test the full notification/call sequence without arming |
| `alarm_guardian.silence_alarm` | Silence the siren while keeping alarm state |
| `alarm_guardian.manual_trigger` | Panic button — trigger alarm manually |
| `alarm_guardian.force_arm` | Arm ignoring specific offline sensors |
| `alarm_guardian.export_events` | Export alarm history to CSV or JSON |
| `alarm_guardian.reset_statistics` | Reset ML learning data |
| `alarm_guardian.clear_fault` | Clear system fault state |
| `alarm_guardian.force_battery_check` | Immediate battery check on all sensors |

---

## Entities created

| Entity | Type | Description |
|---|---|---|
| `sensor.alarm_guardian_correlation_score` | Sensor | Current global correlation score |
| `sensor.alarm_guardian_events_today` | Sensor | Number of alarm events today |
| `sensor.alarm_guardian_battery_min` | Sensor | Lowest battery level among all sensors |
| `binary_sensor.alarm_guardian_jamming` | Binary sensor | RF jamming detected |
| `binary_sensor.alarm_guardian_fp300_*` | Binary sensor | Combined FP300 presence (PIR + radar) |

---

## Supported hardware

Tested with:
- Tuya ZigBee alarm panels (Zemismart, MOES)
- alarmo (virtual alarm panel)
- mqtt-alarm-panel
- Aqara door/window contact sensors
- Aqara FP1E/FP2 presence radar
- Generic ZigBee PIR motion sensors
- Aqara FP300 (dual PIR+radar, auto-combined)
- Frigate NVR with any IP camera

---

## Contributing

Pull requests welcome. Please open an issue first to discuss significant changes.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.

---

## License

[MIT](LICENSE)

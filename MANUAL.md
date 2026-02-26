# Alarm Guardian — User Manual

> Version 2.4.0 | [GitHub](https://github.com/amedello/alarm-guardian)

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Installation](#2-installation)
3. [Initial Setup](#3-initial-setup)
4. [Confirmation Profiles](#4-confirmation-profiles)
5. [Cross-Zone Correlation](#5-cross-zone-correlation)
6. [System States](#6-system-states)
7. [Notifications and Escalation](#7-notifications-and-escalation)
8. [Services](#8-services)
9. [Entities Created](#9-entities-created)
10. [Modifying Configuration](#10-modifying-configuration)
11. [Troubleshooting](#11-troubleshooting)
12. [Advanced Configuration](#12-advanced-configuration)

---

## 1. Introduction

Alarm Guardian is a Home Assistant custom integration that adds an **intelligence layer** on top of any `alarm_control_panel` entity. Instead of triggering on the first sensor event, it waits for a coherent sequence of events before confirming an alarm — eliminating false positives from pets, drafts, or single faulty sensors.

### How it works

The core principle is **correlation scoring**. Every sensor event carries a point value. Alarm Guardian accumulates points within a configurable time window. The alarm fires only when the total reaches the threshold required by the zone's profile — or when events across different zones confirm that an intruder is moving through the home.

**Example:** Kitchen window opens (+70 pts) → Hallway PIR triggers (+40 pts) = 110 pts in zone "Ground Floor" with profile *Perimeter + Volumetric* (threshold 100 pts) → **ALARM CONFIRMED**.

Same scenario with **only the window**: 70 pts < 100 pts → no alarm. The dog walking past the PIR: 40 pts < 100 pts → no alarm.

### Requirements

| Component | Requirement |
|---|---|
| Home Assistant | 2024.1 or newer |
| Alarm panel | Any `alarm_control_panel` entity |
| MQTT broker | Optional — only needed for Frigate NVR |
| Telegram bot | Optional but strongly recommended |

### Supported panels

Alarm Guardian works with any `alarm_control_panel` entity in Home Assistant regardless of manufacturer:

| Panel | Notes |
|---|---|
| Tuya ZigBee (Zemismart, MOES) | Tested, fully supported |
| alarmo | Virtual HA alarm panel, great for testing |
| mqtt-alarm-panel | Compatible, requires MQTT configured |
| Bosch, DSC, Paradox, Visonic | Compatible via their respective HA integrations |
| Any `alarm_control_panel` | If correctly exposed in HA, it works |

---

## 2. Installation

### Via HACS (recommended)

1. Open HACS → Integrations
2. Click the three-dot menu → **Custom repositories**
3. Add `https://github.com/amedello/alarm-guardian` with category **Integration**
4. Search for **Alarm Guardian** and click Install
5. Restart Home Assistant
6. Go to **Settings → Integrations → Add Integration** and search for **Alarm Guardian**

### Manual installation

1. Download the [latest release](https://github.com/amedello/alarm-guardian/releases/latest)
2. Copy the `alarm_guardian` folder into `/config/custom_components/`
3. Restart Home Assistant
4. Go to **Settings → Integrations → Add Integration** and search for **Alarm Guardian**

> **After every update**, a full Home Assistant restart is required. A simple integration reload may not apply all changes.

---

## 3. Initial Setup

Alarm Guardian is configured entirely through the UI. No YAML editing required. The setup wizard has seven sections.

| Step | Section | Required |
|---|---|---|
| 1 | Alarm panel | Yes |
| 2 | Telegram | No (but recommended) |
| 3 | VoIP | No |
| 4 | Timing | Yes (has defaults) |
| 5 | Frigate NVR | No |
| 6 | External siren | No |
| 7 | Zones (repeatable) | Yes — at least one |

Each zone is configured across **4 sub-steps**: name → sensors (home+away) → sensors (away only) → profile.

### Step 1 — Alarm panel

Select the `alarm_control_panel` entity that Alarm Guardian will monitor. A dropdown shows all entities of this type available in your Home Assistant instance.

### Step 2 — Telegram

Configure the Telegram bot for alarm notifications, low battery alerts, jamming detection and system status messages.

| Field | Description |
|---|---|
| Telegram bot | Select the config entry of the `telegram_bot` integration already set up in HA |
| Target chat ID | Your Telegram chat ID (or a group ID). Find it by messaging [@userinfobot](https://t.me/userinfobot) |
| Thread ID (optional) | If you use a group with topics, enter the topic ID where alerts should be sent |

> **How to get your Chat ID:** Send `/start` to [@userinfobot](https://t.me/userinfobot) on Telegram. It will reply with your numeric ID.

### Step 3 — VoIP

Alarm Guardian can place emergency phone calls when an alarm fires. Three providers are supported:

| Provider | When to use |
|---|---|
| Shell command | You have Asterisk/FreePBX configured. The shell command receives the number as an argument. |
| Notify service | You use Twilio, VoIP.ms, or similar via a HA notify service. |
| REST API | Your VoIP provider exposes a custom HTTP API. |
| Disabled | No VoIP calls — Telegram notifications only. |

**Primary** and **secondary number**: Alarm Guardian calls the primary number first, then (after the configured delay) the secondary if there is no answer.

### Step 4 — Timing

| Parameter | Default | Description |
|---|---|---|
| Entry delay | 30 s | Time after the first perimeter sensor triggers before pre-alarm starts. Allows disarming after arriving home. |
| Exit delay | 30 s | Time after arming before sensors become active. Allows leaving after arming. |
| Correlation window | 60 s | Time window within which scores accumulate. After timeout with no confirmation, the counter resets. |
| Delay between VoIP calls | 90 s | Pause between the call to the primary and secondary number. |
| Low battery threshold | 15% | Below this level a Telegram notification is sent for the sensor. |
| Jamming: minimum devices | 2 | Minimum number of devices offline simultaneously to trigger a jamming alert. |
| Jamming: minimum percentage | 50% | Minimum percentage of sensors offline relative to total to trigger a jamming alert. |

### Step 5 — Frigate NVR

| Field | Default | Description |
|---|---|---|
| Frigate host | 192.168.1.109 | IP address or hostname of the Frigate server |
| Frigate port | 5000 | Frigate HTTP port |
| Motion detection switches | — | Select `switch.*_motion` switches to auto-detect camera names |
| Object detection switches | — | Select `switch.*_detect` switches to auto-detect camera names |

> **Why select switches?** Alarm Guardian extracts camera names from the switch entities (e.g. `switch.garden_detect` → camera `garden`). These names are then offered as options in the zone configurator.

### Step 6 — External siren

Optional. Select a `switch` or `siren` entity to activate when an alarm is confirmed. It is automatically deactivated on disarm.

### Step 7 — Zones

Zones are the heart of the configuration. You can define multiple zones (e.g. *Ground Floor*, *Upstairs*, *Garage*). Each zone has its own sensors, confirmation profile, and the armed modes in which it is active.

**The zone configurator loops:** at the end of each zone you are asked whether to add another. You can create as many zones as needed during initial setup, or add/edit them later via the integration's options.

#### 7a — Zone name and areas

Assign a descriptive **name** to the zone (e.g. "Ground Floor", "Night Zone", "Garage") and select the **Home Assistant areas** that belong to this zone. Alarm Guardian uses the areas to automatically detect sensors.

#### 7b — Sensors active in both modes (home + away)

After selecting areas, Alarm Guardian auto-classifies sensors found in those areas. This step covers sensors you want active **regardless of mode** — typically perimeter sensors and any camera or volumetric you want even when you are at home.

| Sensor type | Auto-classification | Score |
|---|---|---|
| Doors, windows, garage (device_class: contact, opening, door) | Perimeter — always active | +70 pts |
| PIR motion (device_class: motion) | Interior | +40 pts |
| Radar, presence (device_class: occupancy/presence) | Interior | +60 pts |
| FP300 combined sensor (PIR + radar unified) | Interior | +60 pts |
| Frigate person detection | Zone camera | +30 pts |

**Perimeter sensors** (doors/windows) are always active whenever the zone is armed — they cannot be restricted to a single mode.

In the **interior sensors (home + away)** and **Frigate cameras (home + away)** fields, place only devices you want active in both armed_home and armed_away. Leave these empty if you want all interior devices to be away-only.

#### 7c — Sensors active only when away (armed_away)

This step collects the interior sensors and cameras that should be **silent when you are home** but active when the house is empty.

Typical away-only sensors:
- Living room PIR (you move through it at night)
- Hallway radar (bedroom corridor)
- Indoor cameras (privacy when home)

Typical both-mode sensors:
- Garden camera (outdoor, not a privacy concern)
- Garage PIR (area you never use at night)

#### 7d — Confirmation profile

The profile determines the rules that must be satisfied to confirm an alarm in this zone. See [Chapter 4](#4-confirmation-profiles) for a detailed description of each profile.

#### 7e — Armed modes

Select which modes activate this zone at all:

- **Away (`armed_away`)**: zone active when armed in away mode. Typically all zones.
- **Home (`armed_home`)**: zone active when armed in home mode (night).

> Note: this setting controls whether the zone participates at all. Individual sensor activity within the zone is further controlled by steps 7b and 7c above.

---

## 4. Confirmation Profiles

A profile defines the rules that must be satisfied for a zone to confirm an alarm. Reaching the score threshold is **necessary but not sufficient** — events must also satisfy the profile's qualitative rules.

### Summary

| Profile | Threshold | Rule | Typical use |
|---|---|---|---|
| Perimeter only | 140 pts | 2+ contact sensors (windows/doors) | Small flat with no PIR |
| Perimeter + volumetric | 100 pts | 1 contact + 1 PIR/radar (both required) | Standard house |
| Rich with cameras | 100 pts | Contact OR Frigate person (+ volumetric boost) | House with IP cameras |
| Diverse volumetric | 100 pts | Radar + PIR (different types, no contact required) | Interior zone with pets |

### Perimeter only

**Threshold: 140 pts** — requires 2 separate contact sensors (70 + 70)

Ideal for zones with only doors and windows and no volumetric sensors. Requires two different openings to be violated: hard to trigger accidentally.

| Scenario | Result |
|---|---|
| Kitchen window (+70) → Front door (+70) = 140 pts | ✅ CONFIRMED |
| Only kitchen window (+70) = 70 pts | ❌ Not confirmed |

### Perimeter + volumetric

**Threshold: 100 pts** — requires at least 1 contact AND at least 1 PIR or radar

The most balanced profile for a standard house. Requires both a physical opening (door/window) and an interior detection. Eliminates almost all single-sensor false alarms.

| Scenario | Result |
|---|---|
| Window (+70) → Living room PIR (+40) = 110 pts | ✅ CONFIRMED |
| Door (+70) → Hallway radar (+60) = 130 pts | ✅ CONFIRMED |
| Only door (+70): no volumetric | ❌ Not confirmed |
| Only PIR (+40) + Radar (+60) = 100 pts: no contact | ❌ Not confirmed |

### Rich with cameras

**Threshold: 100 pts** — requires contact OR Frigate person as anchor

Designed for zones with Frigate cameras. A Frigate person detection (+30 pts) can confirm the alarm together with other sensors, even without a contact sensor. Volumetric sensors boost the score.

| Scenario | Result |
|---|---|
| Window (+70) → PIR (+40) = 110 pts, with contact | ✅ CONFIRMED |
| Frigate person (+30) → Radar (+60) → PIR (+40) = 130 pts, with person | ✅ CONFIRMED |
| Only radar (+60) + PIR (+40) = 100 pts: no contact, no person | ❌ Not confirmed |

### Diverse volumetric

**Threshold: 100 pts** — requires at least 2 **different** volumetric types (radar + PIR)

Designed for interior zones with pets. A dog triggers the PIR but is unlikely to also trigger the FP300/FP1E radar. A human intruder triggers both.

| Scenario | Result |
|---|---|
| FP300 radar (+60) → Living room PIR (+40) = 100 pts, different types | ✅ CONFIRMED |
| Living room PIR (+40) → Hallway PIR (+40) = 80 pts, same type (motion+motion) | ❌ Not confirmed |

---

## 5. Cross-Zone Correlation

In addition to local zone confirmation, Alarm Guardian maintains a **global counter** that accumulates scores from all zones. This detects an intruder moving slowly between zones — a situation where no single zone ever reaches its local threshold.

### Two confirmation paths

| Path | Condition | Threshold |
|---|---|---|
| A — Local confirmation | A zone exceeds its own threshold satisfying profile rules | 100–140 pts (varies by profile) |
| B — Cross-zone confirmation | The global counter exceeds the global threshold | 200 pts |

### Cross-zone multiplier (1.5×)

When an event occurs in a **different zone** from the first zone activated in the session, its contribution to the global score is multiplied by **1.5×**.

**Scenario — intruder entering through the garden:**

1. Garage window (Zone *Garage*) → +70 pts global, Zone Garage at 70 pts
2. Hallway PIR (Zone *House*) → cross-zone! +40 × 1.5 = +60 pts global = 130 pts total
3. Living room radar (Zone *House*) → cross-zone! +60 × 1.5 = +90 pts global = **220 pts ≥ 200 → ALARM** (Path B)

No single zone confirmed locally, but the cross-zone pattern detected the movement.

### The global counter never expires

Unlike local correlation windows (which reset after timeout), the global counter has no expiry. This handles the extreme case of a very slow intruder who takes hours to move through the home. The global counter only resets on disarm or system reset.

> **Note:** If sensors generate repeated false triggers over time, their scores may accumulate toward the global threshold. Identify and address the faulty sensor via the logs.

---

## 6. System States

Alarm Guardian maintains an internal state machine that is more granular than the physical alarm panel.

| State | Description |
|---|---|
| `disarmed` | System disarmed. No sensors monitored. |
| `arming` | Exit delay in progress. System is arming. |
| `armed_away` | Armed in away mode. All sensors active for their respective zones. |
| `armed_home` | Armed in home mode. Zones configured for `armed_home` are active; within each zone only perimeter sensors and sensors marked as "home + away" or "home only" contribute to scoring. |
| `pending` | Entry delay in progress. A perimeter sensor was triggered. Time to disarm. |
| `pre_alarm` | First event detected. Score accumulating. Not yet confirmed. |
| `alarm_confirmed` | Alarm confirmed by correlation. Escalation in progress. |
| `triggered` | Triggered state propagated to the physical panel. |
| `fault` | System fault (e.g. physical panel unreachable). |

### Typical flow — confirmed alarm

```
armed_away → perimeter sensor triggers entry delay → pending → entry delay expires
→ pre_alarm → score accumulates → threshold reached → alarm_confirmed
→ Telegram + VoIP escalation → triggered
```

### Typical flow — false alarm

```
armed_away → sensor triggers → pre_alarm → correlation window expires
without reaching threshold → back to armed_away → timeout notification on Telegram
```

### Disarming during escalation

If the alarm is disarmed during escalation (e.g. it was a false alarm and you are home), Alarm Guardian immediately aborts all ongoing VoIP calls, silences the external siren, and resets the correlation counter.

---

## 7. Notifications and Escalation

When an alarm is confirmed, Alarm Guardian starts a three-phase escalation sequence.

### Phase 1 — Immediate Telegram

Within seconds of confirmation, a Telegram message is sent with:

- Zone name and confirmation type (local or cross-zone)
- Full event sequence with sensor type, friendly name and score
- Total score

**Example alert:**
```
🚨 ALARM CONFIRMED
📍 Zone: Ground Floor
🕐 Time: 02:34:17
🏠 Confirmation: local

Event sequence:
  🚪 Kitchen window (+70 pts)
  👁 Hallway PIR (+40 pts)

📊 Total score: 110 pts
```

**Cross-zone alert:**
```
🚨 ALARM CONFIRMED
📍 Zone: cross-zone (intruder moving)
🕐 Time: 02:41:05
🌐 Confirmation: cross-zone

Event sequence:
  🚪 Garage window — Zone Garage (+70 pts)
  📡 Hallway radar — Zone House (+90 pts ×1.5)
  👁 Living room PIR — Zone House (+60 pts ×1.5)

📊 Total score: 220 pts
```

### Phase 2 — VoIP calls

After the Telegram notification, Alarm Guardian places VoIP calls to the configured numbers. Primary number first, then the secondary after the configured delay.

### Phase 3 — Frigate clips

If Frigate NVR is configured and has recorded video of the event, Alarm Guardian waits for the clip to become available (up to 30 seconds) and sends it via Telegram together with a snapshot.

### Other notifications

| Event | Description |
|---|---|
| Arming | Notification when system is armed, with exit delay countdown |
| Disarming | Notification when system is disarmed |
| Entry delay | Warning that sensor X triggered the entry delay countdown |
| Correlation timeout | Event detected but score not reached within the window |
| Low battery | Sensor below threshold (max 1 alert per sensor per 24 hours) |
| RF jamming | Too many sensors offline simultaneously |
| Online | Startup message sent when HA restarts |

---

## 8. Services

All services are available in **Developer Tools → Services** or from automations and scripts.

| Service | Description |
|---|---|
| `alarm_guardian.test_escalation` | Runs the full escalation sequence (Telegram + VoIP + Frigate) without arming the system. Use to verify notifications and calls work. |
| `alarm_guardian.silence_alarm` | Silences the external siren while keeping the `alarm_confirmed` state. Useful if you are already home. |
| `alarm_guardian.manual_trigger` | Manually triggers the alarm (panic button). Accepts an optional `reason` parameter included in the Telegram notification. |
| `alarm_guardian.force_arm` | Arms the system ignoring specific offline sensors. Accepts a list of `entity_id` to ignore. |
| `alarm_guardian.export_events` | Exports alarm event history to CSV or JSON. Parameters: `days` (1–365), `format` (csv/json), `path` (relative to /config). |
| `alarm_guardian.reset_statistics` | Resets ML learning data. Use after a major reconfiguration. |
| `alarm_guardian.clear_fault` | Clears the system fault state and resumes normal operation. |
| `alarm_guardian.force_battery_check` | Forces an immediate battery check on all sensors and sends notifications for those below threshold. |
| `alarm_guardian.test_battery_notification` | Sends a test low battery Telegram notification to verify the notification channel. |

### Example — panic button automation

```yaml
alias: Panic button
trigger:
  - platform: state
    entity_id: binary_sensor.panic_button
    to: "on"
action:
  - service: alarm_guardian.manual_trigger
    data:
      reason: "Panic button pressed"
```

---

## 9. Entities Created

| Entity | Type | Description |
|---|---|---|
| `sensor.alarm_guardian_correlation_score` | Sensor | Current global correlation score (0 when disarmed or after reset) |
| `sensor.alarm_guardian_events_today` | Sensor | Number of alarm events logged today in the database |
| `sensor.alarm_guardian_battery_min` | Sensor | Lowest battery level among all monitored sensors |
| `binary_sensor.alarm_guardian_jamming` | Binary sensor | ON when RF interference is detected (too many sensors offline) |
| `binary_sensor.alarm_guardian_fp300_*` | Binary sensor | Combined sensor for each FP300 pair: ON if either PIR or radar is active |

### Attributes of `correlation_score`

| Attribute | Description |
|---|---|
| `global_score` | Current global score |
| `global_threshold` | Global cross-zone threshold (200 pts) |
| `first_zone` | Name of the first zone activated in the current session |
| `zones` | List of zones with their individual scores, thresholds and events |

### FP300 combined sensor

The Aqara FP300 exposes two separate entities in HA: one for presence (radar) and one for PIR detection. Alarm Guardian automatically combines them into a `binary_sensor.alarm_guardian_fp300_*` that is ON if either one is active. This combined sensor is used internally in correlation with a score of +60 pts (radar type).

---

## 10. Modifying Configuration

After initial setup, any parameter can be changed without reinstalling. Go to **Settings → Integrations → Alarm Guardian → Configure**.

The options menu is divided into sections: Telegram notifications, VoIP calls, Timing, Frigate NVR, External siren, Zones.

### Zone management

In the Zones section you can:

- **Add** a new zone (same flow as initial setup)
- **Edit** an existing zone: name, perimeter sensors, interior sensors per mode, cameras per mode, profile and armed modes
- **Delete** a zone

> After modifying zones, HA will prompt you to reload the integration. Confirm and wait for the reload to complete.

---

## 11. Troubleshooting

### Enabling debug logs

Add to `configuration.yaml` to see detailed Alarm Guardian activity:

```yaml
logger:
  default: warning
  logs:
    custom_components.alarm_guardian: debug
```

Logs are visible at **Settings → System → Logs**. Filter by `alarm_guardian`.

### Common issues

| Problem | Likely cause | Solution |
|---|---|---|
| Alarm never fires | Score does not reach threshold within the correlation window | Check the logs to see accumulated scores. Verify sensors are correctly classified in the zone. Consider increasing the correlation window or choosing a less strict profile. |
| Too many false alarms | Sensors triggering frequently without an intruder | Identify the problematic sensor in the logs. Move it to a zone with a more restrictive profile, or remove it from correlation. |
| Telegram notifications not received | Bot not configured or wrong chat ID | Test with `alarm_guardian.test_escalation`. Verify the bot is active and the chat ID is correct. |
| VoIP calls not working | Wrong provider configuration | Test with `alarm_guardian.test_escalation`, check logs for the specific error. |
| Sensors not auto-detected | Sensors not assigned to HA areas | Assign devices to rooms in HA (Settings → Areas), then reconfigure the zones. |
| `KeyError: correlation_engine` on boot | Stale cache from a previous version | Restart HA completely (not just reload). Clear browser cache. |
| Options flow error `config_entry has no setter` | HA version mismatch | Update Alarm Guardian to 2.4.0 or newer. |

### Filing a bug report

Open an issue at [github.com/amedello/alarm-guardian/issues](https://github.com/amedello/alarm-guardian/issues) and include:

- Home Assistant version and Alarm Guardian version
- Relevant log entries filtered for `alarm_guardian`
- Steps to reproduce the problem
- Expected vs actual behaviour

---

## 12. Advanced Configuration

### VoIP with Asterisk / shell command

When using the **Shell command** provider, the command field should contain the command to execute. The phone number is passed as the `{number}` placeholder.

```bash
# Example using Asterisk AGI
/usr/local/bin/originate_call.sh {number}
```

### VoIP with REST API

| Field | Example |
|---|---|
| Endpoint URL | `https://api.myprovider.com/v1/calls` |
| HTTP method | `POST` |
| Headers (JSON) | `{"Authorization": "Bearer TOKEN", "Content-Type": "application/json"}` |
| Body (JSON) | `{"to": "{number}", "from": "+390123456789", "message": "Alarm triggered"}` |

Use `{number}` anywhere in the body or URL where the target phone number should be inserted.

### Frigate MQTT integration

Alarm Guardian subscribes to the `frigate/events` MQTT topic. Every Frigate event with label `person` and a confidence of **60% or higher** contributes +30 pts to the score of the zone associated with that camera. Events below 60% confidence are ignored.

### ML adaptive learning

Alarm Guardian includes a machine learning module that learns over time. It analyses patterns of false alarms and confirmed alarms to adapt sensor score weights. Data is stored in a local SQLite database in `/config`.

Use `alarm_guardian.reset_statistics` to reset the ML model after a major reconfiguration.

### Migration from single-zone versions

If you are upgrading from a version that used a flat sensor list (without zones), Alarm Guardian automatically migrates your configuration to a single zone named **Home** containing all existing sensors. The profile is set to `rich` if cameras were configured, otherwise `perimeter_plus`. No manual action is required.

**Upgrading from 2.4.0 to 2.5.0:** Existing zones configured with a single sensor list are automatically treated as "active in both modes" (home + away). No data is lost. To take advantage of per-mode sensor selection, edit each zone via **Settings → Integrations → Alarm Guardian → Configure → Zones**.

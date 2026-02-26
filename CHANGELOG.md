# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.5.0] - 2026-02-26

### Added
- Per-sensor armed mode selection: interior sensors and Frigate cameras can now be configured independently as active in both modes (home + away), away only, or home only
- New config/options flow step `zone_devices_away` for sensors active only when away
- `zone_interior_sensors_both`, `zone_interior_sensors_away`, `zone_interior_sensors_home` zone config keys
- `zone_frigate_cameras_both`, `zone_frigate_cameras_away`, `zone_frigate_cameras_home` zone config keys
- `is_interior_active_in_mode()` and `is_camera_active_in_mode()` methods on ZoneCorrelation

### Changed
- `zone_devices` setup step now covers sensors active in both modes; a new `zone_devices_away` step follows for away-only sensors
- Perimeter sensors remain always active when the zone is armed (no per-mode selection needed)
- Removed hardcoded "perimeter sensors always active in armed_home" override from `__init__.py` — mode filtering is now fully handled by ZoneEngine

### Migration
- Existing zones with `zone_interior_sensors` and `zone_frigate_cameras` are automatically treated as active in both modes (backwards compatible, no data loss)

## [2.4.0] - 2026-02-25

### Added
- Multi-zone architecture with independent confirmation profiles per zone
- Zone Engine with two-level correlation (local + global cross-zone)
- 4 confirmation profiles: perimeter_only, perimeter_plus, rich, volumetric_diverse
- Cross-zone score multiplier (1.5×) for slow-moving intruder detection
- Config flow with zone loop (add multiple zones during setup)
- Options flow with zone add/edit/delete
- Auto-classification of sensors from Home Assistant areas
- Frigate cameras auto-populated from configured detect/motion switches
- Full translations: Italian, English, German, French, Spanish
- Detailed Telegram alert with zone name, sensor sequence and score
- RF jamming alert uses friendly names instead of entity IDs

### Changed
- `config_entries.FlowResult` → `config_entries.ConfigFlowResult` (HA 2024.11+)
- `hass.loop.call_later` → `async_call_later` (HA 2024+)
- Registry access via `er.async_get()` / `dr.async_get()` / `ar.async_get()` (deprecation)
- Sensor `correlation_score` now reflects `ZoneEngine.global_score`
- Frigate listener routes events through ZoneEngine instead of flat sensor list
- Coordinator battery monitoring reads sensors from all zones

### Fixed
- Telegram markdown parse error on jamming alert (entity IDs with underscores)
- Options flow `config_entry` property conflict with HA base class

---

## [2.3.0] - 2026-02-24

### Added
- Configurable VoIP provider: shell command, notify service, REST API, or disabled
- Options flow for post-setup configuration changes
- External siren support with auto-silence on disarm
- Entry delay with pending state and Telegram countdown notification
- Exit delay with arming state
- Arming/disarming Telegram notifications
- Battery monitoring rate limiting (one alert per sensor per 24h)
- RF jamming detection (configurable device count and percentage thresholds)

### Fixed
- Disarming now correctly aborts active escalation
- Escalation abort flag reset on each new escalation sequence

---

## [2.0.0] - 2026-02-20

### Added
- Initial public release
- Single-zone correlation engine
- Frigate MQTT person detection integration
- Telegram notification with snapshot
- VoIP call via Asterisk shell command
- ML false alarm predictor
- Adaptive correlation window
- SQLite event database
- Battery monitoring
- FP300 combined sensor (dual PIR + radar → single binary sensor)

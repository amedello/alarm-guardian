"""Zone Engine per Alarm Guardian.

Gestisce la correlazione a due livelli:
- Livello locale: ogni zona valuta i propri sensori con il suo profilo
- Livello globale: accumula score cross-zona con bonus moltiplicatore

La conferma può avvenire in due modi:
  A) Una zona raggiunge la sua soglia locale → allarme confermato immediato
  B) Lo score globale supera SCORE_THRESHOLD_GLOBAL → allarme confermato
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from .const import (
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
    ZONE_PROFILE_PERIMETER_ONLY,
    ZONE_PROFILE_PERIMETER_PLUS,
    ZONE_PROFILE_RICH,
    ZONE_PROFILE_VOLUMETRIC_DIVERSE,
    ZONE_PROFILE_THRESHOLDS,
    SCORE_CONTACT_SENSOR,
    SCORE_RADAR_SENSOR,
    SCORE_MOTION_SENSOR,
    SCORE_PERSON_DETECTION,
    SCORE_THRESHOLD_GLOBAL,
    CROSS_ZONE_MULTIPLIER,
    VOLUMETRIC_SENSOR_TYPES,
    FP300_SUFFIXES,
)

_LOGGER = logging.getLogger(__name__)

ConfirmCallback = Callable[[], Coroutine]
TimeoutCallback = Callable[[str], Coroutine]  # zone_name


class ZoneTriggerEvent:
    """Evento di trigger in una zona."""

    def __init__(
        self,
        entity_id: str,
        entity_name: str,
        sensor_type: str,
        score: int,
        zone_id: str,
        zone_name: str,
        timestamp: datetime | None = None,
    ) -> None:
        self.entity_id = entity_id
        self.entity_name = entity_name
        self.sensor_type = sensor_type
        self.score = score
        self.zone_id = zone_id
        self.zone_name = zone_name
        self.timestamp = timestamp or datetime.now()

    def __repr__(self) -> str:
        return (
            f"ZoneTriggerEvent({self.sensor_type}, {self.entity_name}, "
            f"score={self.score}, zona='{self.zone_name}')"
        )


class ZoneCorrelation:
    """Correlazione locale di una singola zona.

    Valuta gli eventi rispetto al profilo configurato e
    decide se la zona ha confermato l'allarme.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zone_config: dict[str, Any],
        correlation_window: int,
        on_confirmed: ConfirmCallback,
        on_timeout: TimeoutCallback,
    ) -> None:
        self.hass = hass
        self.zone_id: str = zone_config[ZONE_ID]
        self.zone_name: str = zone_config[ZONE_NAME]
        self.profile: str = zone_config.get(ZONE_PROFILE, ZONE_PROFILE_PERIMETER_PLUS)
        self.perimeter_sensors: set[str] = set(zone_config.get(ZONE_PERIMETER_SENSORS, []))

        # Retrocompatibilità: se esistono le chiavi legacy le usiamo come _both
        _legacy_interior = zone_config.get(ZONE_INTERIOR_SENSORS, [])
        _legacy_cameras  = zone_config.get(ZONE_FRIGATE_CAMERAS, [])

        self.interior_sensors_both: set[str] = set(
            zone_config.get(ZONE_INTERIOR_SENSORS_BOTH, _legacy_interior)
        )
        self.interior_sensors_away: set[str] = set(
            zone_config.get(ZONE_INTERIOR_SENSORS_AWAY, [])
        )
        self.interior_sensors_home: set[str] = set(
            zone_config.get(ZONE_INTERIOR_SENSORS_HOME, [])
        )

        self.frigate_cameras_both: set[str] = set(
            zone_config.get(ZONE_FRIGATE_CAMERAS_BOTH, _legacy_cameras)
        )
        self.frigate_cameras_away: set[str] = set(
            zone_config.get(ZONE_FRIGATE_CAMERAS_AWAY, [])
        )
        self.frigate_cameras_home: set[str] = set(
            zone_config.get(ZONE_FRIGATE_CAMERAS_HOME, [])
        )

        # Proprietà aggregate — usate esternamente (battery, FP300 patch, ecc.)
        self.interior_sensors: set[str] = (
            self.interior_sensors_both
            | self.interior_sensors_away
            | self.interior_sensors_home
        )
        self.frigate_cameras: set[str] = (
            self.frigate_cameras_both
            | self.frigate_cameras_away
            | self.frigate_cameras_home
        )
        self.armed_modes: list[str] = zone_config.get(ZONE_ARMED_MODES, ["armed_away"])
        self.correlation_window = correlation_window
        self._threshold = ZONE_PROFILE_THRESHOLDS[self.profile]

        self._on_confirmed = on_confirmed
        self._on_timeout = on_timeout

        self._events: list[ZoneTriggerEvent] = []
        self._total_score: int = 0
        self._active: bool = False
        self._started_at: Optional[datetime] = None
        self._timer_handle = None

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def total_score(self) -> int:
        return self._total_score

    @property
    def events(self) -> list[ZoneTriggerEvent]:
        return self._events.copy()

    @property
    def all_sensor_ids(self) -> set[str]:
        return self.perimeter_sensors | self.interior_sensors

    def owns_sensor(self, entity_id: str) -> bool:
        return entity_id in self.all_sensor_ids

    def owns_camera(self, camera_name: str) -> bool:
        return camera_name in self.frigate_cameras

    def is_interior_active_in_mode(self, entity_id: str, alarm_mode: str) -> bool:
        """Verifica se un sensore interno è attivo per la modalità corrente."""
        if entity_id in self.interior_sensors_both:
            return True
        if alarm_mode == "armed_away" and entity_id in self.interior_sensors_away:
            return True
        if alarm_mode == "armed_home" and entity_id in self.interior_sensors_home:
            return True
        return False

    def is_camera_active_in_mode(self, camera_name: str, alarm_mode: str) -> bool:
        """Verifica se una telecamera è attiva per la modalità corrente."""
        if camera_name in self.frigate_cameras_both:
            return True
        if alarm_mode == "armed_away" and camera_name in self.frigate_cameras_away:
            return True
        if alarm_mode == "armed_home" and camera_name in self.frigate_cameras_home:
            return True
        return False

    def is_active_in_mode(self, alarm_mode: str) -> bool:
        return alarm_mode in self.armed_modes

    def start(self) -> None:
        """Avvia la finestra di correlazione locale."""
        self._active = True
        self._started_at = datetime.now()
        self._schedule_timeout()
        _LOGGER.info("Zona '%s': finestra di correlazione aperta (%ds)", self.zone_name, self.correlation_window)

    def reset(self) -> None:
        """Reset completo della zona."""
        if self._timer_handle:
            self._timer_handle.cancel()
            self._timer_handle = None
        self._events.clear()
        self._total_score = 0
        self._active = False
        self._started_at = None

    def _schedule_timeout(self) -> None:
        if self._timer_handle:
            self._timer_handle.cancel()
        self._timer_handle = async_call_later(
            self.hass,
            self.correlation_window,
            lambda _: self.hass.async_create_task(self._handle_timeout()),
        )

    async def _handle_timeout(self) -> None:
        _LOGGER.info(
            "Zona '%s': timeout correlazione (score=%d/%d, eventi=%d)",
            self.zone_name, self._total_score, self._threshold, len(self._events),
        )
        self.reset()
        await self._on_timeout(self.zone_name)

    async def add_event(self, event: ZoneTriggerEvent) -> bool:
        """Aggiunge un evento e valuta se la zona ha confermato.

        Restituisce True se la zona ha confermato l'allarme.
        """
        if not self._active:
            self.start()

        self._events.append(event)
        self._total_score += event.score

        _LOGGER.info(
            "Zona '%s' [%s]: %s '%s' score=%d → totale=%d/%d",
            self.zone_name, self.profile,
            event.sensor_type, event.entity_name,
            event.score, self._total_score, self._threshold,
        )

        if self._total_score < self._threshold:
            return False

        # Score raggiunto: verifica regole del profilo
        if self._evaluate_profile():
            _LOGGER.warning(
                "Zona '%s': CONFERMATO (score=%d, profilo=%s, eventi=%s)",
                self.zone_name, self._total_score, self.profile,
                [str(e) for e in self._events],
            )
            if self._timer_handle:
                self._timer_handle.cancel()
                self._timer_handle = None
            await self._on_confirmed()
            return True

        return False

    def _evaluate_profile(self) -> bool:
        """Valuta le regole specifiche del profilo."""
        types = [e.sensor_type for e in self._events]
        unique_types = set(types)

        if self.profile == ZONE_PROFILE_PERIMETER_ONLY:
            # Servono 2+ contact (70+70=140)
            contact_count = types.count("contact")
            if contact_count < 2:
                _LOGGER.debug(
                    "Zona '%s' [perimeter_only]: score ok ma solo %d contact (ne servono 2)",
                    self.zone_name, contact_count,
                )
                return False
            return True

        elif self.profile == ZONE_PROFILE_PERIMETER_PLUS:
            # Serve almeno 1 contact + almeno 1 volumetrico
            has_contact = "contact" in unique_types
            has_volumetric = bool(unique_types & VOLUMETRIC_SENSOR_TYPES)
            if not has_contact:
                _LOGGER.debug(
                    "Zona '%s' [perimeter_plus]: score ok ma nessun contact",
                    self.zone_name,
                )
                return False
            if not has_volumetric:
                _LOGGER.debug(
                    "Zona '%s' [perimeter_plus]: score ok ma nessun volumetrico",
                    self.zone_name,
                )
                return False
            return True

        elif self.profile == ZONE_PROFILE_RICH:
            # Contact OR person come ancora, poi volumetrici rinforzano
            has_contact = "contact" in unique_types
            has_person = "person" in unique_types
            if not (has_contact or has_person):
                _LOGGER.debug(
                    "Zona '%s' [rich]: score ok ma nessun contact né person",
                    self.zone_name,
                )
                return False
            return True

        elif self.profile == ZONE_PROFILE_VOLUMETRIC_DIVERSE:
            # Solo volumetrici ma tipi DIVERSI (radar + motion)
            volumetric_types = unique_types & VOLUMETRIC_SENSOR_TYPES
            if len(volumetric_types) < 2:
                _LOGGER.debug(
                    "Zona '%s' [volumetric_diverse]: score ok ma tipi volumetrici: %s (ne servono 2 diversi)",
                    self.zone_name, volumetric_types,
                )
                return False
            return True

        # Profilo sconosciuto: fallback permissivo
        _LOGGER.warning("Zona '%s': profilo '%s' sconosciuto, uso fallback permissivo", self.zone_name, self.profile)
        return True

    def get_attributes(self) -> dict[str, Any]:
        return {
            "zone_name": self.zone_name,
            "profile": self.profile,
            "is_active": self._active,
            "total_score": self._total_score,
            "threshold": self._threshold,
            "events_count": len(self._events),
            "events": [
                {"type": e.sensor_type, "name": e.entity_name, "score": e.score, "time": e.timestamp.isoformat()}
                for e in self._events
            ],
        }


class ZoneEngine:
    """Engine principale multi-zona.

    Coordina tutte le ZoneCorrelation e mantiene il contatore globale cross-zona.

    Percorso A: una zona conferma localmente → allarme
    Percorso B: score globale >= SCORE_THRESHOLD_GLOBAL → allarme
    """

    def __init__(
        self,
        hass: HomeAssistant,
        zones_config: list[dict[str, Any]],
        correlation_window: int,
        on_confirmed: ConfirmCallback,
        on_timeout: TimeoutCallback,
    ) -> None:
        self.hass = hass
        self._on_confirmed = on_confirmed
        self._on_timeout = on_timeout
        self._confirmed = False

        # Mappa zone per ID
        self._zones: dict[str, ZoneCorrelation] = {}
        for zc in zones_config:
            z = ZoneCorrelation(
                hass=hass,
                zone_config=zc,
                correlation_window=correlation_window,
                on_confirmed=self._zone_confirmed,
                on_timeout=on_timeout,
            )
            self._zones[z.zone_id] = z

        # Contatore globale cross-zona
        self._global_score: float = 0.0
        self._first_zone_id: Optional[str] = None
        self._global_events: list[ZoneTriggerEvent] = []

        # Mappa inversa entity_id → zone_id (per lookup veloce)
        self._sensor_to_zone: dict[str, str] = {}
        self._camera_to_zone: dict[str, str] = {}
        for z in self._zones.values():
            for sid in z.all_sensor_ids:
                self._sensor_to_zone[sid] = z.zone_id
            for cam in z.frigate_cameras:
                self._camera_to_zone[cam] = z.zone_id

        _LOGGER.info(
            "ZoneEngine inizializzato: %d zone, %d sensori totali",
            len(self._zones),
            len(self._sensor_to_zone),
        )

    @property
    def zones(self) -> list[ZoneCorrelation]:
        return list(self._zones.values())

    @property
    def global_score(self) -> float:
        return self._global_score

    @property
    def all_sensor_ids(self) -> list[str]:
        return list(self._sensor_to_zone.keys())

    def get_zone_for_sensor(self, entity_id: str) -> Optional[ZoneCorrelation]:
        zone_id = self._sensor_to_zone.get(entity_id)
        return self._zones.get(zone_id) if zone_id else None

    def get_zone_for_camera(self, camera_name: str) -> Optional[ZoneCorrelation]:
        zone_id = self._camera_to_zone.get(camera_name)
        return self._zones.get(zone_id) if zone_id else None

    def sensor_type_for(self, entity_id: str, zone: ZoneCorrelation) -> tuple[str, int]:
        """Determina tipo e score di un sensore nella sua zona."""
        entity_lower = entity_id.lower()
        is_perimeter = entity_id in zone.perimeter_sensors

        if is_perimeter:
            return "contact", SCORE_CONTACT_SENSOR

        if "_fp300_combinato" in entity_lower or any(s in entity_lower for s in FP300_SUFFIXES):
            return "radar", SCORE_RADAR_SENSOR

        return "motion", SCORE_MOTION_SENSOR

    def reset(self) -> None:
        """Reset completo di tutte le zone e del globale."""
        for z in self._zones.values():
            z.reset()
        self._global_score = 0.0
        self._first_zone_id = None
        self._global_events.clear()
        self._confirmed = False
        _LOGGER.info("ZoneEngine: reset completo")

    async def process_sensor_event(
        self,
        entity_id: str,
        entity_name: str,
        alarm_mode: str,
        adjusted_score: int | None = None,
    ) -> bool:
        """Processa un evento sensore.

        Restituisce True se l'allarme è stato confermato.
        """
        if self._confirmed:
            return False

        zone = self.get_zone_for_sensor(entity_id)
        if not zone:
            _LOGGER.warning("Sensore %s non appartiene a nessuna zona", entity_id)
            return False

        if not zone.is_active_in_mode(alarm_mode):
            _LOGGER.debug(
                "Zona '%s' non attiva in modalità %s", zone.zone_name, alarm_mode
            )
            return False

        # I perimetrali sono sempre attivi. I sensori interni vengono filtrati per modalità.
        is_perimeter = entity_id in zone.perimeter_sensors
        if not is_perimeter and not zone.is_interior_active_in_mode(entity_id, alarm_mode):
            _LOGGER.debug(
                "Sensore interno '%s' non attivo in modalità %s (zona '%s')",
                entity_id, alarm_mode, zone.zone_name,
            )
            return False

        sensor_type, base_score = self.sensor_type_for(entity_id, zone)
        score = adjusted_score if adjusted_score is not None else base_score

        event = ZoneTriggerEvent(
            entity_id=entity_id,
            entity_name=entity_name,
            sensor_type=sensor_type,
            score=score,
            zone_id=zone.zone_id,
            zone_name=zone.zone_name,
        )

        # ── Aggiorna score globale cross-zona ─────────────────────────
        is_cross_zone = (
            self._first_zone_id is not None
            and self._first_zone_id != zone.zone_id
        )
        if self._first_zone_id is None:
            self._first_zone_id = zone.zone_id

        global_contribution = score * CROSS_ZONE_MULTIPLIER if is_cross_zone else score
        self._global_score += global_contribution
        self._global_events.append(event)

        if is_cross_zone:
            _LOGGER.info(
                "Cross-zona: %s in '%s' (prima zona: '%s') → contributo globale %.0f (x%.1f)",
                entity_name, zone.zone_name,
                self._zones[self._first_zone_id].zone_name,
                global_contribution, CROSS_ZONE_MULTIPLIER,
            )

        # ── Percorso B: score globale ──────────────────────────────────
        if self._global_score >= SCORE_THRESHOLD_GLOBAL:
            _LOGGER.warning(
                "Percorso B: score globale %.0f >= %d → allarme confermato cross-zona",
                self._global_score, SCORE_THRESHOLD_GLOBAL,
            )
            self._confirmed = True
            await self._on_confirmed()
            return True

        # ── Percorso A: correlazione locale di zona ────────────────────
        confirmed = await zone.add_event(event)
        if confirmed:
            self._confirmed = True
            return True

        return False

    async def process_person_detection(
        self,
        camera_name: str,
        confidence: float,
        alarm_mode: str,
    ) -> bool:
        """Processa rilevamento persona da Frigate."""
        if self._confirmed:
            return False

        zone = self.get_zone_for_camera(camera_name)
        if not zone:
            _LOGGER.debug("Telecamera %s non appartiene a nessuna zona", camera_name)
            return False

        if not zone.is_active_in_mode(alarm_mode):
            return False

        if not zone.is_camera_active_in_mode(camera_name, alarm_mode):
            _LOGGER.debug(
                "Telecamera '%s' non attiva in modalità %s (zona '%s')",
                camera_name, alarm_mode, zone.zone_name,
            )
            return False

        entity_name = f"Camera {camera_name} (person {int(confidence * 100)}%)"
        score = SCORE_PERSON_DETECTION

        event = ZoneTriggerEvent(
            entity_id=f"frigate_{camera_name}",
            entity_name=entity_name,
            sensor_type="person",
            score=score,
            zone_id=zone.zone_id,
            zone_name=zone.zone_name,
        )

        # Cross-zona check
        is_cross_zone = (
            self._first_zone_id is not None
            and self._first_zone_id != zone.zone_id
        )
        if self._first_zone_id is None:
            self._first_zone_id = zone.zone_id

        global_contribution = score * CROSS_ZONE_MULTIPLIER if is_cross_zone else score
        self._global_score += global_contribution
        self._global_events.append(event)

        # Percorso B
        if self._global_score >= SCORE_THRESHOLD_GLOBAL:
            _LOGGER.warning("Percorso B (person): score globale %.0f >= %d", self._global_score, SCORE_THRESHOLD_GLOBAL)
            self._confirmed = True
            await self._on_confirmed()
            return True

        # Percorso A
        confirmed = await zone.add_event(event)
        if confirmed:
            self._confirmed = True
            return True

        return False

    async def _zone_confirmed(self) -> None:
        """Callback: una zona ha confermato → propaga al livello superiore."""
        if not self._confirmed:
            self._confirmed = True
            await self._on_confirmed()

    def get_attributes(self) -> dict[str, Any]:
        return {
            "global_score": round(self._global_score, 1),
            "global_threshold": SCORE_THRESHOLD_GLOBAL,
            "first_zone": self._zones[self._first_zone_id].zone_name if self._first_zone_id else None,
            "zones": [z.get_attributes() for z in self._zones.values()],
        }


def build_zones_from_legacy(entry_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Costruisce una lista di zone dal formato legacy (pre-zone).

    Usato per la migration automatica di installazioni esistenti.
    Crea una zona unica "Casa" con tutti i sensori esistenti e
    profilo 'rich' se ci sono telecamere, altrimenti 'perimeter_plus'.
    """
    perimeter = (
        entry_data.get("perimeter_sensors", [])
        or entry_data.get("contact_sensors", [])
    )
    interior = (
        entry_data.get("interior_sensors", [])
        or entry_data.get("motion_sensors", [])
    )
    cameras = entry_data.get("frigate_cameras", [])

    profile = ZONE_PROFILE_RICH if cameras else ZONE_PROFILE_PERIMETER_PLUS

    zone: dict[str, Any] = {
        ZONE_ID: str(uuid.uuid4()),
        ZONE_NAME: "Casa",
        ZONE_HA_AREAS: [],
        ZONE_PERIMETER_SENSORS: perimeter,
        ZONE_INTERIOR_SENSORS_BOTH: interior,
        ZONE_FRIGATE_CAMERAS_BOTH: cameras,
        ZONE_PROFILE: profile,
        ZONE_ARMED_MODES: ["armed_away", "armed_home"],
    }

    _LOGGER.info(
        "Migration legacy → zona unica 'Casa' (profilo=%s, perimetro=%d, interni=%d, cam=%d)",
        profile, len(perimeter), len(interior), len(cameras),
    )
    return [zone]

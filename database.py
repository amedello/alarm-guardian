"""Database manager for Alarm Guardian audit log."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Database schema
SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS alarm_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    event_type TEXT NOT NULL,
    state_from TEXT,
    state_to TEXT,
    sensor_id TEXT,
    sensor_name TEXT,
    correlation_score INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS alarm_escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    channel TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    retry_count INTEGER DEFAULT 0,
    response_time FLOAT,
    FOREIGN KEY(event_id) REFERENCES alarm_events(id)
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON alarm_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON alarm_events(event_type);
CREATE INDEX IF NOT EXISTS idx_escalations_event ON alarm_escalations(event_id);
"""


class AlarmDatabase:
    """Manages SQLite database for alarm event logging."""

    def __init__(self, hass: HomeAssistant, config_entry_id: str) -> None:
        """Initialize database manager."""
        self.hass = hass
        self.config_entry_id = config_entry_id
        
        # Database file location
        db_dir = Path(hass.config.path("alarm_guardian"))
        db_dir.mkdir(exist_ok=True)
        
        self.db_path = db_dir / f"{config_entry_id}.db"
        self._conn: sqlite3.Connection | None = None
        
        _LOGGER.info("Database path: %s", self.db_path)

    async def async_setup(self) -> None:
        """Set up database (create tables if needed)."""
        await self.hass.async_add_executor_job(self._setup_sync)

    def _setup_sync(self) -> None:
        """Set up database synchronously."""
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=10.0,
        )
        
        # Enable foreign keys
        self._conn.execute("PRAGMA foreign_keys = ON")
        
        # Create schema
        self._conn.executescript(SCHEMA_SQL)
        
        # Check/update schema version
        cursor = self._conn.execute("SELECT COUNT(*) FROM schema_version")
        if cursor.fetchone()[0] == 0:
            self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,)
            )
        
        self._conn.commit()
        _LOGGER.info("Database initialized successfully")

    async def async_close(self) -> None:
        """Close database connection."""
        if self._conn:
            await self.hass.async_add_executor_job(self._conn.close)
            _LOGGER.info("Database connection closed")

    async def log_event(
        self,
        event_type: str,
        state_from: str | None = None,
        state_to: str | None = None,
        sensor_id: str | None = None,
        sensor_name: str | None = None,
        correlation_score: int | None = None,
        notes: str | None = None,
    ) -> int:
        """Log an alarm event.
        
        Returns the event ID.
        """
        return await self.hass.async_add_executor_job(
            self._log_event_sync,
            event_type,
            state_from,
            state_to,
            sensor_id,
            sensor_name,
            correlation_score,
            notes,
        )

    def _log_event_sync(
        self,
        event_type: str,
        state_from: str | None,
        state_to: str | None,
        sensor_id: str | None,
        sensor_name: str | None,
        correlation_score: int | None,
        notes: str | None,
    ) -> int:
        """Log an alarm event synchronously."""
        if not self._conn:
            raise RuntimeError("Database not initialized")

        cursor = self._conn.execute(
            """
            INSERT INTO alarm_events 
            (event_type, state_from, state_to, sensor_id, sensor_name, 
             correlation_score, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                state_from,
                state_to,
                sensor_id,
                sensor_name,
                correlation_score,
                notes,
            ),
        )
        
        self._conn.commit()
        event_id = cursor.lastrowid
        
        _LOGGER.debug(
            "Logged event: id=%d, type=%s, sensor=%s",
            event_id,
            event_type,
            sensor_name,
        )
        
        return event_id

    async def log_escalation(
        self,
        event_id: int,
        channel: str,
        success: bool,
        retry_count: int = 0,
        response_time: float | None = None,
    ) -> None:
        """Log an escalation attempt."""
        await self.hass.async_add_executor_job(
            self._log_escalation_sync,
            event_id,
            channel,
            success,
            retry_count,
            response_time,
        )

    def _log_escalation_sync(
        self,
        event_id: int,
        channel: str,
        success: bool,
        retry_count: int,
        response_time: float | None,
    ) -> None:
        """Log an escalation attempt synchronously."""
        if not self._conn:
            raise RuntimeError("Database not initialized")

        self._conn.execute(
            """
            INSERT INTO alarm_escalations 
            (event_id, channel, success, retry_count, response_time)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, channel, success, retry_count, response_time),
        )
        
        self._conn.commit()
        
        _LOGGER.debug(
            "Logged escalation: event_id=%d, channel=%s, success=%s",
            event_id,
            channel,
            success,
        )

    async def get_events_today(self) -> int:
        """Get count of events today."""
        return await self.hass.async_add_executor_job(self._get_events_today_sync)

    def _get_events_today_sync(self) -> int:
        """Get count of events today synchronously."""
        if not self._conn:
            return 0

        today_start = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM alarm_events WHERE timestamp >= ?",
            (today_start,),
        )
        
        return cursor.fetchone()[0]

    async def get_recent_events(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent events."""
        return await self.hass.async_add_executor_job(
            self._get_recent_events_sync,
            limit,
        )

    def _get_recent_events_sync(self, limit: int) -> list[dict[str, Any]]:
        """Get recent events synchronously."""
        if not self._conn:
            return []

        self._conn.row_factory = sqlite3.Row
        cursor = self._conn.execute(
            """
            SELECT * FROM alarm_events 
            ORDER BY timestamp DESC 
            LIMIT ?
            """,
            (limit,),
        )
        
        events = [dict(row) for row in cursor.fetchall()]
        self._conn.row_factory = None
        
        return events

    async def export_events(
        self,
        output_path: str,
        days: int = 7,
    ) -> bool:
        """Export events to CSV file."""
        return await self.hass.async_add_executor_job(
            self._export_events_sync,
            output_path,
            days,
        )

    def _export_events_sync(self, output_path: str, days: int) -> bool:
        """Export events to CSV synchronously."""
        if not self._conn:
            return False

        try:
            import csv
            
            cutoff = datetime.now() - timedelta(days=days)
            
            cursor = self._conn.execute(
                """
                SELECT 
                    e.timestamp,
                    e.event_type,
                    e.state_from,
                    e.state_to,
                    e.sensor_name,
                    e.correlation_score,
                    e.notes,
                    GROUP_CONCAT(esc.channel || ':' || esc.success) as escalations
                FROM alarm_events e
                LEFT JOIN alarm_escalations esc ON e.id = esc.event_id
                WHERE e.timestamp >= ?
                GROUP BY e.id
                ORDER BY e.timestamp DESC
                """,
                (cutoff,),
            )
            
            with open(output_path, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                
                # Header
                writer.writerow([
                    'Timestamp',
                    'Event Type',
                    'State From',
                    'State To',
                    'Sensor',
                    'Score',
                    'Notes',
                    'Escalations',
                ])
                
                # Data
                for row in cursor:
                    writer.writerow(row)
            
            _LOGGER.info("Events exported to %s", output_path)
            return True
            
        except Exception as err:
            _LOGGER.error("Failed to export events: %s", err)
            return False

    async def cleanup_old_events(self, days: int = 365) -> int:
        """Delete events older than specified days.
        
        Returns number of deleted events.
        """
        return await self.hass.async_add_executor_job(
            self._cleanup_old_events_sync,
            days,
        )

    def _cleanup_old_events_sync(self, days: int) -> int:
        """Delete old events synchronously."""
        if not self._conn:
            return 0

        cutoff = datetime.now() - timedelta(days=days)
        
        cursor = self._conn.execute(
            "DELETE FROM alarm_events WHERE timestamp < ?",
            (cutoff,),
        )
        
        deleted = cursor.rowcount
        self._conn.commit()
        
        _LOGGER.info("Deleted %d old events (older than %d days)", deleted, days)
        
        return deleted

"""History compaction, persistent DatabaseSessionService, and async memory store."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Optional

import aiosqlite
from google.adk.plugins.context_filter_plugin import ContextFilterPlugin

try:
    from google.adk.apps.app import EventsCompactionConfig
except ImportError:  # pragma: no cover
    from google.adk.apps._configs import EventsCompactionConfig

try:
    from google.adk.sessions import DatabaseSessionService
except ImportError:  # pragma: no cover
    from google.adk.sessions.database_session_service import DatabaseSessionService

from concierge_agent.observability import (
    get_structured_logger,
    scrub_pii,
    traced_operation,
)

logger = get_structured_logger("concierge_agent.memory")
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def build_compaction_config(
    compaction_interval: int = 5,
    overlap_size: int = 1,
    token_threshold: int = 12000,
    event_retention_size: int = 4,
) -> EventsCompactionConfig:
    """Builds ADK EventsCompactionConfig combining sliding-window and token caps."""
    return EventsCompactionConfig(
        compaction_interval=compaction_interval,
        overlap_size=overlap_size,
        token_threshold=token_threshold,
        event_retention_size=event_retention_size,
    )


def build_context_filter_plugin(
    num_invocations_to_keep: int = 8,
    remove_amount: int = 2,
) -> ContextFilterPlugin:
    """Creates an ADK ContextFilterPlugin to prune stale conversational turns."""
    return ContextFilterPlugin(
        num_invocations_to_keep=num_invocations_to_keep,
        remove_amount=remove_amount,
    )


def create_persistent_session_service(
    db_url: str = "sqlite+aiosqlite:///./concierge_sessions.db",
) -> DatabaseSessionService:
    """Creates a persistent SQL-backed ADK DatabaseSessionService instance."""
    logger.info(
        "Initialized persistent ADK DatabaseSessionService",
        extra={"event": "session_service_initialized", "db_backend": db_url.split(":")[0]},
    )
    return DatabaseSessionService(db_url=db_url)


class TravelerPreferenceStore:
    """Persistent SQLite repository for consolidated traveler preferences."""

    def __init__(self, sqlite_path: str = "./concierge_preferences.db") -> None:
        self.sqlite_path = sqlite_path
        self._initialized = False

    async def initialize(self) -> None:
        """Creates the persistent traveler_preferences table if absent."""
        if self._initialized:
            return
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS traveler_preferences (
                    traveler_id TEXT PRIMARY KEY,
                    preferences_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.commit()
        self._initialized = True

    async def consolidate_preferences_async(
        self,
        traveler_id: str,
        raw_interaction_notes: str,
    ) -> dict[str, Any]:
        """Extracts, scrubs PII, and persists structured traveler preferences."""
        await self.initialize()
        sanitized_notes = scrub_pii(raw_interaction_notes)
        lowered = sanitized_notes.lower()

        extracted: dict[str, Any] = {
            "seat_preference": (
                "aisle" if "aisle" in lowered else ("window" if "window" in lowered else "standard")
            ),
            "hotel_style": "boutique" if "boutique" in lowered else "business",
            "transit_priority": (
                "near_transit" if "transit" in lowered or "train" in lowered else "flexible"
            ),
            "sanitized_summary": sanitized_notes,
        }
        updated_at = datetime.now(timezone.utc).isoformat()

        with traced_operation("memory.consolidate_preferences_async", {"traveler_id": traveler_id}):
            async with aiosqlite.connect(self.sqlite_path) as db:
                await db.execute(
                    """
                    INSERT INTO traveler_preferences (traveler_id, preferences_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(traveler_id) DO UPDATE SET
                        preferences_json = excluded.preferences_json,
                        updated_at = excluded.updated_at
                    """,
                    (traveler_id, json.dumps(extracted), updated_at),
                )
                await db.commit()

        logger.info(
            "Consolidated traveler preferences asynchronously",
            extra={"event": "memory_consolidated", "traveler_id": traveler_id},
        )
        return extracted

    async def retrieve_preferences(self, traveler_id: str) -> Optional[dict[str, Any]]:
        """Retrieves persisted traveler preferences across turns and restarts."""
        await self.initialize()
        async with aiosqlite.connect(self.sqlite_path) as db:
            cursor = await db.execute(
                "SELECT preferences_json FROM traveler_preferences WHERE traveler_id = ?",
                (traveler_id,),
            )
            row = await cursor.fetchone()
            return json.loads(row[0]) if row else None


def schedule_async_preference_consolidation(
    store: TravelerPreferenceStore,
    traveler_id: str,
    raw_interaction_notes: str,
) -> asyncio.Task[dict[str, Any]]:
    """Schedules non-blocking background memory consolidation via asyncio.create_task."""
    task = asyncio.create_task(
        store.consolidate_preferences_async(traveler_id, raw_interaction_notes)
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


async def drain_background_memory_tasks() -> None:
    """Awaits any pending background memory tasks during graceful shutdown or tests."""
    if _BACKGROUND_TASKS:
        await asyncio.gather(*list(_BACKGROUND_TASKS))
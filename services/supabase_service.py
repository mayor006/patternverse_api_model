"""Storage service — all database operations.

Two interchangeable backends behind one async interface:

* ``SupabaseStore``  — talks to a real Supabase project (production / staging).
* ``InMemoryStore``  — pure-Python fallback so the API runs end-to-end with
  nothing but Ollama. Data lives only for the process lifetime.

The backend is chosen automatically: if SUPABASE_URL + a key are configured
(and the ``supabase`` package imports), the Supabase backend is used; otherwise
the in-memory store is used and a warning is logged.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from typing import Dict, List, Optional
from uuid import UUID

from config import get_settings
from models.schemas import PatternObject

logger = logging.getLogger("patternverse.storage")


class StorageError(Exception):
    """Raised when a backend database operation fails."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── In-memory backend ──────────────────────────────────────────────────
class InMemoryStore:
    """Volatile store for local development / testing without Supabase."""

    backend_name = "in-memory"

    def __init__(self) -> None:
        self._sessions: Dict[str, dict] = {}
        self._messages: Dict[str, List[dict]] = defaultdict(list)
        self._patterns: List[dict] = []

    async def create_session(self, session_id: str, user_id: str) -> dict:
        row = {
            "session_id": session_id,
            "user_id": user_id,
            "created_at": _now_iso(),
            "status": "active",
            "turn": 0,
        }
        self._sessions[session_id] = row
        return row

    async def get_session(self, session_id: str) -> Optional[dict]:
        return self._sessions.get(session_id)

    async def update_session(
        self, session_id: str, status: Optional[str] = None, turn: Optional[int] = None
    ) -> None:
        row = self._sessions.get(session_id)
        if row is None:
            raise StorageError(f"Session {session_id} not found")
        if status is not None:
            row["status"] = status
        if turn is not None:
            row["turn"] = turn

    async def add_message(self, session_id: str, role: str, content: str) -> None:
        self._messages[session_id].append(
            {"role": role, "content": content, "timestamp": _now_iso()}
        )

    async def get_messages(self, session_id: str) -> List[dict]:
        return list(self._messages.get(session_id, []))

    async def save_pattern(
        self, session_id: str, user_id: str, pattern: PatternObject
    ) -> dict:
        row = {
            "session_id": session_id,
            "user_id": user_id,
            **pattern.model_dump(),
            "created_at": _now_iso(),
        }
        self._patterns.append(row)
        return row

    async def get_pattern_by_session(self, session_id: str) -> Optional[dict]:
        matches = [p for p in self._patterns if p["session_id"] == session_id]
        return matches[-1] if matches else None

    async def get_patterns_by_user(self, user_id: str) -> List[dict]:
        return [p for p in self._patterns if p["user_id"] == user_id]


# ── Supabase backend ───────────────────────────────────────────────────
class SupabaseStore:
    """Supabase-backed store. Sync client calls are offloaded to a threadpool."""

    backend_name = "supabase"

    def __init__(self, client) -> None:  # noqa: ANN001 — supabase.Client
        self._client = client

    async def _run(self, fn):
        from starlette.concurrency import run_in_threadpool

        try:
            return await run_in_threadpool(fn)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Supabase operation failed")
            raise StorageError(str(exc)) from exc

    async def create_session(self, session_id: str, user_id: str) -> dict:
        def _op():
            return (
                self._client.table("sessions")
                .insert(
                    {"session_id": session_id, "user_id": user_id, "status": "active", "turn": 0}
                )
                .execute()
            )

        result = await self._run(_op)
        return result.data[0] if result.data else {}

    async def get_session(self, session_id: str) -> Optional[dict]:
        def _op():
            return (
                self._client.table("sessions")
                .select("*")
                .eq("session_id", session_id)
                .limit(1)
                .execute()
            )

        result = await self._run(_op)
        return result.data[0] if result.data else None

    async def update_session(
        self, session_id: str, status: Optional[str] = None, turn: Optional[int] = None
    ) -> None:
        payload: dict = {}
        if status is not None:
            payload["status"] = status
        if turn is not None:
            payload["turn"] = turn
        if not payload:
            return

        def _op():
            return (
                self._client.table("sessions")
                .update(payload)
                .eq("session_id", session_id)
                .execute()
            )

        await self._run(_op)

    async def add_message(self, session_id: str, role: str, content: str) -> None:
        def _op():
            return (
                self._client.table("messages")
                .insert(
                    {
                        "session_id": session_id,
                        "role": role,
                        "content": content,
                        "timestamp": _now_iso(),
                    }
                )
                .execute()
            )

        await self._run(_op)

    async def get_messages(self, session_id: str) -> List[dict]:
        def _op():
            return (
                self._client.table("messages")
                .select("role, content, timestamp")
                .eq("session_id", session_id)
                .order("timestamp", desc=False)
                .order("id", desc=False)
                .execute()
            )

        result = await self._run(_op)
        return result.data or []

    async def save_pattern(
        self, session_id: str, user_id: str, pattern: PatternObject
    ) -> dict:
        def _op():
            return (
                self._client.table("patterns")
                .insert({"session_id": session_id, "user_id": user_id, **pattern.model_dump()})
                .execute()
            )

        result = await self._run(_op)
        return result.data[0] if result.data else {}

    async def get_pattern_by_session(self, session_id: str) -> Optional[dict]:
        def _op():
            return (
                self._client.table("patterns")
                .select("*")
                .eq("session_id", session_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

        result = await self._run(_op)
        return result.data[0] if result.data else None

    async def get_patterns_by_user(self, user_id: str) -> List[dict]:
        def _op():
            return (
                self._client.table("patterns")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .execute()
            )

        result = await self._run(_op)
        return result.data or []


# ── Backend selection ──────────────────────────────────────────────────
@lru_cache
def get_store():
    """Return the active store singleton, chosen from configuration."""
    settings = get_settings()
    if settings.supabase_active:
        try:
            from supabase import create_client

            client = create_client(settings.supabase_url, settings.supabase_key)
            logger.info("Storage backend: Supabase (%s)", settings.supabase_url)
            return SupabaseStore(client)
        except ImportError:
            logger.warning(
                "supabase package not installed; falling back to in-memory store. "
                "Run `pip install supabase` to enable the Supabase backend."
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not init Supabase (%s); using in-memory store.", exc)
    elif settings.supabase_url and settings.supabase_anon_key:
        # URL + anon key present, but no service key: RLS would block all server
        # operations, so we deliberately stay on the in-memory store.
        logger.warning(
            "SUPABASE_URL is set but SUPABASE_SERVICE_KEY is missing — RLS would "
            "block the anon key for server-side ops. Staying on the IN-MEMORY store. "
            "Add the service-role key to activate Supabase."
        )
    else:
        logger.warning(
            "SUPABASE_URL / key not configured — using the IN-MEMORY store. "
            "Data is volatile and resets on restart. Set SUPABASE_* for persistence."
        )
    return InMemoryStore()

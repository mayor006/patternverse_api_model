"""Mirror session orchestration — shared by /session and /mirror/chat routes.

Turn-based Mirror flow:
- Turns 1-5  : questions only.
- Turns 6-9  : synthesize if a pattern is clearly visible, else keep asking.
- Turn 10+   : force synthesis.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from fastapi import HTTPException

from models.schemas import (
    Message,
    PatternObject,
    SessionDetailResponse,
    SessionEndResponse,
    SessionReplyResponse,
    SessionStartResponse,
)
from services import conversation
from services.supabase_service import get_store

logger = logging.getLogger("patternverse.mirror_session")


def pattern_from_row(row: dict | None) -> PatternObject | None:
    if not row:
        return None
    return PatternObject(
        pattern_name=row["pattern_name"],
        pattern_summary=row["pattern_summary"],
        trigger=row["trigger"],
        response=row["response"],
        insight=row["insight"],
        next_step=row["next_step"],
    )


async def start_session(user_id: str) -> SessionStartResponse:
    store = get_store()
    session_id = str(uuid4())
    await store.create_session(session_id, user_id)

    opening = await conversation.generate_opening()
    await store.add_message(session_id, "assistant", opening)
    await store.update_session(session_id, turn=1)

    return SessionStartResponse(session_id=session_id, message=opening, turn=1)


async def reply(session_id: str, user_message: str) -> SessionReplyResponse:
    store = get_store()
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("status") == "complete":
        pattern = pattern_from_row(await store.get_pattern_by_session(session_id))
        if pattern is not None:
            return SessionReplyResponse(
                type="synthesis",
                content=pattern,
                turn=session.get("turn", 0),
                session_complete=True,
            )
        raise HTTPException(status_code=409, detail="This session has already ended.")

    await store.add_message(session_id, "user", user_message)
    history = await store.get_messages(session_id)
    upcoming_turn = int(session.get("turn", 0)) + 1

    pattern: PatternObject | None = None
    if upcoming_turn < conversation.SYNTHESIS_MIN_TURN:
        text = await conversation.converse(history, upcoming_turn)
        if conversation.parse_pattern(text) is not None:
            text = conversation.FALLBACK_QUESTION
    elif upcoming_turn < conversation.FORCE_SYNTHESIS_TURN:
        text = await conversation.converse(history, upcoming_turn, allow_synthesis=True)
        pattern = conversation.parse_pattern(text)
    else:
        pattern, text = await conversation.synthesize(history)
        if pattern is None:
            text = conversation.FALLBACK_QUESTION

    if pattern is not None:
        await store.add_message(session_id, "assistant", text)
        await store.save_pattern(session_id, session["user_id"], pattern)
        await store.update_session(session_id, status="complete", turn=upcoming_turn)
        return SessionReplyResponse(
            type="synthesis",
            content=pattern,
            turn=upcoming_turn,
            session_complete=True,
        )

    await store.add_message(session_id, "assistant", text)
    await store.update_session(session_id, status="active", turn=upcoming_turn)
    return SessionReplyResponse(
        type="question",
        content=text,
        turn=upcoming_turn,
        session_complete=False,
    )


async def get_session(session_id: str) -> SessionDetailResponse:
    store = get_store()
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    raw_messages = await store.get_messages(session_id)
    messages = [
        Message(role=m["role"], content=m["content"], timestamp=m["timestamp"])
        for m in raw_messages
    ]
    pattern = pattern_from_row(await store.get_pattern_by_session(session_id))

    return SessionDetailResponse(
        session_id=session["session_id"],
        user_id=session["user_id"],
        status=session["status"],
        messages=messages,
        pattern=pattern,
        turn=int(session.get("turn", 0)),
    )


async def end_session(session_id: UUID) -> SessionEndResponse:
    store = get_store()
    sid = str(session_id)
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    existing = pattern_from_row(await store.get_pattern_by_session(sid))
    if existing is not None:
        await store.update_session(sid, status="complete")
        return SessionEndResponse(session_id=session_id, status="complete", pattern=existing)

    turn = int(session.get("turn", 0))
    if turn >= conversation.SYNTHESIS_MIN_TURN:
        history = await store.get_messages(sid)
        pattern, text = await conversation.synthesize(history)
        if pattern is not None:
            await store.add_message(sid, "assistant", text)
            await store.save_pattern(sid, session["user_id"], pattern)
            await store.update_session(sid, status="complete", turn=turn)
            return SessionEndResponse(
                session_id=session_id, status="complete", pattern=pattern
            )
        await store.update_session(sid, status="complete")
        return SessionEndResponse(
            session_id=session_id,
            status="complete",
            pattern=None,
            message="Session ended, but the signal was too thin to name a pattern.",
        )

    await store.update_session(sid, status="complete")
    return SessionEndResponse(
        session_id=session_id,
        status="complete",
        pattern=None,
        message="Session ended before enough signal accumulated to surface a pattern.",
    )

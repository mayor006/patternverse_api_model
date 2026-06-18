"""Session routes: start, reply, fetch, end.

Implements the turn-based Mirror flow:
- Turns 1-5  : questions only.
- Turns 6-9  : synthesize if a pattern is clearly visible, else keep asking.
- Turn 10+   : force synthesis.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from models.schemas import (
    Message,
    PatternObject,
    SessionDetailResponse,
    SessionEndRequest,
    SessionEndResponse,
    SessionReplyRequest,
    SessionReplyResponse,
    SessionStartRequest,
    SessionStartResponse,
)
from services import conversation
from services.supabase_service import get_store

logger = logging.getLogger("patternverse.session")

router = APIRouter(prefix="/session", tags=["session"])


def _pattern_from_row(row: dict | None) -> PatternObject | None:
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


@router.post("/start", response_model=SessionStartResponse)
async def start_session(req: SessionStartRequest) -> SessionStartResponse:
    store = get_store()
    session_id = str(uuid4())
    await store.create_session(session_id, req.user_id)

    opening = await conversation.generate_opening()
    await store.add_message(session_id, "assistant", opening)
    await store.update_session(session_id, turn=1)

    return SessionStartResponse(session_id=session_id, message=opening, turn=1)


@router.post("/reply", response_model=SessionReplyResponse)
async def reply(req: SessionReplyRequest) -> SessionReplyResponse:
    store = get_store()
    session_id = str(req.session_id)
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # A completed session is terminal — never resurrect it via /reply.
    if session.get("status") == "complete":
        pattern = _pattern_from_row(await store.get_pattern_by_session(session_id))
        if pattern is not None:
            # Return the synthesis idempotently.
            return SessionReplyResponse(
                type="synthesis",
                content=pattern,
                turn=session.get("turn", 0),
                session_complete=True,
            )
        # Ended without a pattern (e.g. ended early) — closed, not continuable.
        raise HTTPException(status_code=409, detail="This session has already ended.")

    # Record the user's message, then reconstruct full history.
    await store.add_message(session_id, "user", req.user_message)
    history = await store.get_messages(session_id)

    # `turn` tracks assistant turns produced; the upcoming one is +1.
    upcoming_turn = int(session.get("turn", 0)) + 1

    pattern: PatternObject | None = None
    if upcoming_turn < conversation.SYNTHESIS_MIN_TURN:
        # Turns 1-5: listen + reflect + ask, never synthesize. If the model still
        # insists on emitting a pattern, fall back to a generic question.
        text = await conversation.converse(history)
        if conversation.parse_pattern(text) is not None:
            text = conversation.FALLBACK_QUESTION
    elif upcoming_turn < conversation.FORCE_SYNTHESIS_TURN:
        # Turns 6-9: synthesize only if a clear pattern emerges, else keep listening.
        text = await conversation.converse(history, allow_synthesis=True)
        pattern = conversation.parse_pattern(text)
    else:
        # Turn 10+: force synthesis (retries once on invalid JSON).
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


@router.get("/{session_id}", response_model=SessionDetailResponse)
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
    pattern = _pattern_from_row(await store.get_pattern_by_session(session_id))

    return SessionDetailResponse(
        session_id=session["session_id"],
        user_id=session["user_id"],
        status=session["status"],
        messages=messages,
        pattern=pattern,
        turn=int(session.get("turn", 0)),
    )


@router.post("/end", response_model=SessionEndResponse)
async def end_session(req: SessionEndRequest) -> SessionEndResponse:
    store = get_store()
    session_id = str(req.session_id)
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Already have a pattern? Return it.
    existing = _pattern_from_row(await store.get_pattern_by_session(session_id))
    if existing is not None:
        await store.update_session(session_id, status="complete")
        return SessionEndResponse(
            session_id=req.session_id, status="complete", pattern=existing
        )

    turn = int(session.get("turn", 0))
    # Force synthesis if we have enough material (turn >= 6).
    if turn >= conversation.SYNTHESIS_MIN_TURN:
        history = await store.get_messages(session_id)
        pattern, text = await conversation.synthesize(history)
        if pattern is not None:
            await store.add_message(session_id, "assistant", text)
            await store.save_pattern(session_id, session["user_id"], pattern)
            await store.update_session(session_id, status="complete", turn=turn)
            return SessionEndResponse(
                session_id=req.session_id, status="complete", pattern=pattern
            )
        await store.update_session(session_id, status="complete")
        return SessionEndResponse(
            session_id=req.session_id,
            status="complete",
            pattern=None,
            message="Session ended, but the signal was too thin to name a pattern.",
        )

    # Too early to synthesize honestly — end without fabricating one.
    await store.update_session(session_id, status="complete")
    return SessionEndResponse(
        session_id=req.session_id,
        status="complete",
        pattern=None,
        message="Session ended before enough signal accumulated to surface a pattern.",
    )

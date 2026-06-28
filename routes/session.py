"""Session routes: start, reply, fetch, end.

Legacy paths under ``/session/*``. Prefer ``/mirror/chat`` for new Flutter
integration — both share ``services.mirror_session``.
"""

from __future__ import annotations

from fastapi import APIRouter

from models.schemas import (
    SessionDetailResponse,
    SessionEndRequest,
    SessionEndResponse,
    SessionReplyRequest,
    SessionReplyResponse,
    SessionStartRequest,
    SessionStartResponse,
)
from services import mirror_session

router = APIRouter(prefix="/session", tags=["session"])


@router.post("/start", response_model=SessionStartResponse)
async def start_session(req: SessionStartRequest) -> SessionStartResponse:
    return await mirror_session.start_session(req.user_id)


@router.post("/reply", response_model=SessionReplyResponse)
async def reply(req: SessionReplyRequest) -> SessionReplyResponse:
    return await mirror_session.reply(str(req.session_id), req.user_message)


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str) -> SessionDetailResponse:
    return await mirror_session.get_session(session_id)


@router.post("/end", response_model=SessionEndResponse)
async def end_session(req: SessionEndRequest) -> SessionEndResponse:
    return await mirror_session.end_session(req.session_id)

"""Mirror chat routes — the Flutter-facing API for the AI Mirror conversation.

Primary entry point: ``POST /mirror/chat`` — one endpoint for the chat screen:
- Start:  ``{ "user_id": "..." }`` → opening question + ``session_id``
- Reply:  ``{ "session_id": "...", "message": "..." }`` → next question or synthesis

Legacy ``/session/*`` routes remain available and share the same logic.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional, Union
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from models.schemas import (
    PatternObject,
    SessionDetailResponse,
    SessionEndRequest,
    SessionEndResponse,
)
from services import mirror_session

logger = logging.getLogger("patternverse.mirror")

router = APIRouter(prefix="/mirror", tags=["mirror"])


class MirrorChatRequest(BaseModel):
    """Unified chat payload — start a session or send the next user message."""

    user_id: Optional[str] = None
    session_id: Optional[UUID] = None
    message: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _start_or_reply(self) -> "MirrorChatRequest":
        if self.session_id is None:
            if not self.user_id:
                raise ValueError("user_id is required when starting a new mirror chat.")
            if self.message is not None:
                raise ValueError(
                    "Do not send message when starting — call again with session_id to reply."
                )
        else:
            if not self.message or not self.message.strip():
                raise ValueError("message is required when replying to an existing session.")
        return self


class MirrorChatResponse(BaseModel):
    session_id: UUID
    type: Literal["opening", "question", "synthesis"]
    content: Union[PatternObject, str]
    turn: int
    session_complete: bool


@router.post("/chat", response_model=MirrorChatResponse)
async def mirror_chat(req: MirrorChatRequest) -> MirrorChatResponse:
    if req.session_id is None:
        started = await mirror_session.start_session(req.user_id)  # type: ignore[arg-type]
        logger.info("Mirror chat started for user=%s session=%s", req.user_id, started.session_id)
        return MirrorChatResponse(
            session_id=started.session_id,
            type="opening",
            content=started.message,
            turn=started.turn,
            session_complete=False,
        )

    reply = await mirror_session.reply(str(req.session_id), req.message.strip())  # type: ignore[union-attr]
    logger.info(
        "Mirror chat turn session=%s type=%s turn=%s",
        req.session_id,
        reply.type,
        reply.turn,
    )
    return MirrorChatResponse(
        session_id=req.session_id,
        type=reply.type,
        content=reply.content,
        turn=reply.turn,
        session_complete=reply.session_complete,
    )


@router.get("/chat/{session_id}", response_model=SessionDetailResponse)
async def get_mirror_chat(session_id: UUID) -> SessionDetailResponse:
    return await mirror_session.get_session(str(session_id))


@router.post("/chat/end", response_model=SessionEndResponse)
async def end_mirror_chat(req: SessionEndRequest) -> SessionEndResponse:
    return await mirror_session.end_session(req.session_id)

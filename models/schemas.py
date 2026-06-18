"""Pydantic data models for the Patternverse MIS API.

These cover the core domain objects (Message, PatternObject, Session) plus the
request/response envelopes for each endpoint.
"""

from datetime import datetime
from typing import List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field


# ── Core domain models ────────────────────────────────────────────────
class Message(BaseModel):
    role: str  # "system" | "assistant" | "user"
    content: str
    timestamp: datetime


class PatternObject(BaseModel):
    """The synthesis output — the 'mirror' returned once a pattern is clear."""

    pattern_name: str
    pattern_summary: str
    trigger: str
    response: str
    insight: str
    next_step: str


class Session(BaseModel):
    session_id: UUID
    user_id: str
    created_at: datetime
    status: str  # "active" | "complete"
    messages: List[Message] = Field(default_factory=list)
    pattern: Optional[PatternObject] = None
    turn: int = 0


# ── Request models ────────────────────────────────────────────────────
class SessionStartRequest(BaseModel):
    user_id: str


class SessionReplyRequest(BaseModel):
    session_id: UUID
    user_message: str


class SessionEndRequest(BaseModel):
    session_id: UUID


# ── Response models ───────────────────────────────────────────────────
class SessionStartResponse(BaseModel):
    session_id: UUID
    message: str
    turn: int


class SessionReplyResponse(BaseModel):
    type: Literal["question", "synthesis"]
    # A plain question string, or the structured PatternObject on synthesis.
    # PatternObject is listed first so dict payloads validate as a pattern.
    content: Union[PatternObject, str]
    turn: int
    session_complete: bool


class SessionDetailResponse(BaseModel):
    session_id: UUID
    user_id: str
    status: str  # "active" | "complete"
    messages: List[Message] = Field(default_factory=list)
    pattern: Optional[PatternObject] = None
    turn: int


class PatternsResponse(BaseModel):
    patterns: List[PatternObject] = Field(default_factory=list)


class SessionEndResponse(BaseModel):
    session_id: UUID
    status: str
    pattern: Optional[PatternObject] = None
    message: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    environment: str  # "development" | "production"
    model: str  # "ollama" | "huggingface"

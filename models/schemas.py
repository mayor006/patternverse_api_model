"""Pydantic data models for the Patternverse MIS API.

These cover the core domain objects (Message, PatternObject, Session) plus the
request/response envelopes for each endpoint.
"""

from datetime import datetime
from typing import Dict, List, Literal, Optional, Union
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


# ── Growth Area insight models ─────────────────────────────────────────
# The fine-tuned model scores six personal-growth dimensions (0-100) from a
# user's profile/progress data. Input shape mirrors the payload built by the
# Supabase edge function; output is the strict `{ "areas": [...] }` contract.
class OnboardingResponse(BaseModel):
    question: str = ""
    answer: str = ""


class ProgressMetrics(BaseModel):
    loops_identified: int = 0
    top_loops: List[str] = Field(default_factory=list)
    trigger_clarity_pct: int = 0
    reaction_autopilot_pct: int = 0
    interrupt_rate_pct: int = 0
    # spotter | namer | mapper | interruptor | rewriter | stabilizer; empty when
    # the user has no progression yet (so no ladder floor is applied).
    ladder_stage: str = ""


class UserMemory(BaseModel):
    entries_count: int = 0
    active_loops: List[str] = Field(default_factory=list)
    emotion_distribution: Dict[str, float] = Field(default_factory=dict)
    narrative_summary: str = ""


class AreaScore(BaseModel):
    id: str
    value: int


class GrowthAreaRequest(BaseModel):
    goals: List[str] = Field(default_factory=list)
    stress_frequency: str = ""
    happiness_level: str = ""
    ai_onboarding_responses: List[OnboardingResponse] = Field(default_factory=list)
    progress_metrics: ProgressMetrics = Field(default_factory=ProgressMetrics)
    user_memory: UserMemory = Field(default_factory=UserMemory)
    # Sane anchor the model leans on when signal is thin. Optional: the server
    # computes a heuristic anchor when the client omits it.
    fallback_scores: List[AreaScore] = Field(default_factory=list)


class GrowthAreaResponse(BaseModel):
    areas: List[AreaScore]
    source: Literal["finetuned", "profile"]  # observability: which path scored it

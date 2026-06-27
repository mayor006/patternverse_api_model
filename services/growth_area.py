"""Growth Area insight scoring.

Replaces the third-party LLM call that scored the **Growth Area** insight with
this self-hosted inference endpoint. The flow mirrors the Supabase edge
function:

1. Build the §3 input payload (with input hygiene + a heuristic anchor).
2. Ask the model (via the swappable ``model_service``) for the six scores.
3. Strictly validate the output against the §4 contract.
4. On ANY failure — model unavailable, non-JSON, missing/invalid ids — fall back
   to the deterministic heuristic so the card always renders.

The heuristic (``score_from_profile``) is the behavioral ground truth: the same
rules the edge function and the Dart client encode. It is both the fallback and
the label generator for fine-tuning.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from models.schemas import AreaScore, GrowthAreaRequest
from services.model_service import ModelUnavailableError, generate_response

logger = logging.getLogger("patternverse.growth_area")

# ── Canonical areas (fixed order + base scores) ────────────────────────
AREA_IDS: List[str] = [
    "mental_health",
    "growth_mindset",
    "relationships",
    "personal_development",
    "self_awareness",
    "stress_management",
]

_BASE_SCORES: Dict[str, int] = {area_id: 42 for area_id in AREA_IDS}
_BASE_SCORES["self_awareness"] = 46

# Heuristic-path clamp (the model path clamps to the full [0, 100]).
_HEURISTIC_MIN = 32
_HEURISTIC_MAX = 94

# Input-hygiene caps (keep in sync with the edge function).
_MAX_ONBOARDING = 8
_MAX_QUESTION_CHARS = 280
_MAX_ANSWER_CHARS = 700
_MAX_NARRATIVE_CHARS = 600
_MAX_ACTIVE_LOOPS = 5


# ── The system prompt the model is fine-tuned against ──────────────────
GROWTH_PROMPT = """You are the Patternverse Growth Area scorer. You read a single JSON object describing one user's profile and progress, and you return six personal-growth scores as strict JSON.

The six areas (use these exact ids, all six, exactly once):
- mental_health
- growth_mindset
- relationships
- personal_development
- self_awareness
- stress_management

Each value is a WHOLE NUMBER from 0 to 100 representing the user's CURRENT growth focus, readiness, and available signal in that area. This is NOT a diagnosis or clinical assessment.

RULES
- Output ONLY a JSON object of the form {"areas":[{"id":"mental_health","value":0}, ...]} and nothing else. No prose, no code fences.
- Include all six ids exactly once. Values are whole numbers 0-100.
- Use ONLY the provided data. Do NOT infer protected traits, trauma history, or medical conditions.
- If the data is thin or contradictory, stay MODERATE — near the provided fallback_scores — rather than overconfident.
- Read free-text answers (goals, ai_onboarding_responses, user_memory) for meaning, not just keywords.

DOMAIN GLOSSARY
- loop: a repeating thought/behavior pattern the user is tracking.
- ladder_stage: the user's progression spotter -> namer -> mapper -> interruptor -> rewriter -> stabilizer (increasing mastery).
- trigger_clarity_pct: how clearly the user identifies what triggers a loop (raises self_awareness).
- interrupt_rate_pct: how often they interrupt a loop before reacting (raises stress_management).
- reaction_autopilot_pct: how often they react on autopilot."""


# ── Heuristic scoring rules (the ground truth / fallback) ──────────────
# Goal phrases — substring match, additive. A rule fires once per goal string
# whenever any of its triggers appears.
_GOAL_RULES: List[Tuple[Tuple[str, ...], Dict[str, int]]] = [
    (("thoughts that loop",), {"mental_health": 14, "self_awareness": 11, "stress_management": 12}),
    (("reactions you can't explain",), {"self_awareness": 14, "stress_management": 8, "growth_mindset": 6}),
    (("relationship",), {"relationships": 16, "self_awareness": 6, "mental_health": 4}),
    (("work",), {"stress_management": 12, "personal_development": 8, "mental_health": 6}),
    (("decision",), {"growth_mindset": 12, "personal_development": 10, "self_awareness": 8}),
    (("don't know", "not sure"), {"self_awareness": 10, "growth_mindset": 8}),
]

_STRESS_RULES: List[Tuple[str, Dict[str, int]]] = [
    ("almost every day", {"stress_management": 16, "mental_health": 10, "self_awareness": 5}),
    ("a few times a week", {"stress_management": 12, "mental_health": 7}),
    ("now and then", {"stress_management": 6}),
    ("i don't know yet", {"self_awareness": 8, "growth_mindset": 5}),
]

_HAPPINESS_RULES: List[Tuple[str, Dict[str, int]]] = [
    ("noticing something i couldn't see alone", {"self_awareness": 12, "growth_mindset": 5}),
    ("catching myself before i react", {"stress_management": 12, "growth_mindset": 8}),
    ("understanding someone in my life better", {"relationships": 14, "self_awareness": 6}),
    ("making sense of a decision", {"personal_development": 12, "growth_mindset": 8}),
    ("somewhere honest to think", {"mental_health": 8, "self_awareness": 8}),
]

# Keyword density — +4 per distinct matched keyword, capped at +22 per area.
_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "mental_health": ("anxiety", "anxious", "mood", "depress", "sad", "lonely", "overwhelm", "worry", "panic"),
    "growth_mindset": ("stuck", "grow", "learn", "improve", "change", "progress", "better"),
    "relationships": ("partner", "family", "friend", "relationship", "marriage", "parent", "colleague"),
    "personal_development": ("decision", "habit", "career", "goal", "routine", "discipline", "purpose"),
    "self_awareness": ("pattern", "notice", "trigger", "aware", "reflect", "realize", "understand"),
    "stress_management": ("stress", "burnout", "deadline", "pressure", "tension", "overwhelmed"),
}
_KEYWORD_STEP = 4
_KEYWORD_CAP = 22

# Ladder-stage floors (increasing mastery).
_LADDER_FLOOR: Dict[str, int] = {
    "spotter": 48,
    "namer": 56,
    "mapper": 64,
    "interruptor": 72,
    "rewriter": 80,
    "stabilizer": 88,
}


def _normalize_text(text: str) -> str:
    """Lowercase and straighten curly apostrophes so substring rules match."""
    return (text or "").replace("\u2019", "'").lower()


def _clamp(value: float, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(round(value))))


def score_from_profile(req: GrowthAreaRequest) -> List[Dict[str, int]]:
    """Deterministic heuristic — the behavioral spec and the safe fallback.

    Reproduces the rules in §5 of the implementation note and clamps each area
    to ``[32, 94]``. With empty input it returns the base scores (42, with
    self_awareness at 46).
    """
    scores: Dict[str, float] = dict(_BASE_SCORES)

    # Goal phrases (substring, additive, once per goal per matching rule).
    for goal in req.goals:
        text = _normalize_text(goal)
        for triggers, deltas in _GOAL_RULES:
            if any(t in text for t in triggers):
                for area, delta in deltas.items():
                    scores[area] += delta

    # stress_frequency — apply the first matching bucket.
    stress = _normalize_text(req.stress_frequency)
    for trigger, deltas in _STRESS_RULES:
        if trigger in stress:
            for area, delta in deltas.items():
                scores[area] += delta
            break

    # happiness_level — apply the first matching bucket.
    happiness = _normalize_text(req.happiness_level)
    for trigger, deltas in _HAPPINESS_RULES:
        if trigger in happiness:
            for area, delta in deltas.items():
                scores[area] += delta
            break

    # Keyword density across goals + answers + memory text.
    corpus_parts: List[str] = list(req.goals)
    for r in req.ai_onboarding_responses:
        corpus_parts.append(r.answer)
    corpus_parts.append(req.user_memory.narrative_summary)
    corpus_parts.extend(req.user_memory.active_loops)
    corpus = _normalize_text(" ".join(corpus_parts))
    for area, keywords in _KEYWORDS.items():
        matched = sum(1 for kw in keywords if kw in corpus)
        scores[area] += min(_KEYWORD_CAP, matched * _KEYWORD_STEP)

    # Engagement depth.
    n_responses = len(req.ai_onboarding_responses)
    if n_responses > 0:
        depth_boost = min(8, n_responses * 2)
        scores["self_awareness"] += depth_boost
        scores["growth_mindset"] += max(3, depth_boost // 2)

    # Progress metrics (max where noted, additive for loops).
    scores.update(_apply_progress_metrics(scores, req))

    return [
        {"id": area_id, "value": _clamp(scores[area_id], _HEURISTIC_MIN, _HEURISTIC_MAX)}
        for area_id in AREA_IDS
    ]


def _apply_progress_metrics(
    scores: Dict[str, float], req: GrowthAreaRequest
) -> Dict[str, float]:
    pm = req.progress_metrics
    out = dict(scores)

    out["self_awareness"] = max(out["self_awareness"], pm.trigger_clarity_pct)
    out["stress_management"] = max(out["stress_management"], pm.interrupt_rate_pct)
    out["growth_mindset"] = max(out["growth_mindset"], pm.interrupt_rate_pct - 4)

    n = pm.loops_identified
    if n > 0:
        out["self_awareness"] += min(12, n * 2)
        out["growth_mindset"] += min(8, n)

    floor = _LADDER_FLOOR.get(_normalize_text(pm.ladder_stage))
    if floor is not None:
        out["growth_mindset"] = max(out["growth_mindset"], floor)
        out["self_awareness"] = max(out["self_awareness"], floor - 4)

    return out


# ── Building the model input (with input hygiene) ──────────────────────
def _build_model_payload(req: GrowthAreaRequest, fallback: List[Dict[str, int]]) -> dict:
    responses = []
    for r in req.ai_onboarding_responses[:_MAX_ONBOARDING]:
        responses.append(
            {
                "question": r.question[:_MAX_QUESTION_CHARS],
                "answer": r.answer[:_MAX_ANSWER_CHARS],
            }
        )

    return {
        "goals": req.goals,
        "stress_frequency": req.stress_frequency,
        "happiness_level": req.happiness_level,
        "ai_onboarding_responses": responses,
        "progress_metrics": req.progress_metrics.model_dump(),
        "user_memory": {
            "entries_count": req.user_memory.entries_count,
            "active_loops": req.user_memory.active_loops[:_MAX_ACTIVE_LOOPS],
            "emotion_distribution": req.user_memory.emotion_distribution,
            "narrative_summary": req.user_memory.narrative_summary[:_MAX_NARRATIVE_CHARS],
        },
        "fallback_scores": fallback,
    }


# ── Model call + strict validation ─────────────────────────────────────
def _extract_json_block(text: str) -> Optional[str]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return None


def _coerce_int(value: object) -> Optional[int]:
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, str):
        try:
            return int(round(float(value.strip())))
        except (ValueError, TypeError):
            return None
    return None


def _parse_and_validate(text: str) -> Optional[List[Dict[str, int]]]:
    """Return six clamped scores if the model output is fully schema-valid, else None.

    Enforces the §4 contract: a JSON object with an ``areas`` array containing
    all six ids exactly once, each with an integer value in ``[0, 100]``.
    """
    block = _extract_json_block(text)
    if block is None:
        return None
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        logger.info("Growth-area model returned non-JSON output.")
        return None

    raw_areas = data.get("areas") if isinstance(data, dict) else None
    if not isinstance(raw_areas, list):
        return None

    lookup: Dict[str, object] = {}
    for item in raw_areas:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            lookup[item["id"]] = item.get("value")

    result: List[Dict[str, int]] = []
    for area_id in AREA_IDS:
        num = _coerce_int(lookup.get(area_id))
        if num is None:
            logger.info("Growth-area model output missing/invalid id %r.", area_id)
            return None
        result.append({"id": area_id, "value": _clamp(num, 0, 100)})
    return result


async def _maybe_score_with_model(payload: dict) -> Optional[List[Dict[str, int]]]:
    messages = [
        {"role": "system", "content": GROWTH_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        text = await generate_response(messages)
    except ModelUnavailableError as exc:
        logger.warning("Growth-area model unavailable, using heuristic: %s", exc)
        return None
    return _parse_and_validate(text)


# ── Public entry point ─────────────────────────────────────────────────
async def score_growth_area(req: GrowthAreaRequest) -> Tuple[List[Dict[str, int]], str]:
    """Score the six growth areas. Returns ``(areas, source)``.

    ``source`` is ``"finetuned"`` when the model produced a schema-valid result,
    or ``"profile"`` when we fell back to the deterministic heuristic.
    """
    heuristic = score_from_profile(req)

    # Anchor the model with the client's fallback_scores when present, else the
    # freshly computed heuristic.
    if req.fallback_scores:
        anchor = [{"id": a.id, "value": a.value} for a in req.fallback_scores]
    else:
        anchor = heuristic

    payload = _build_model_payload(req, anchor)
    model_areas = await _maybe_score_with_model(payload)
    if model_areas is not None:
        return model_areas, "finetuned"
    return heuristic, "profile"


def to_area_scores(areas: List[Dict[str, int]]) -> List[AreaScore]:
    return [AreaScore(id=a["id"], value=a["value"]) for a in areas]

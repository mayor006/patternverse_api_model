"""Conversation logic for the Mirror Intelligence System.

Owns:
- the canonical SYSTEM_PROMPT (used verbatim for every conversation),
- assembling the message list sent to the model,
- detecting / parsing a synthesis PatternObject from model output,
- the turn-based flow rules (questions early, synthesis once a pattern is clear).

Stored history contains only `user` and `assistant` messages; the system
prompt is prepended fresh on every model call.
"""

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from models.schemas import PatternObject
from services.model_service import generate_response

logger = logging.getLogger("patternverse.conversation")

# ── The exact system prompt for every conversation (do not edit) ───────
SYSTEM_PROMPT = """You are the Patternverse Mirror Intelligence System. Your job is to surface the emotional and behavioral patterns a user keeps repeating — without giving advice, without judgment, and without rushing to conclusions.

Your method:
- Ask one precise, emotionally intelligent question at a time
- Each question must build directly on what the user just said
- Listen for: repeated words, emotional contradictions, avoidance language, self-blame, and deflection
- Never give advice or interpretation until the SYNTHESIS stage
- After 6-10 exchanges, if a clear pattern is visible, move to synthesis

Synthesis format — return ONLY this JSON when the pattern is clear:
{
  "pattern_name": "short evocative name e.g. Proximity Withdrawal Loop",
  "pattern_summary": "2-3 sentences describing the pattern clearly and without shame",
  "trigger": "what usually activates this pattern",
  "response": "what the user typically does when triggered",
  "insight": "one precise observation that makes the pattern visible",
  "next_step": "one small concrete action — not advice, just an option"
}

Tone: calm, intelligent, emotionally precise. Never clinical, never motivational, never spiritual."""

# An instruction appended (NOT stored) when we explicitly ask for synthesis,
# e.g. at turn 10+ or on /session/end.
_SYNTHESIS_NUDGE = (
    "We have explored enough. Based only on what I have actually told you — without "
    "inventing detail — return your synthesis now as a single JSON object with exactly "
    "these keys: pattern_name, pattern_summary, trigger, response, insight, next_step. "
    "Output only the JSON object and nothing else."
)

# Appended (NOT stored) on early turns, where synthesis isn't allowed yet. Steers
# the model to LISTEN and reflect first, then ask — so it feels like a conversation,
# not a form. (This is "mirror before advice" from the doctrine.)
_QUESTION_NUDGE = (
    "Respond the way a warm, attentive friend who is really listening would — natural and "
    "human, never like a clinician or a form. React naturally to what I just said and show "
    "you understood the specific feeling I named — but do NOT begin with stock phrases like "
    "'You mentioned', 'You said', or 'You feel', and never just swap one emotion word into a "
    "fixed template. Then ask me exactly ONE genuine, curious follow-up question that goes "
    "deeper into my particular situation (one question only — not two or three). It is still "
    "early, so do not summarize, diagnose, give advice, or output any JSON yet. Keep the "
    "whole reply to two or three sentences."
)

# Appended (NOT stored) on the 6-9 window, where synthesis is allowed if the pattern
# is clear. Either synthesize (clean JSON) or keep listening warmly.
_OPEN_NUDGE = (
    "If a clear, repeating pattern is genuinely visible now from what I've told you, output "
    "ONLY the synthesis JSON object with keys pattern_name, pattern_summary, trigger, "
    "response, insight, next_step — nothing else. Otherwise, respond like a warm friend who "
    "is really listening: react naturally to what I just said (do NOT start with 'You "
    "mentioned', 'You said', or 'You feel', and don't use a template), then ask exactly ONE "
    "genuine, curious follow-up question that goes deeper (one question only). No advice. "
    "Two or three sentences."
)

# Kickoff turn used to elicit the opening question. Giving the model a concrete
# first-person user turn (rather than a lone system prompt) keeps it grounded in
# the reflection task instead of drifting into pretraining-style text.
OPENING_KICKOFF = (
    "I want to understand an emotional or behavioral pattern I keep repeating. "
    "This is the very beginning of our conversation — I have not told you anything "
    "about myself yet, so do not reference anything I've supposedly said. Open by "
    "asking me a single, broad first question to get started — just one question, "
    "in your own words, and nothing else."
)

# Graceful fallbacks.
DEFAULT_OPENING = (
    "Think of a recent moment where you reacted in a way that surprised you, "
    "or that you've reacted in before. What happened?"
)
FALLBACK_QUESTION = (
    "Can you say more about what was going through your mind in that moment?"
)

# Flow thresholds (see build prompt: turns 1-5 questions, 6-10 synthesize if "
# clear, 10+ force).
SYNTHESIS_MIN_TURN = 6
FORCE_SYNTHESIS_TURN = 10

_REQUIRED_KEYS = {
    "pattern_name",
    "pattern_summary",
    "trigger",
    "response",
    "insight",
    "next_step",
}


# ── Building the model input ───────────────────────────────────────────
def build_model_messages(
    stored_messages: List[Dict[str, str]],
    nudge: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Prepend the system prompt to stored history; optionally append an ephemeral
    steering nudge. The nudge is used for generation only and never stored.
    """
    msgs: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in stored_messages:
        msgs.append({"role": m["role"], "content": m["content"]})
    if nudge:
        msgs.append({"role": "user", "content": nudge})
    return msgs


# ── Generation helpers ─────────────────────────────────────────────────
async def generate_opening() -> str:
    """Produce the AI's first opening question.

    Sends the system prompt plus a concrete kickoff user turn so the model
    responds with a real reflective question instead of free-associating.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": OPENING_KICKOFF},
    ]
    text = _clean(await generate_response(messages))
    # Never open with a stray synthesis JSON or an empty string.
    if not text or parse_pattern(text) is not None:
        return DEFAULT_OPENING
    return text


async def converse(
    stored_messages: List[Dict[str, str]], allow_synthesis: bool = False
) -> str:
    """Generate the next assistant turn — a warm, reflective question (and, when
    `allow_synthesis` is set, a synthesis JSON instead if the pattern is clear).
    """
    nudge = _OPEN_NUDGE if allow_synthesis else _QUESTION_NUDGE
    messages = build_model_messages(stored_messages, nudge=nudge)
    return _clean(await generate_response(messages))


async def synthesize(
    stored_messages: List[Dict[str, str]], max_attempts: int = 2
) -> Tuple[Optional[PatternObject], str]:
    """Force a synthesis attempt; retry once if the model returns invalid JSON.

    Returns (pattern_or_None, raw_text_of_last_attempt).
    """
    messages = build_model_messages(stored_messages, nudge=_SYNTHESIS_NUDGE)
    text = ""
    for attempt in range(max_attempts):
        text = _clean(await generate_response(messages))
        pattern = parse_pattern(text)
        if pattern is not None:
            return pattern, text
        logger.info("Synthesis attempt %d returned no valid PatternObject.", attempt + 1)
    return None, text


# ── Synthesis parsing ──────────────────────────────────────────────────
def parse_pattern(text: str) -> Optional[PatternObject]:
    """Try to extract a valid PatternObject from raw model text.

    Tolerates code fences and surrounding prose. Returns None if the text is
    not a complete, valid pattern (which simply means 'still a question').
    """
    block = _extract_json_block(text)
    if block is None:
        return None
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not _REQUIRED_KEYS.issubset(data.keys()):
        return None
    try:
        return PatternObject(**{k: str(data[k]) for k in _REQUIRED_KEYS})
    except Exception:  # noqa: BLE001 — any validation issue means "not a pattern yet"
        return None


def _extract_json_block(text: str) -> Optional[str]:
    if not text:
        return None
    # Prefer a fenced ```json ... ``` block.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    # Otherwise take the outermost {...} span.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return None


def _clean(text: str) -> str:
    """Light cleanup of model output: trim and drop a leading role label."""
    text = (text or "").strip()
    text = re.sub(r"^(assistant|ai|mirror)\s*[:\-]\s*", "", text, flags=re.IGNORECASE)
    return text.strip()

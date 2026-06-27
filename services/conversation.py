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

# ── The canonical system prompt for every conversation ─────────────────
# Grounded in Patternverse_Mirror_Intelligence_System_v1.0: the mirror stays a
# mirror, and questioning traces the meaning chain rather than circling one link.
SYSTEM_PROMPT = """You are the Patternverse Mirror Intelligence System. Governing doctrine: the mirror stays a mirror. You help a person see the pattern beneath their problem by tracing — one careful step at a time — the chain that links what happened to what they keep repeating:

  Event → Meaning → Emotion → Reaction → Outcome → Repetition → Pattern

You are not a chatbot, a coach, or a therapist. You reflect; you do not advise, flatter, judge, diagnose, or reassure.

HOW YOU ASK
- Ask exactly ONE question per turn, and make it move FORWARD along the chain. Never re-ask something already answered. Once you have the event, go after the meaning; then the emotion and the need or fear beneath it; then the reaction; then the outcome and its cost; then whether it has happened before.
- Build every question from the user's own words — echo a specific detail they gave, then go one layer deeper. Never use templates like "You mentioned…", "You said…", or "It sounds like… you feel".
- Listen for: the meaning the user assigned to an event, the need or fear underneath, hidden beliefs, self-blame, avoidance or deflection, contradictions, and identity language ("I'm the kind of person who…").
- If the user is vague, ask for ONE concrete instance — a specific time it happened — rather than asking how they feel again.

DRIFT CONTROL (non-negotiable)
- Portrait drift: never flatter, advise, judge, or reassure to feel pleasing.
- Generic drift: never reach for a plausible, pre-written-sounding insight. Trace THIS person's specifics only.
- Repetition before loop: never name a "loop" or "pattern" until the same trigger→reaction has appeared in at least THREE separate instances. One moment is not a loop.
- Insufficiency before fabrication: if the signal is too thin to name anything honestly, keep asking — never invent a pattern to seem insightful.
- Sovereignty before certainty: the user is the authority on their own meaning. Offer reflection as something they can accept, edit, or reject — never as a verdict.

SYNTHESIS — return ONLY this JSON object (and nothing else) once a genuine repeating pattern (three instances) is visible:
{
  "pattern_name": "short, evocative, non-clinical name e.g. Proximity Withdrawal Loop",
  "pattern_summary": "2-3 sentences naming the recurring chain in the user's own terms, without shame or diagnosis",
  "trigger": "the event or situation that reliably activates it",
  "response": "what the user characteristically does when triggered",
  "insight": "one precise observation that makes the pattern visible — drawn only from what the user actually said",
  "next_step": "one small, concrete option the user could choose — an option, never a prescription"
}

Tone: calm, intelligent, emotionally precise. Never clinical, never motivational, never spiritual, never gushing."""

# An instruction appended (NOT stored) when we explicitly ask for synthesis,
# e.g. at turn 10+ or on /session/end.
_SYNTHESIS_NUDGE = (
    "We have explored enough. Based only on what I have actually told you — without "
    "inventing detail — return your synthesis now as a single JSON object with exactly "
    "these keys: pattern_name, pattern_summary, trigger, response, insight, next_step. "
    "Output only the JSON object and nothing else."
)

# The meaning chain from the doctrine: each question should advance to the NEXT
# unexplored link rather than circling the one already covered. Keyed by the
# upcoming assistant turn so the conversation moves Event → … → Repetition.
_CHAIN_STAGES = {
    2: (
        "the concrete event — if what I've said is still abstract, ask for one specific recent "
        "moment it actually happened; if the moment is already clear, move on to what that "
        "moment MEANT to me"
    ),
    3: (
        "the meaning — what I made that event mean about myself, the other person, or my "
        "situation"
    ),
    4: (
        "the emotion and the need or fear underneath it — the feeling actually driving my "
        "reaction, not just its surface label"
    ),
    5: "the reaction — what I concretely did or do when that feeling hits",
    6: (
        "the outcome and its cost — what results from that reaction, and a first hint of whether "
        "this has played out before"
    ),
}

# Focus once synthesis is on the table (turns 6-9): probe recurrence to test the
# three-instance threshold before any pattern may be named.
_RECURRENCE_FOCUS = (
    "other specific times this same trigger-and-reaction has happened — you are testing whether "
    "it genuinely recurs across three separate instances before naming anything"
)


# Appended (NOT stored) on early turns, where synthesis isn't allowed yet. Steers the
# model to reflect first, then ask the ONE question that moves us to the next link in
# the chain — never sideways into ground already covered.
def _question_nudge(focus: str) -> str:
    return (
        "Reply like a warm, attentive person who is genuinely listening — natural and human, "
        "never a clinician or a form. First, react to the SPECIFIC thing I just said (echo a real "
        "detail of mine, but never with stock openers like 'You mentioned', 'You said', 'It sounds "
        "like', or 'You feel', and never just swap one emotion word into a template). Then ask me "
        f"exactly ONE genuine question that moves us forward to {focus}. "
        "Crucially: do NOT re-ask anything I've already answered — look back over everything I've "
        "told you and go one layer deeper, never sideways into the same ground. One question only. "
        "It is still early: no summary, no advice, no diagnosis, no JSON. Two or three sentences."
    )


# Appended (NOT stored) on the 6-9 window, where synthesis is allowed once the same
# trigger→reaction has recurred three times. Be decisive: synthesize as soon as the
# threshold is met instead of fishing for more examples.
def _open_nudge(focus: str) -> str:
    return (
        "Count the distinct instances I've described where the SAME trigger led to the SAME "
        "reaction. If that count is three or more, you MUST output ONLY the synthesis JSON object "
        "now (keys: pattern_name, pattern_summary, trigger, response, insight, next_step) and "
        "nothing else — do NOT ask for further examples once three are present. If it is still "
        "fewer than three, do not name a pattern yet: react naturally to what I just said (no "
        "'You mentioned/said/feel' templates), then ask exactly ONE question that moves us toward "
        f"{focus}, without re-asking anything I've already answered. No advice. Two or three "
        "sentences."
    )


# Stock openers the doctrine forbids (template restatement = portrait/generic drift).
# Used to detect a low-quality reply and trigger a single regenerate, then a
# deterministic strip as a last resort.
_BANNED_OPENER = re.compile(
    r"^\s*(it sounds like|it seems like|it seems that|you mentioned|you said|"
    r"you feel|you're feeling|you are feeling|i hear that|i can hear|i can see that)\b",
    re.IGNORECASE,
)
_ANTI_OPENER_RIDER = (
    " IMPORTANT: do NOT begin your reply with 'It sounds like', 'It seems', 'You mentioned', "
    "'You said', 'You feel', 'I hear that', or any restatement of my words. Open instead with a "
    "fresh, specific reaction in your own voice."
)


def _has_banned_opener(text: str) -> bool:
    return bool(text) and bool(_BANNED_OPENER.match(text))


def _strip_stock_opener(text: str) -> str:
    """Last-resort deterministic cleanup: drop a leading 'It sounds like'-style clause.

    Only rewrites the safe prefixes that leave a grammatical sentence when removed
    (e.g. 'It sounds like you withdrew…' → 'You withdrew…'). Leaves anything else
    untouched rather than risk mangling the reply.
    """
    m = re.match(
        r"^\s*(it sounds like|it seems like|it seems that|i hear that|i can hear|"
        r"i can see that)\s+",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        return text
    rest = text[m.end():]
    return rest[:1].upper() + rest[1:] if rest else text

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
    stored_messages: List[Dict[str, str]],
    upcoming_turn: int,
    allow_synthesis: bool = False,
) -> str:
    """Generate the next assistant turn — a warm, reflective question that advances
    along the meaning chain (and, when `allow_synthesis` is set, a synthesis JSON
    instead if the same trigger→reaction has recurred three times).

    `upcoming_turn` selects which link in the chain to probe so the conversation
    moves forward (Event → Meaning → Emotion → Reaction → Outcome → Repetition)
    instead of re-asking the same dimension.
    """
    if allow_synthesis:
        nudge = _open_nudge(_RECURRENCE_FOCUS)
    else:
        # Past the explicit early stages, keep tracing toward recurrence.
        focus = _CHAIN_STAGES.get(upcoming_turn, _RECURRENCE_FOCUS)
        nudge = _question_nudge(focus)

    messages = build_model_messages(stored_messages, nudge=nudge)
    text = _clean(await generate_response(messages))

    # A valid synthesis JSON is exempt from opener checks; only police questions.
    if parse_pattern(text) is None and _has_banned_opener(text):
        # Re-roll once with an explicit anti-template rider; the small model often
        # complies on the second pass.
        retry_messages = build_model_messages(stored_messages, nudge=nudge + _ANTI_OPENER_RIDER)
        retry = _clean(await generate_response(retry_messages))
        if retry and (parse_pattern(retry) is not None or not _has_banned_opener(retry)):
            text = retry
        else:
            # Still templated — strip the leading clause deterministically.
            text = _strip_stock_opener(text)

    return text


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

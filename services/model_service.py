"""Swappable model service.

A SINGLE entry point — ``generate_response(messages)`` — that routes to either
Ollama (development) or the Hugging Face Inference API (production) based on
``APP_ENV``. Both paths take the same input (a list of chat messages) and
return the same output (a plain text string), so the rest of the app never
needs to know which backend is live.

Switching backends requires ONLY changing APP_ENV.
"""

import logging
from typing import Dict, List

import httpx

from config import Settings, get_settings

logger = logging.getLogger("patternverse.model")

# Generous timeout: a local 7B model can take several seconds per turn,
# and HF cold-starts can be slow.
_REQUEST_TIMEOUT = httpx.Timeout(180.0, connect=10.0)


class ModelUnavailableError(Exception):
    """Raised when the active model backend cannot be reached or errors out."""


async def generate_response(messages: List[Dict[str, str]]) -> str:
    """Route to the active backend and return the assistant's text reply.

    `messages` is a list of {"role": "system"|"user"|"assistant", "content": str}.
    """
    settings = get_settings()
    if settings.is_production:
        return await _generate_huggingface(messages, settings)
    return await _generate_ollama(messages, settings)


# ── Ollama (development) ───────────────────────────────────────────────
async def _generate_ollama(messages: List[Dict[str, str]], settings: Settings) -> str:
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.7},
    }
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Ollama returned %s: %s", exc.response.status_code, exc.response.text)
        raise ModelUnavailableError(
            f"Ollama responded with status {exc.response.status_code}. "
            "Is the model pulled? Try `ollama pull mistral`."
        ) from exc
    except httpx.HTTPError as exc:
        logger.error("Could not reach Ollama at %s: %s", url, exc)
        raise ModelUnavailableError(
            f"Could not reach Ollama at {settings.ollama_base_url}. "
            "Is `ollama serve` running?"
        ) from exc

    content = (data.get("message") or {}).get("content", "")
    return content.strip()


# ── Hugging Face Inference API (production) ────────────────────────────
async def _generate_huggingface(messages: List[Dict[str, str]], settings: Settings) -> str:
    if not settings.hf_api_token:
        raise ModelUnavailableError(
            "HF_API_TOKEN is not set but APP_ENV=production. "
            "Add a Hugging Face token to use the production backend."
        )

    prompt = _to_mistral_prompt(messages)
    headers = {"Authorization": f"Bearer {settings.hf_api_token}"}
    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": 512,
            "temperature": 0.7,
            "return_full_text": False,
        },
        "options": {"wait_for_model": True},
    }
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(settings.hf_model_url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("HF returned %s: %s", exc.response.status_code, exc.response.text)
        raise ModelUnavailableError(
            f"Hugging Face responded with status {exc.response.status_code}."
        ) from exc
    except httpx.HTTPError as exc:
        logger.error("Could not reach Hugging Face: %s", exc)
        raise ModelUnavailableError("Could not reach the Hugging Face Inference API.") from exc

    # HF text-generation returns [{"generated_text": "..."}]; be defensive.
    if isinstance(data, list) and data:
        return str(data[0].get("generated_text", "")).strip()
    if isinstance(data, dict):
        if "generated_text" in data:
            return str(data["generated_text"]).strip()
        if "error" in data:
            raise ModelUnavailableError(f"Hugging Face error: {data['error']}")
    raise ModelUnavailableError("Unexpected response shape from Hugging Face.")


def _to_mistral_prompt(messages: List[Dict[str, str]]) -> str:
    """Render chat messages into the Mistral-Instruct prompt template.

    Mistral instruct models have no dedicated system role, so the system prompt
    is folded into the first user instruction:

        <s>[INST] {system}\\n\\n{user1} [/INST] {assistant1}</s>[INST] {user2} [/INST]
    """
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    convo = [m for m in messages if m["role"] in ("user", "assistant")]

    # The template must begin with a user turn. If the stored history starts
    # with the assistant's opening question, inject a minimal kickoff turn.
    if not convo or convo[0]["role"] != "user":
        convo = [{"role": "user", "content": _KICKOFF}] + convo

    parts: List[str] = []
    system_injected = False
    for m in convo:
        if m["role"] == "user":
            text = m["content"]
            if not system_injected and system:
                text = f"{system}\n\n{text}"
                system_injected = True
            parts.append(f"[INST] {text} [/INST]")
        else:  # assistant
            parts.append(f" {m['content']}</s>")
    return "<s>" + "".join(parts)


# Used to elicit the very first question when no user turn exists yet.
_KICKOFF = "I'm ready to begin. Ask me your first question, one at a time."

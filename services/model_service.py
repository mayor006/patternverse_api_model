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


# ── Hugging Face Inference Providers (production) ──────────────────────
async def _generate_huggingface(messages: List[Dict[str, str]], settings: Settings) -> str:
    """Call the HF router's OpenAI-compatible chat completions endpoint.

    The router accepts the chat ``messages`` list directly (system/user/assistant
    roles) and auto-selects an available inference provider for the model.
    """
    if not settings.hf_api_token:
        raise ModelUnavailableError(
            "HF_API_TOKEN is not set but APP_ENV=production. "
            "Add a Hugging Face token to use the production backend."
        )

    headers = {"Authorization": f"Bearer {settings.hf_api_token}"}
    payload = {
        "model": settings.hf_model,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.7,
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

    if isinstance(data, dict):
        if "error" in data:
            raise ModelUnavailableError(f"Hugging Face error: {data['error']}")
        choices = data.get("choices")
        if choices:
            content = (choices[0].get("message") or {}).get("content", "")
            return str(content).strip()
    raise ModelUnavailableError("Unexpected response shape from Hugging Face.")

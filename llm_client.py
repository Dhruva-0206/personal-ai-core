"""
Thin wrapper around Nebius Token Factory's OpenAI-compatible endpoint.

Two responsibilities only:
  - extract_json(): send a prompt, get back parsed JSON
  - embed(): get an embedding vector for a piece of text

Checks both `message.content` and `message.reasoning_content` before
giving up on a response — see _message_text() below for what's actually
been verified about when each is populated (it's more nuanced than "all
reasoning models always use reasoning_content").
"""
import json
import logging
from openai import OpenAI

import config

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not config.NEBIUS_API_KEY:
            raise RuntimeError(
                "NEBIUS_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        _client = OpenAI(api_key=config.NEBIUS_API_KEY, base_url=config.NEBIUS_BASE_URL)
    return _client


def _message_text(message) -> str:
    """
    Return whichever of content / reasoning_content is populated.

    Verified via isolated testing (see CLAUDE.md) that
    nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B does NOT expose reasoning_content
    at all when using native tool-calling — the attribute doesn't exist on
    the message object in that mode. content=None in that case just means
    the model chose to call a tool instead of answering in text, which is
    normal OpenAI-compatible behavior, not the reasoning-mode issue we
    originally flagged. The reasoning_content fallback below is kept as a
    defensive measure in case other Nemotron variants (Super/Ultra) or
    non-tool-calling calls behave differently — not because we've
    confirmed it happens, but because it's cheap insurance we already have
    in place. Don't assume it applies until tested for those cases
    specifically.
    """
    content = getattr(message, "content", None)
    if content:
        return content
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning:
        logger.warning(
            "Model returned reasoning_content instead of content — "
            "this model is running in reasoning mode."
        )
        return reasoning
    return ""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # drop the opening ``` or ```json line and the closing ``` line
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def extract_json(system_prompt: str, user_text: str, max_retries: int = 1) -> dict:
    """
    Send a structured-extraction prompt and parse the response as JSON.
    Returns {} on unrecoverable failure so callers can fall back gracefully
    instead of crashing an entire ingestion run on one bad response.
    """
    client = get_client()
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=config.EXTRACTION_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                temperature=0.1,
            )
            raw = _message_text(response.choices[0].message)
            cleaned = _strip_code_fence(raw)
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning("Extraction JSON parse failed (attempt %d): %s", attempt + 1, e)
        except Exception as e:
            last_error = e
            logger.error("Extraction call failed (attempt %d): %s", attempt + 1, e)

    logger.error("Extraction gave up after %d attempts: %s", max_retries + 1, last_error)
    return {}


def embed(text: str) -> list[float]:
    client = get_client()
    response = client.embeddings.create(model=config.EMBEDDING_MODEL, input=text)
    return response.data[0].embedding

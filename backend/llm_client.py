"""
AegisLayer — LLM Client (v2)
=============================
Async HTTP bridge to any OpenAI-compatible chat completions endpoint.

Recommended FREE provider: Groq
  → Sign up at https://console.groq.com (no credit card required)
  → Copy your API key → set LLM_API_KEY=gsk_...
  → Set LLM_BASE_URL=https://api.groq.com/openai/v1
  → Set LLM_MODEL=llama-3.1-70b-versatile   (or llama-3.1-8b-instant)

Other providers:
  OpenAI:       LLM_BASE_URL=https://api.openai.com/v1
  Fireworks AI: LLM_BASE_URL=https://api.fireworks.ai/inference/v1
  vLLM local:   LLM_BASE_URL=http://localhost:8080/v1

Graceful degradation
--------------------
No LLM_API_KEY → contextual mock response (pipeline still functions fully).
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger("aegislayer.llm_client")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LLM_API_KEY    = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL   = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL      = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_TIMEOUT    = float(os.getenv("LLM_TIMEOUT_S", "30"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))
LLM_SYSTEM_MSG = os.getenv(
    "LLM_SYSTEM_MSG",
    (
        "You are a precise, helpful enterprise AI assistant. "
        "Answer the user's question accurately and concisely. "
        "Some words in their message are placeholder tokens like [PERSON_1] or [EMAIL_A] — "
        "treat them as opaque proper nouns and reference them naturally in your response."
    ),
)


# ---------------------------------------------------------------------------
# Smart Mock Response Generator
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r'\[(PERSON|ORG|LOC|EMAIL|APIKEY|IP|PHONE|CARD|ENTITY)_([A-Z0-9]+)\]')

_INTENT_PATTERNS = {
    "summarise"   : r'\b(summar(?:ise|ize)|recap|overview|brief)\b',
    "explain"     : r'\b(explain|describe|what is|what are|how does)\b',
    "analyse"     : r'\b(analys[ie]|evaluat|assess|review)\b',
    "list"        : r'\b(list|enumerate|show|give me)\b',
    "question"    : r'\?',
    "email_draft" : r'\b(draft|write|compose|send)\b.*\b(email|message|note)\b',
    "code"        : r'\b(code|script|implement|write.*function)\b',
}


def _build_mock_response(sanitised_prompt: str) -> str:
    """
    Generate a contextually aware mock response based on the sanitised prompt.
    Analyses intent and entity types present to craft a plausible response.
    """
    tokens = _TOKEN_RE.findall(sanitised_prompt)
    token_types = [t for t, _ in tokens]
    n_tokens = len(tokens)

    prompt_lower = sanitised_prompt.lower()

    # Detect intent
    intent = "general"
    for key, pattern in _INTENT_PATTERNS.items():
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            intent = key
            break

    # Build entity context description
    entity_parts = []
    if "PERSON" in token_types:
        count = token_types.count("PERSON")
        entity_parts.append(f"{count} individual{'s' if count > 1 else ''}")
    if "ORG" in token_types:
        count = token_types.count("ORG")
        entity_parts.append(f"{count} organisation{'s' if count > 1 else ''}")
    if "EMAIL" in token_types:
        entity_parts.append(f"{token_types.count('EMAIL')} email address{'es' if token_types.count('EMAIL') > 1 else ''}")
    if "IP" in token_types:
        entity_parts.append(f"{token_types.count('IP')} IP address{'es' if token_types.count('IP') > 1 else ''}")
    if "APIKEY" in token_types:
        entity_parts.append(f"{token_types.count('APIKEY')} API credential{'s' if token_types.count('APIKEY') > 1 else ''}")

    entity_summary = ", ".join(entity_parts) if entity_parts else "general content"

    # Compose response by intent
    if intent == "summarise":
        response = (
            f"Here is a summary of the provided context:\n\n"
            f"The communication involves {entity_summary}. "
            f"Based on the discussion points raised, the key takeaways are:\n\n"
            f"• The matter involves coordination between the identified parties\n"
            f"• Action items and timelines have been referenced in the exchange\n"
            f"• Follow-up is recommended to confirm outstanding decisions\n\n"
            f"[AegisLayer · {n_tokens} entit{'ies' if n_tokens != 1 else 'y'} were redacted from this prompt before transmission]"
        )
    elif intent == "explain":
        response = (
            f"Based on the provided context involving {entity_summary}:\n\n"
            f"The subject matter relates to the entities and systems referenced in your prompt. "
            f"A detailed explanation would depend on the specific domain — "
            f"the placeholder tokens indicate sensitive references that have been protected by AegisLayer.\n\n"
            f"[AegisLayer · {n_tokens} entit{'ies' if n_tokens != 1 else 'y'} were redacted from this prompt before transmission]"
        )
    elif intent == "email_draft":
        response = (
            f"Draft communication:\n\n"
            f"Dear [PERSON_1],\n\n"
            f"I am writing in connection with the matters we discussed. "
            f"Please find the relevant details attached and do not hesitate to reach out at [EMAIL_A] "
            f"should you require any clarification.\n\n"
            f"Best regards\n\n"
            f"[AegisLayer · {n_tokens} entit{'ies' if n_tokens != 1 else 'y'} were redacted from this prompt before transmission]"
        )
    elif intent == "question":
        response = (
            f"In response to your query regarding {entity_summary}:\n\n"
            f"The answer depends on the specific context of the placeholder entities referenced. "
            f"Once AegisLayer restores the original values, the response will contain the "
            f"precise information relevant to your situation.\n\n"
            f"[AegisLayer · {n_tokens} entit{'ies' if n_tokens != 1 else 'y'} were redacted from this prompt before transmission]"
        )
    else:
        response = (
            f"I have processed your request. The prompt referenced {entity_summary}, "
            f"all of which were safely redacted before transmission.\n\n"
            f"To receive a substantive AI-generated response, configure an LLM provider:\n"
            f"  1. Visit https://console.groq.com and create a free account\n"
            f"  2. Copy your API key (starts with gsk_...)\n"
            f"  3. Add to backend/.env:\n"
            f"       LLM_API_KEY=gsk_...\n"
            f"       LLM_BASE_URL=https://api.groq.com/openai/v1\n"
            f"       LLM_MODEL=llama-3.1-70b-versatile\n\n"
            f"[AegisLayer · {n_tokens} entit{'ies' if n_tokens != 1 else 'y'} were redacted from this prompt before transmission]"
        )

    return response


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Async HTTP client for OpenAI-compatible chat completions.
    Re-uses a single httpx.AsyncClient across requests for connection pooling.
    """

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url = LLM_BASE_URL,
                timeout  = httpx.Timeout(LLM_TIMEOUT, connect=5.0),
                headers  = {
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type" : "application/json",
                    "User-Agent"   : "AegisLayer/1.0",
                },
            )
        return self._client

    async def chat(
        self,
        sanitised_prompt : str,
        model            : Optional[str] = None,
        system_override  : Optional[str] = None,
    ) -> str:
        """
        Send *sanitised_prompt* to the LLM and return the assistant reply text.
        Falls back to a contextual mock response if no API key is configured.
        """
        # ── Mock path ─────────────────────────────────────────────────────
        if not LLM_API_KEY:
            logger.warning("LLMClient: LLM_API_KEY not set — returning contextual mock response")
            return _build_mock_response(sanitised_prompt)

        # ── Live path ──────────────────────────────────────────────────────
        payload = {
            "model"      : model or LLM_MODEL,
            "messages"   : [
                {"role": "system", "content": system_override or LLM_SYSTEM_MSG},
                {"role": "user"  , "content": sanitised_prompt},
            ],
            "max_tokens" : LLM_MAX_TOKENS,
            "temperature": 0.7,
        }

        max_retries = 3
        for attempt in range(max_retries):
            t0 = time.perf_counter()
            try:
                import asyncio
                client = await self._get_client()
                response = await client.post("/chat/completions", json=payload)
                response.raise_for_status()
                data    = response.json()
                elapsed = (time.perf_counter() - t0) * 1000
                content = data["choices"][0]["message"]["content"]
                logger.info(
                    "LLMClient: response received in %.1f ms (%d chars) on attempt %d",
                    elapsed, len(content), attempt + 1
                )
                return content

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 1.5
                    logger.warning("LLMClient: HTTP %s. Retrying in %.1fs...", status, wait_time)
                    await asyncio.sleep(wait_time)
                    continue
                logger.error("LLMClient: HTTP %s — %s", status, exc.response.text[:300])
                return (
                    f"LLM API returned HTTP {status}.\n"
                    f"Details: {exc.response.text[:300]}"
                )

            except httpx.RequestError as exc:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 1.5
                    logger.warning("LLMClient: Network error. Retrying in %.1fs...", wait_time)
                    await asyncio.sleep(wait_time)
                    continue
                logger.error("LLMClient: network error — %s", exc)
                return f"Could not reach LLM endpoint ({LLM_BASE_URL}).\nReason: {exc}"

            except Exception as exc:  # noqa: BLE001
                logger.error("LLMClient: unexpected error — %s", exc)
                return f"Unexpected LLM client error: {exc}"

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @property
    def endpoint(self) -> str:
        return LLM_BASE_URL

    @property
    def configured(self) -> bool:
        return bool(LLM_API_KEY)


# Module-level singleton
llm_client = LLMClient()

"""
AegisLayer — Module C: Ephemeral Session Vault
===============================================
Maintains strict per-session token ↔ plaintext mappings.
Rules:
  - Same original value within a session always maps to the same token.
  - Different entity types use independent counters + different label prefixes.
  - Sessions are wiped after a completed round-trip to minimise threat surface.
  - Thread-safe via asyncio.Lock per session.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger("aegislayer.vault")


# ---------------------------------------------------------------------------
# Token label → human-readable prefix map
# ---------------------------------------------------------------------------
_LABEL_PREFIX: Dict[str, str] = {
    "PERSON"      : "PERSON",
    "ORG"         : "ORG",
    "LOCATION"    : "LOC",
    "EMAIL"       : "EMAIL",
    "API_KEY"     : "APIKEY",
    "IPV4"        : "IP",
    "PHONE"       : "PHONE",
    "CREDIT_CARD" : "CARD",
    "UNKNOWN"     : "ENTITY",
}

# Counter style: numeric for PER/ORG/LOC (important for semantic parity),
# alpha for structured data (EMAIL, IP, etc.)
_NUMERIC_TYPES = {"PERSON", "ORG", "LOCATION", "UNKNOWN"}


def _counter_to_label(entity_type: str, count: int) -> str:
    """Convert a per-type counter to a token suffix."""
    prefix = _LABEL_PREFIX.get(entity_type, "ENTITY")
    if entity_type in _NUMERIC_TYPES:
        return f"[{prefix}_{count}]"
    else:
        # Alpha suffix: A, B, C … Z, AA, AB …
        suffix = _int_to_alpha(count)
        return f"[{prefix}_{suffix}]"


def _int_to_alpha(n: int) -> str:
    """Convert integer 1..∞ to A, B, … Z, AA, AB, …"""
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result


# ---------------------------------------------------------------------------
# Per-session State
# ---------------------------------------------------------------------------

class _Session:
    __slots__ = ("token_map", "reverse_map", "counters", "lock")

    def __init__(self) -> None:
        # token → original
        self.token_map   : Dict[str, str] = {}
        # original → token (for dedup within session)
        self.reverse_map : Dict[str, str] = {}
        # per entity-type counter
        self.counters    : Dict[str, int] = {}
        self.lock        : asyncio.Lock   = asyncio.Lock()

    def _next_token(self, entity_type: str, original: str) -> str:
        """Return existing token if value seen, else mint a new one."""
        if original in self.reverse_map:
            return self.reverse_map[original]
        self.counters[entity_type] = self.counters.get(entity_type, 0) + 1
        token = _counter_to_label(entity_type, self.counters[entity_type])
        self.token_map[token]      = original
        self.reverse_map[original] = token
        return token


# ---------------------------------------------------------------------------
# Public Vault Interface
# ---------------------------------------------------------------------------

class SessionVault:
    """
    Global singleton managing all in-flight session states.
    Usage::

        vault = SessionVault()

        # During sanitisation pass
        token = await vault.tokenize(session_id, "john.doe@corp.com", "EMAIL")

        # After LLM round-trip
        restored = await vault.detokenize(session_id, llm_response_text)

        # Cleanup
        await vault.clear_session(session_id)
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, _Session] = {}
        self._global_lock = asyncio.Lock()

    async def _get_or_create(self, session_id: str) -> _Session:
        async with self._global_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = _Session()
                logger.debug("Vault: created session %s", session_id)
            return self._sessions[session_id]

    async def tokenize(
        self,
        session_id  : str,
        original    : str,
        entity_type : str,
    ) -> str:
        """
        Register an original value and return its deterministic placeholder token.
        Idempotent: calling twice with the same original yields the same token.
        """
        session = await self._get_or_create(session_id)
        async with session.lock:
            token = session._next_token(entity_type.upper(), original)
        logger.debug("Vault[%s] tokenize %r → %s", session_id, original, token)
        return token

    async def detokenize(self, session_id: str, text: str) -> str:
        """
        Replace all placeholder tokens in *text* with their original values.
        Handles case-mutation and bracket dropping by LLMs gracefully.
        """
        session = await self._get_or_create(session_id)
        async with session.lock:
            mapping = dict(session.token_map)  # snapshot

        if not mapping:
            return text

        # Sort descending to prevent subset matches ([PERSON_10] before [PERSON_1])
        sorted_tokens = sorted(mapping.keys(), key=len, reverse=True)
        
        # Clean tokens to get raw identifier strings (e.g., "EMAIL_A" out of "[EMAIL_A]")
        raw_ids = [tok.strip("[]") for tok in sorted_tokens]
        
        # Build case-insensitive regex that accommodates missing/dropped brackets optionally
        # Matches: [EMAIL_A], [email_a], EMAIL_A, email_a securely
        pattern_str = "|".join([rf"\[?({re.escape(raw_id)})\]?" for raw_id in raw_ids])
        pattern = re.compile(pattern_str, re.IGNORECASE)

        def replacer(m: re.Match) -> str:
            # Find which captured group hit to reconstruct the exact standard token key
            matched_text = m.group(0)
            
            # Normalize match back to your uppercase dictionary key format "[TOKEN_ID]"
            extracted_raw = re.sub(r"[\[\]]", "", matched_text).upper()
            standard_key = f"[{extracted_raw}]"
            
            return mapping.get(standard_key, matched_text)

        restored = pattern.sub(replacer, text)
        logger.debug("Vault[%s] resilient detokenize done (%d subs)", session_id, len(mapping))
        return restored

    async def get_mapping(self, session_id: str) -> Dict[str, str]:
        """Return a copy of the current token→original map for audit purposes."""
        session = await self._get_or_create(session_id)
        async with session.lock:
            return dict(session.token_map)

    async def clear_session(self, session_id: str) -> None:
        """
        Wipe all state for a session.
        Call this after the full round-trip to minimise memory footprint
        and shrink the active threat surface.
        """
        async with self._global_lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info("Vault: cleared session %s", session_id)

    async def active_sessions(self) -> int:
        async with self._global_lock:
            return len(self._sessions)


# Singleton instance shared across the application
vault = SessionVault()

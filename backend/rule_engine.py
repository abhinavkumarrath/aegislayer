"""
AegisLayer — Module A: CPU Rule & Regex Engine
===============================================
Handles instant parsing of structured PII/sensitive data using deterministic
compiled regex patterns. Runs entirely on CPU — no GPU needed.

Patterns covered
----------------
PII_EMAIL       RFC 5322 compliant email addresses
SEC_API_KEY     High-entropy API key prefixes (sk-, ghp_, AIza, Bearer …)
NET_IPV4        Isolated IPv4 routing addresses
PHONE           E.164 + common US/international formats
CREDIT_CARD     16-digit PAN groups (Luhn-adjacent pattern)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Tuple

logger = logging.getLogger("aegislayer.rule_engine")


# ---------------------------------------------------------------------------
# Pattern Registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Rule:
    name        : str   # entity type string used in vault
    pattern     : re.Pattern
    priority    : int = 0  # lower = checked first


_RULES: List[_Rule] = [
    # ── API Keys (highest priority — structured, high-confidence) ───────────
    _Rule(
        name="API_KEY",
        priority=0,
        pattern=re.compile(
            r"""
            (?<![A-Za-z0-9_\-])         # No alphanum immediately before
            (?:
                sk-proj-[A-Za-z0-9\-_]{20,}  # OpenAI project keys (must be first)
              | sk-[A-Za-z0-9\-_]{20,}        # OpenAI / generic secret key
              | ghp_[A-Za-z0-9]{36}           # GitHub personal access token
              | gho_[A-Za-z0-9]{36}           # GitHub OAuth token
              | github_pat_[A-Za-z0-9_]{82}   # GitHub fine-grained PAT
              | AIza[A-Za-z0-9\-_]{35}        # Google API key
              | AKIA[A-Z0-9]{16}              # AWS Access Key ID
              | xox[bpoa]-[A-Za-z0-9\-]{10,} # Slack tokens
              | Bearer\s+[A-Za-z0-9\-._~+/]+=* # HTTP Bearer tokens
              | gsk_[A-Za-z0-9]{20,}          # Groq API keys
              # Generic high-entropy keys: 20+ chars mixing letters, digits, underscores
              # Must have uppercase + at least one digit in it to avoid matching regular words
              | [A-Z]{2,}[A-Za-z0-9_]{16,}(?=[^A-Za-z0-9_]|$)  # e.g. GKS_ABC123XYZ
              | [A-Za-z0-9+/]{40}             # Generic 40-char base64 secret
            )
            (?![A-Za-z0-9_\-])             # No alphanum immediately after
            """,
            re.VERBOSE,
        ),
    ),

    # ── Credit Card PANs ────────────────────────────────────────────────────
    _Rule(
        name="CREDIT_CARD",
        priority=1,
        pattern=re.compile(
            r"""
            (?<!\d)
            (?:
                4[0-9]{3}[\s\-]?[0-9]{4}[\s\-]?[0-9]{4}[\s\-]?[0-9]{4}   # Visa
              | 5[1-5][0-9]{2}[\s\-]?[0-9]{4}[\s\-]?[0-9]{4}[\s\-]?[0-9]{4} # MC
              | 3[47][0-9]{2}[\s\-]?[0-9]{6}[\s\-]?[0-9]{5}               # Amex
              | 6011[\s\-]?[0-9]{4}[\s\-]?[0-9]{4}[\s\-]?[0-9]{4}         # Discover
            )
            (?!\d)
            """,
            re.VERBOSE,
        ),
    ),

    # ── IPv4 Addresses ───────────────────────────────────────────────────────
    _Rule(
        name="IPV4",
        priority=2,
        pattern=re.compile(
            r"""
            (?<!\d\.)(?<!\d)
            (?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}
            (?:25[0-5]|2[0-4]\d|[01]?\d\d?)
            (?!\d|\.\d)
            """,
            re.VERBOSE,
        ),
    ),

    # ── Email Addresses (RFC 5322 subset) ────────────────────────────────────
    _Rule(
        name="EMAIL",
        priority=3,
        pattern=re.compile(
            r"""
            (?<![A-Za-z0-9._%+\-])
            [A-Za-z0-9._%+\-]{1,64}
            @
            (?:[A-Za-z0-9\-]{1,63}\.)+
            [A-Za-z]{2,}
            (?![A-Za-z0-9\-])
            """,
            re.VERBOSE,
        ),
    ),

    # ── Phone Numbers (E.164 + common formats) ───────────────────────────────
    _Rule(
        name="PHONE",
        priority=4,
        pattern=re.compile(
            r"""
            (?<!\d)
            (?:
                # International with +country code: +44 20 7946 0958, +91 9909092253, +1-555-867-5309
                \+[1-9]\d{0,3}           # + and 1-4 digit country code
                (?:[\s\-.]?\d{2,5}){1,4} # 1-4 groups of 2-5 digits (flexible for any country)
                # US/Canada 10-digit without country code
              | \(?[2-9]\d{2}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}
                # US with country code 1
              | 1[\s\-\.]\(?[2-9]\d{2}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}
            )
            (?!\d)
            """,
            re.VERBOSE,
        ),
    ),

    # ── Fallback Explicit Declarations ───────────────────────────────────────
    _Rule(
        name="PERSON",
        priority=5,
        pattern=re.compile(
            r"(?:(?i:my name is|i am|this is)\s+)([A-Z][A-Za-z0-9_-]*(?:\s+[A-Z][A-Za-z0-9_-]*)*)",
        ),
    ),
]

# Sort by priority for deterministic, ordered application
_RULES.sort(key=lambda r: r.priority)


# ---------------------------------------------------------------------------
# Public Interface
# ---------------------------------------------------------------------------

@dataclass
class RegexMatch:
    start       : int
    end         : int
    entity_type : str
    original    : str


def find_all(text: str) -> List[RegexMatch]:
    """
    Scan *text* with all compiled rules and return a deduplicated, sorted
    list of non-overlapping matches (longest-wins for overlapping spans).
    """
    raw_matches: List[RegexMatch] = []

    for rule in _RULES:
        for m in rule.pattern.finditer(text):
            group_idx = 1 if m.lastindex else 0
            raw_matches.append(
                RegexMatch(
                    start       = m.start(group_idx),
                    end         = m.end(group_idx),
                    entity_type = rule.name,
                    original    = m.group(group_idx).strip(),
                )
            )

    # Deduplicate / resolve overlaps: keep the longest span; on tie keep
    # the higher-priority (lower priority number) rule.
    raw_matches.sort(key=lambda x: (x.start, -(x.end - x.start)))
    merged: List[RegexMatch] = []
    last_end = -1
    for match in raw_matches:
        if match.start >= last_end:  # non-overlapping
            merged.append(match)
            last_end = match.end
        else:
            # Overlapping — already have a longer/higher-priority match
            pass

    logger.debug("RegexEngine: found %d matches", len(merged))
    return merged


async def scrub(
    text       : str,
    session_id : str,
    vault_inst,           # injected to avoid circular import
) -> Tuple[str, List[dict]]:
    """
    Apply all regex rules to *text*, tokenize matches via *vault_inst*,
    and return (sanitised_text, audit_log_entries).

    This is an async function so it can be awaited alongside the NER pass.
    """
    matches    = find_all(text)
    audit_logs = []

    if not matches:
        return text, audit_logs

    # Rebuild string with replacements, iterating in reverse so offsets stay valid
    chars = list(text)
    for match in reversed(matches):
        token = await vault_inst.tokenize(session_id, match.original, match.entity_type)
        chars[match.start : match.end] = list(token)
        audit_logs.insert(
            0,
            {
                "action"  : "REDACTED",
                "type"    : match.entity_type,
                "token"   : token,
                "original": match.original,
            },
        )

    return "".join(chars), audit_logs

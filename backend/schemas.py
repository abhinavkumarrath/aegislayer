"""
AegisLayer — Pydantic Schemas
Defines all request/response data models for the API surface.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, UUID4


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class EntityType(str, Enum):
    PERSON       = "PERSON"
    ORG          = "ORG"
    LOCATION     = "LOCATION"
    EMAIL        = "EMAIL"
    API_KEY      = "API_KEY"
    IPV4         = "IPV4"
    PHONE        = "PHONE"
    CREDIT_CARD  = "CREDIT_CARD"
    UNKNOWN      = "UNKNOWN"


class AuditAction(str, Enum):
    REDACTED   = "REDACTED"
    RESTORED   = "RESTORED"


# ---------------------------------------------------------------------------
# Shared Sub-models
# ---------------------------------------------------------------------------

class AuditEntry(BaseModel):
    """Single mutation record in the compliance ledger."""
    action : AuditAction = Field(..., description="REDACTED or RESTORED")
    type   : EntityType  = Field(..., description="Category of entity detected")
    token  : str         = Field(..., description="Placeholder token used, e.g. [PERSON_1]")
    original: Optional[str] = Field(
        None,
        description="Original plaintext value (omit in logs sent to untrusted parties)"
    )


# ---------------------------------------------------------------------------
# /api/process
# ---------------------------------------------------------------------------

class ProcessRequest(BaseModel):
    session_id : str  = Field(..., description="Client-owned UUIDv4 session identifier")
    prompt     : str  = Field(..., min_length=1, description="Raw, un-redacted enterprise prompt")
    llm_model  : Optional[str] = Field(
        None,
        description="Override LLM model slug (defaults to env LLM_MODEL)"
    )


class ProcessResponse(BaseModel):
    session_id                 : str
    original_prompt            : str
    sanitized_prompt           : str
    raw_llm_response           : str
    final_de_sanitized_response: str
    audit_logs                 : List[AuditEntry]
    latency_ms                 : Optional[float] = Field(
        None, description="End-to-end pipeline latency in milliseconds"
    )
    ner_device                 : Optional[str] = Field(
        None, description="Device used for NER inference (cuda/cpu)"
    )


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status      : str  = "ok"
    uptime_s    : float
    ner_device  : str
    ner_model   : str
    ner_ready   : bool
    llm_endpoint: Optional[str] = None
    version     : str  = "1.0.0"

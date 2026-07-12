"""
AegisLayer — FastAPI Application (main.py)
==========================================
Asynchronous middleware orchestrator implementing the full
Redact → Sanitise → LLM → Restore pipeline.

Startup sequence
----------------
1. Load .env configuration
2. Pre-warm NER model (background task)
3. Accept requests at /health and /api/process

Pipeline (POST /api/process)
-----------------------------
Raw Prompt
  ├─ [A] CPU Regex Engine  →  partial scrub + audit
  ├─ [B] AMD GPU NER       →  deep scrub + audit
  └─ Encrypted Session Vault (token ↔ real map)
         │
     Sanitised Prompt  ──►  External LLM
                                  │
     De-sanitised Response  ◄─────┘
         │
     Audit Ledger + Clear Vault
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Load environment variables before importing config-dependent modules
load_dotenv()

from ner_engine  import ner_engine, scrub_ner   # noqa: E402
from rule_engine import find_all, scrub as regex_scrub  # noqa: E402
from vault       import vault                    # noqa: E402
from llm_client  import llm_client              # noqa: E402
from schemas     import (                        # noqa: E402
    AuditEntry,
    AuditAction,
    EntityType,
    HealthResponse,
    ProcessRequest,
    ProcessResponse,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level  = os.getenv("LOG_LEVEL", "INFO"),
    format = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("aegislayer.main")

# ---------------------------------------------------------------------------
# Startup time reference
# ---------------------------------------------------------------------------
_START_TIME = time.time()


# ---------------------------------------------------------------------------
# Application lifespan (pre-warm model on startup)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-warm and pre-execute the NER model during startup."""
    logger.info("AegisLayer starting — warming model architectures …")
    
    # Target your existing initialization helper
    await ner_engine.ensure_loaded()
    
    # Run a cold dummy execution pass to force ROCm layer kernel compilation
    try:
        logger.info("AegisLayer executing ROCm warm-up routine …")
        await ner_engine.extract_entities("Warm up system execution context for AMD hardware acceleration.")
        logger.info("AegisLayer ROCm engine fully compiled and optimized.")
    except Exception as e:
        logger.warning("ROCm compilation warm-up warning: %s", e)

    yield
    # Graceful shutdown
    logger.info("AegisLayer shutting down — closing LLM client …")
    await llm_client.aclose()


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(
    title       = "AegisLayer — Enterprise Privacy Middleware",
    description = (
        "Zero-trust reversible tokenization middleware. "
        "Intercepts LLM prompts, redacts PII/secrets, and restores them in responses."
    ),
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = None,       # Replaced by custom /docs below
    redoc_url   = None,
)

# ── CORS (allow the local frontend) ────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],   # lock down in production
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Serve frontend static files ─────────────────────────────────────────────
_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")


# ---------------------------------------------------------------------------
# Middleware: request timing
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    t0       = time.perf_counter()
    response = await call_next(request)
    elapsed  = (time.perf_counter() - t0) * 1000
    response.headers["X-AegisLayer-Latency-Ms"] = f"{elapsed:.2f}"
    return response


# ---------------------------------------------------------------------------
# Helper: coerce raw audit dict → AuditEntry schema
# ---------------------------------------------------------------------------

def _coerce_audit(raw: dict) -> AuditEntry:
    entity_type_str = raw.get("type", "UNKNOWN").upper()
    try:
        etype = EntityType(entity_type_str)
    except ValueError:
        etype = EntityType.UNKNOWN

    return AuditEntry(
        action   = AuditAction(raw.get("action", "REDACTED")),
        type     = etype,
        token    = raw.get("token", ""),
        original = raw.get("original"),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    response_model = HealthResponse,
    summary        = "System health & GPU status check",
    tags           = ["System"],
)
async def health() -> HealthResponse:
    """
    Returns system health including NER device, model readiness, and uptime.
    Use this to confirm ROCm/CUDA acceleration is active.
    """
    return HealthResponse(
        status       = "ok",
        uptime_s     = round(time.time() - _START_TIME, 2),
        ner_device   = ner_engine.device_name,
        ner_model    = ner_engine.model_name,
        ner_ready    = ner_engine.is_ready,
        llm_endpoint = llm_client.endpoint if llm_client.configured else None,
        version      = "1.0.0",
    )


@app.get("/", include_in_schema=False)
async def root():
    """Serve the landing page index.html if present."""
    index_path = os.path.join(_FRONTEND_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "AegisLayer API running. See /docs for API reference."})


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    """Serve the dashboard.html if present."""
    index_path = os.path.join(_FRONTEND_DIR, "dashboard.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "Dashboard not found."})


@app.get("/documentation", include_in_schema=False)
async def documentation():
    """Serve the docs.html if present."""
    index_path = os.path.join(_FRONTEND_DIR, "docs.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "Docs not found."})


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    """Serve the custom-branded Swagger UI."""
    swagger_path = os.path.join(_FRONTEND_DIR, "swagger.html")
    if os.path.isfile(swagger_path):
        return FileResponse(swagger_path, media_type="text/html")
    # Fallback: generate inline if file missing
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url="/openapi.json", title="AegisLayer API")


@app.get("/redoc", include_in_schema=False)
async def redoc_redirect():
    """Redirect ReDoc to our custom docs."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docs")


@app.post(
    "/api/process",
    response_model = ProcessResponse,
    summary        = "Full privacy pipeline — redact, send to LLM, restore",
    tags           = ["Pipeline"],
)
async def process_prompt(req: ProcessRequest) -> ProcessResponse:
    """
    End-to-end AegisLayer pipeline:

    1. **Regex Engine (CPU)** — detect structured PII (emails, API keys, IPs, phones)
    2. **NER Engine (AMD GPU)** — detect unstructured entities (names, orgs, locations)
    3. **Session Vault** — tokenise all detected values
    4. **LLM Bridge** — forward sanitised prompt to external LLM
    5. **De-sanitise** — restore original values in LLM response
    6. **Audit ledger** — return complete mutation log
    7. **Vault clear** — wipe session state to minimise threat surface
    """
    pipeline_start = time.perf_counter()

    session_id     = req.session_id
    original_prompt = req.prompt

    if not session_id:
        raise HTTPException(
            status_code = status.HTTP_400_BAD_REQUEST,
            detail      = "session_id must be a non-empty string (prefer UUIDv4)",
        )

    logger.info("Pipeline start — session=%s, prompt_len=%d", session_id, len(original_prompt))

    # ── Step 1: CPU Regex Engine ────────────────────────────────────────────
    try:
        regex_scrubbed, regex_audit = await regex_scrub(
            original_prompt, session_id, vault
        )
    except Exception as exc:
        logger.exception("Regex engine error: %s", exc)
        regex_scrubbed, regex_audit = original_prompt, []

    # Track character ranges already replaced by regex so NER doesn't corrupt them.
    # We MUST scan the *scrubbed* prompt because offsets shifted during replacement.
    token_pattern = re.compile(r'\[(?:PERSON|ORG|LOC|EMAIL|APIKEY|IP|PHONE|CARD|ENTITY)_[A-Z0-9]+\]')
    regex_spans   = [{"start": m.start(), "end": m.end()} for m in token_pattern.finditer(regex_scrubbed)]

    # ── Step 2: AMD GPU NER Engine ──────────────────────────────────────────
    # Wrap in a timeout so a first-run model download (can be several minutes)
    # never blocks the whole pipeline. If NER isn't ready yet, skip it and rely
    # on regex results; the model will be cached locally for the next request.
    _NER_TIMEOUT_S = float(os.getenv("NER_REQUEST_TIMEOUT_S", "45"))
    try:
        ner_scrubbed, ner_audit = await asyncio.wait_for(
            scrub_ner(regex_scrubbed, session_id, vault, exclude_spans=regex_spans),
            timeout=_NER_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "NER engine timed out after %.0fs — skipping NER pass. "
            "Model may still be downloading; retry in a moment.",
            _NER_TIMEOUT_S,
        )
        ner_scrubbed, ner_audit = regex_scrubbed, []
    except Exception as exc:
        logger.exception("NER engine error: %s", exc)
        ner_scrubbed, ner_audit = regex_scrubbed, []

    sanitised_prompt = ner_scrubbed
    combined_audit   = regex_audit + ner_audit

    logger.info(
        "Sanitisation complete — %d entities redacted, sending to LLM",
        len(combined_audit),
    )

    # ── Step 3: Forward to External LLM ─────────────────────────────────────
    try:
        raw_llm_response = await llm_client.chat(
            sanitised_prompt = sanitised_prompt,
            model            = req.llm_model,
        )
    except Exception as exc:
        logger.exception("LLM client error: %s", exc)
        raw_llm_response = f"[AegisLayer Error] LLM call failed: {exc}"

    # ── Step 4: De-sanitise LLM Response ────────────────────────────────────
    try:
        final_response = await vault.detokenize(session_id, raw_llm_response)
    except Exception as exc:
        logger.exception("Detokenize error: %s", exc)
        final_response = raw_llm_response

    # ── Step 5: Cleanup Session Vault ───────────────────────────────────────
    await vault.clear_session(session_id)

    # ── Step 6: Build response ───────────────────────────────────────────────
    total_ms = (time.perf_counter() - pipeline_start) * 1000

    audit_entries = [_coerce_audit(a) for a in combined_audit]

    logger.info(
        "Pipeline complete — session=%s, latency=%.1f ms, redacted=%d",
        session_id, total_ms, len(audit_entries),
    )

    return ProcessResponse(
        session_id                  = session_id,
        original_prompt             = original_prompt,
        sanitized_prompt            = sanitised_prompt,
        raw_llm_response            = raw_llm_response,
        final_de_sanitized_response = final_response,
        audit_logs                  = audit_entries,
        latency_ms                  = round(total_ms, 2),
        ner_device                  = ner_engine.device_name,
    )


# ---------------------------------------------------------------------------
# Dev entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host     = "0.0.0.0",
        port     = int(os.getenv("PORT", "8000")),
        reload   = True,
        log_level= os.getenv("LOG_LEVEL", "info").lower(),
    )

"""
AegisLayer — Module B: AMD GPU / ROCm NER Engine
=================================================
Loads a Hugging Face NER transformer (dslim/bert-base-NER) and runs inference
on the AMD GPU Pod via PyTorch ROCm.  Falls back gracefully to CPU if ROCm
is unavailable (e.g., local dev environment).

ROCm Note
---------
AMD ROCm exposes itself through PyTorch as CUDA-compatible.  torch.cuda.is_available()
returns True on a ROCm build, and device="cuda" targets the AMD GPU.
For explicitness we also check for rocm in torch.version.hip.

Span grouping
-------------
HuggingFace NER pipeline returns B- / I- prefixed tokens.  We group them
into full entity spans so "John" + "Doe" → single PERSON entity.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("aegislayer.ner_engine")


# ---------------------------------------------------------------------------
# Span data class
# ---------------------------------------------------------------------------

@dataclass
class NERSpan:
    start       : int
    end         : int
    entity_type : str   # PERSON | ORG | LOCATION
    word        : str
    score       : float


# ---------------------------------------------------------------------------
# Label normalisation map
# ---------------------------------------------------------------------------

_LABEL_MAP = {
    # dslim/bert-base-NER labels
    "PER"  : "PERSON",
    "ORG"  : "ORG",
    "LOC"  : "LOCATION",
    "MISC" : "UNKNOWN",
    # spacy-style fallbacks
    "PERSON"   : "PERSON",
    "GPE"      : "LOCATION",
    "NORP"     : "ORG",
    "FAC"      : "LOCATION",
    "PRODUCT"  : "UNKNOWN",
}


def _normalise_label(raw: str) -> str:
    """Strip B- / I- prefix and map to canonical AegisLayer entity type."""
    stripped = raw.replace("B-", "").replace("I-", "").upper()
    return _LABEL_MAP.get(stripped, "UNKNOWN")


# ---------------------------------------------------------------------------
# NER Engine
# ---------------------------------------------------------------------------

class NEREngine:
    """
    Singleton wrapper around the HuggingFace NER pipeline.

    The model is lazy-loaded on first call to avoid blocking startup.
    Thread safety is guaranteed by an asyncio.Lock.
    """

    _MODEL_NAME = os.getenv("NER_MODEL", "dslim/bert-base-NER")
    _SCORE_THRESHOLD = float(os.getenv("NER_SCORE_THRESHOLD", "0.75"))

    def __init__(self) -> None:
        self._pipeline    = None
        self._device_name : str  = "uninitialised"
        self._ready       : bool = False
        self._load_lock   : asyncio.Lock = asyncio.Lock()
        self._load_error  : Optional[str] = None

    # ------------------------------------------------------------------
    # Device resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_device() -> tuple[int, str]:
        """
        Return (device_int, label) where device_int is passed to HF pipeline.
        -1 = CPU, 0 = first CUDA/ROCm device.
        """
        try:
            import torch  # noqa: PLC0415
            if torch.cuda.is_available():
                # ROCm exposes itself via CUDA compatibility layer
                hip = getattr(torch.version, "hip", None)
                label = f"rocm:{torch.cuda.get_device_name(0)}" if hip else f"cuda:{torch.cuda.get_device_name(0)}"
                return 0, label
        except ImportError:
            logger.warning("PyTorch not installed — NER will run in mock mode")
        return -1, "cpu"

    # ------------------------------------------------------------------
    # Lazy model loading
    # ------------------------------------------------------------------

    async def ensure_loaded(self) -> None:
        """Idempotent: load model if not yet loaded."""
        if self._ready:
            return

        async with self._load_lock:
            if self._ready:   # double-checked locking
                return
            await asyncio.get_event_loop().run_in_executor(None, self._blocking_load)

    def _blocking_load(self) -> None:
        """Synchronous model load — runs in a thread-pool executor."""
        t0 = time.perf_counter()
        try:
            from transformers import pipeline as hf_pipeline  # noqa: PLC0415

            device_int, label = self._resolve_device()
            self._device_name = label

            logger.info("NEREngine: loading model %s on %s …", self._MODEL_NAME, label)
            self._pipeline = hf_pipeline(
                task                  = "ner",
                model                 = self._MODEL_NAME,
                aggregation_strategy  = "first",   # 'first' gives cleaner span boundaries than 'simple'
                device                = device_int,
            )
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info("NEREngine: model ready in %.1f ms on %s", elapsed, label)
            self._ready = True

        except Exception as exc:  # noqa: BLE001
            self._load_error  = str(exc)
            self._device_name = "cpu (fallback)"
            logger.error("NEREngine: model load failed — %s", exc)
            logger.warning("NEREngine: falling back to mock/no-op mode")
            self._ready = True   # mark ready so we don't retry on every request

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    async def extract_entities(self, text: str) -> List[NERSpan]:
        """
        Run NER inference on *text* and return a list of NERSpan objects.
        Returns [] if model failed to load or no entities found.
        """
        await self.ensure_loaded()

        if self._pipeline is None:
            logger.debug("NEREngine: pipeline unavailable, returning empty spans")
            return []

        # Run blocking inference in thread executor to keep FastAPI event loop free
        raw_entities = await asyncio.get_event_loop().run_in_executor(
            None, self._run_pipeline, text
        )

        spans = []
        for ent in raw_entities:
            score = float(ent.get("score", 0))
            if score < self._SCORE_THRESHOLD:
                continue

            start = int(ent["start"])
            end   = int(ent["end"])

            # ----------------------------------------------------------------
            # KEY FIX: use character offsets to slice the original text.
            # ent["word"] from BERT contains WordPiece artifacts like '##b',
            # '##im', etc. which produce broken tokens ([PERSON_1] = '##b').
            # Slicing text[start:end] always gives the correct surface form.
            # ----------------------------------------------------------------
            actual_word = text[start:end].strip()

            # Skip empty or single-character fragments (BERT tokenizer noise)
            if not actual_word or len(actual_word) < 2:
                logger.debug("NEREngine: skipping short fragment %r at %d-%d", actual_word, start, end)
                continue

            entity_type = _normalise_label(
                ent.get("entity_group", ent.get("entity", "UNKNOWN"))
            )

            spans.append(
                NERSpan(
                    start       = start,
                    end         = end,
                    entity_type = entity_type,
                    word        = actual_word,
                    score       = score,
                )
            )

        # Merge adjacent spans of the same type.
        # BERT sometimes outputs consecutive B-PER spans for a single multi-word
        # name (e.g. 'Abhimanyu' split across tokens). We reunify them here.
        spans = _merge_adjacent_spans(spans, text)

        # Drop UNKNOWN / low-value entity types
        spans = [s for s in spans if s.entity_type != "UNKNOWN"]

        logger.debug("NEREngine: %d clean spans from %d chars", len(spans), len(text))
        return spans

    def _run_pipeline(self, text: str) -> list:
        """Synchronous pipeline call — executed in thread pool."""
        try:
            results = self._pipeline(text)
            return results if results else []
        except Exception as exc:  # noqa: BLE001
            logger.error("NEREngine: inference error — %s", exc)
            return []

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def device_name(self) -> str:
        return self._device_name

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def model_name(self) -> str:
        return self._MODEL_NAME

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error


# ------------------------------------------------------------------
# Span post-processing
# ------------------------------------------------------------------

def _merge_adjacent_spans(spans: List[NERSpan], text: str) -> List[NERSpan]:
    """
    Merge consecutive spans of the same entity type when they are adjacent
    or only separated by whitespace in the original text.

    Example:  [NERSpan('A', PER, 17-18), NERSpan('bhimanyu', PER, 18-26)]
              → [NERSpan('Abhimanyu', PER, 17-26)]

    This fixes BERT WordPiece subword fragmentation where a single name
    is split into multiple consecutive B-PER predictions.
    """
    if len(spans) <= 1:
        return spans

    spans = sorted(spans, key=lambda s: s.start)
    merged: List[NERSpan] = []
    current = spans[0]

    for nxt in spans[1:]:
        gap = nxt.start - current.end  # chars between end of current and start of next
        same_type = nxt.entity_type == current.entity_type

        # Merge if: same entity type AND gap is 0 (adjacent) or 1 (single space)
        if same_type and gap <= 1:
            new_end  = max(current.end, nxt.end)
            new_word = text[current.start : new_end].strip()
            current  = NERSpan(
                start       = current.start,
                end         = new_end,
                entity_type = current.entity_type,
                word        = new_word,
                score       = min(current.score, nxt.score),  # conservative: take lower
            )
            logger.debug(
                "NEREngine: merged '%s'+'%s' → '%s'",
                current.word, nxt.word, new_word,
            )
        else:
            merged.append(current)
            current = nxt

    merged.append(current)
    return merged


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------
ner_engine = NEREngine()


# ------------------------------------------------------------------
# Convenience scrub helper (used by main.py)
# ------------------------------------------------------------------

async def scrub_ner(
    text       : str,
    session_id : str,
    vault_inst,           # injected to avoid circular import
    exclude_spans: Optional[List] = None,  # spans already handled by regex
) -> tuple[str, list]:
    """
    Run NER on *text*, tokenize detected entities via *vault_inst*,
    and return (sanitised_text, audit_entries).

    Spans that overlap with already-tokenized regions (from regex pass)
    are skipped to prevent double-tokenization.
    """
    spans = await ner_engine.extract_entities(text)

    exclude_spans = exclude_spans or []
    audit_logs    = []

    if not spans:
        return text, audit_logs

    # Filter spans that overlap with already-replaced regions
    def _overlaps(span: NERSpan) -> bool:
        for ex in exclude_spans:
            if span.start < ex["end"] and span.end > ex["start"]:
                return True
        return False

    valid_spans = [s for s in spans if not _overlaps(s)]

    # Rebuild text in reverse order so offsets remain stable
    chars = list(text)
    for span in sorted(valid_spans, key=lambda s: s.start, reverse=True):
        token = await vault_inst.tokenize(session_id, span.word, span.entity_type)
        chars[span.start : span.end] = list(token)
        audit_logs.insert(
            0,
            {
                "action"  : "REDACTED",
                "type"    : span.entity_type,
                "token"   : token,
                "original": span.word,
                "score"   : span.score,
            },
        )

    return "".join(chars), audit_logs

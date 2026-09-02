"""Dual-run ingest writer for native DynamoDB embedding items (SPEC §3.4).

Reuses the vectors the ingest embedding step already computed (zero extra
OpenAI calls) and writes them as ``*_EMBEDDING`` items via the
``receipt_embeddings`` engine writer. Gated on the ``DUAL_WRITE_EMBEDDINGS``
env flag (string ``"true"`` enables; default off) and strictly non-fatal:
any failure is logged and reported, never raised, so the receipt's ingest
outcome is unaffected.

Metadata written here is the best available at ingest time (place may not
be resolved yet, sections do not exist yet); the stream freshening leg
(SPEC §3.4a) refreshes ``merchant_name``/``place_id``/``label_status``/
``section_type`` when those entities land.
"""

import logging
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

from receipt_chroma.embedding.metadata.line_metadata import (
    enrich_row_metadata_with_anchors,
)
from receipt_embeddings.write_requests import build_embedding_write_requests
from receipt_embeddings.writer import write_report_incomplete

logger = logging.getLogger(__name__)

DUAL_WRITE_ENV_VAR = "DUAL_WRITE_EMBEDDINGS"


def dual_write_embeddings_enabled() -> bool:
    """True only when DUAL_WRITE_EMBEDDINGS is the string "true"."""
    return os.environ.get(DUAL_WRITE_ENV_VAR, "").strip().lower() == "true"


def build_ingest_embedding_requests(
    *,
    image_id: str,
    receipt_id: int,
    lines: Sequence[Any],
    words: Sequence[Any],
    word_labels: Sequence[Any],
    merchant_name: str,
    place_id: str,
    row_embeddings: Sequence[Sequence[float]],
    row_line_ids_list: Sequence[Sequence[int]],
    word_embeddings_list: Sequence[Sequence[float]],
) -> List[Any]:
    """Build engine write requests carrying the ingest's in-memory vectors.

    ``row_embeddings``/``row_line_ids_list`` come from the same visual-row
    grouping that produced the vectors, so rows are never re-derived here
    (no risk of misaligning a vector with a different grouping).
    """
    return build_embedding_write_requests(
        image_id=image_id,
        receipt_id=receipt_id,
        lines=lines,
        words=words,
        word_labels=word_labels,
        merchant_name=merchant_name,
        place_id=place_id,
        row_line_ids_list=row_line_ids_list,
        row_embeddings=row_embeddings,
        word_embeddings=word_embeddings_list,
        include_embedding_input=False,
        missing_row="raise",
        enrich_anchors=enrich_row_metadata_with_anchors,
    )


def maybe_dual_write_embeddings(
    *,
    dynamo: Any,
    image_id: str,
    receipt_id: int,
    lines: Sequence[Any],
    words: Sequence[Any],
    word_labels: Sequence[Any],
    receipt_place: Any,
    row_embeddings: Sequence[Sequence[float]],
    row_line_ids_list: Sequence[Sequence[int]],
    word_embeddings_list: Sequence[Sequence[float]],
    writer_factory: Optional[Callable[[Any, str], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Flag-gated, never-raising dual write of the receipt's embeddings.

    Returns None when the flag is off (zero work, zero writer calls);
    otherwise a small report dict for logging/metrics. Every vector is
    supplied up front, so the engine writer never calls OpenAI.
    """
    if not dual_write_embeddings_enabled():
        return None

    try:
        merchant_name = (
            str(getattr(receipt_place, "merchant_name", "") or "")
            if receipt_place is not None
            else ""
        )
        place_id = (
            str(getattr(receipt_place, "place_id", "") or "")
            if receipt_place is not None
            else ""
        )
        requests = build_ingest_embedding_requests(
            image_id=image_id,
            receipt_id=receipt_id,
            lines=lines,
            words=words,
            word_labels=word_labels,
            merchant_name=merchant_name,
            place_id=place_id,
            row_embeddings=row_embeddings,
            row_line_ids_list=row_line_ids_list,
            word_embeddings_list=word_embeddings_list,
        )
        if writer_factory is None:
            from receipt_embeddings import EmbeddingWriter

            writer = EmbeddingWriter(dynamo._client, dynamo.table_name)
        else:
            writer = writer_factory(dynamo._client, dynamo.table_name)
        report = writer.write(requests)
        result = {
            "enabled": True,
            "requests": len(requests),
            "written": report.written,
            "skipped_existing": len(report.skipped_existing_keys),
            "failed": len(report.failures),
        }
        logger.info(
            "Dual-write embeddings for %s#%s: %s",
            image_id,
            receipt_id,
            result,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - never affect ingest outcome
        logger.exception(
            "Dual-write embeddings failed for %s#%s (non-fatal)",
            image_id,
            receipt_id,
        )
        return {
            "enabled": True,
            "written": 0,
            "failed": 0,
            "error": str(exc),
        }


__all__ = [
    "DUAL_WRITE_ENV_VAR",
    "build_ingest_embedding_requests",
    "dual_write_embeddings_enabled",
    "maybe_dual_write_embeddings",
    "write_report_incomplete",
]

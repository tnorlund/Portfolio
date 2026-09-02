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
from receipt_dynamo.constants import ValidationStatus

logger = logging.getLogger(__name__)

DUAL_WRITE_ENV_VAR = "DUAL_WRITE_EMBEDDINGS"


def dual_write_embeddings_enabled() -> bool:
    """True only when DUAL_WRITE_EMBEDDINGS is the string "true"."""
    return os.environ.get(DUAL_WRITE_ENV_VAR, "").strip().lower() == "true"


def _word_label_statuses(
    word_labels: Sequence[Any],
) -> Dict[tuple, str]:
    """Aggregate labels per word with the terminal-verdict rule.

    Any terminal human verdict (VALID or INVALID) -> validated, else any
    PENDING -> pending, else none — same rule as the backfill and the
    stream freshener. INVALID-only words must stay in the validated
    population or the word index's label_status filter would drop
    exactly the counterexamples similar_labeled_words needs for
    evidence_against (E3 review P1-2; codex flip P2)."""
    by_word: Dict[tuple, List[str]] = {}
    for label in word_labels:
        key = (int(label.line_id), int(label.word_id))
        by_word.setdefault(key, []).append(str(label.validation_status))
    statuses: Dict[tuple, str] = {}
    for key, values in by_word.items():
        if (
            ValidationStatus.VALID.value in values
            or ValidationStatus.INVALID.value in values
        ):
            statuses[key] = "validated"
        elif ValidationStatus.PENDING.value in values:
            statuses[key] = "pending"
        else:
            statuses[key] = "none"
    return statuses


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
    from receipt_embeddings import EmbeddingWriteRequest

    lines_by_id = {int(line.line_id): line for line in lines}
    requests: List[Any] = []

    for row_line_ids, vector in zip(
        row_line_ids_list, row_embeddings, strict=True
    ):
        line_ids = [int(value) for value in row_line_ids]
        row_lines = [
            lines_by_id[line_id]
            for line_id in line_ids
            if line_id in lines_by_id
        ]
        if not row_lines:
            raise ValueError(
                f"visual row {line_ids} has no matching receipt lines"
            )
        row_line_id_set = set(line_ids)
        anchors = enrich_row_metadata_with_anchors(
            {},
            [word for word in words if int(word.line_id) in row_line_id_set],
        )
        requests.append(
            EmbeddingWriteRequest(
                kind="line",
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_ids[0],
                text=" ".join(line.text for line in row_lines),
                merchant_name=merchant_name,
                place_id=place_id,
                row_line_ids=tuple(line_ids),
                # Sections don't exist yet at ingest; the stream freshening
                # leg writes section_type when RECEIPT_SECTION lands.
                section_type="",
                normalized_phone_10=str(
                    anchors.get("normalized_phone_10", "")
                ),
                normalized_full_address=str(
                    anchors.get("normalized_full_address", "")
                ),
                vector=list(vector),
            )
        )

    statuses = _word_label_statuses(word_labels)
    for word, vector in zip(words, word_embeddings_list, strict=True):
        requests.append(
            EmbeddingWriteRequest(
                kind="word",
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=int(word.line_id),
                word_id=int(word.word_id),
                text=word.text,
                merchant_name=merchant_name,
                label_status=statuses.get(
                    (int(word.line_id), int(word.word_id)), "none"
                ),
                vector=list(vector),
            )
        )
    return requests


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
]

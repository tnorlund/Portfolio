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
from dataclasses import replace as dataclasses_replace
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


def _sweep_native_embedding_items(
    raw: Any, table_name: str, image_id: str, receipt_id: int
) -> int:
    """Delete a receipt's existing ``#EMBEDDING`` items (with
    UnprocessedItems retries) so a rewrite can replace them — the
    engine writer skips existing keys. Raises if deletes remain
    unprocessed after retries (an undeleted key would silently keep
    its stale vector)."""
    import time as _time

    kwargs = {
        "TableName": table_name,
        "KeyConditionExpression": "PK = :p AND begins_with(SK, :s)",
        "ExpressionAttributeValues": {
            ":p": {"S": f"IMAGE#{image_id}"},
            ":s": {"S": f"RECEIPT#{receipt_id:05d}#"},
        },
        "ProjectionExpression": "PK, SK",
    }
    keys = []
    while True:
        response = raw.query(**kwargs)
        keys.extend(
            {"PK": item["PK"], "SK": item["SK"]}
            for item in response.get("Items", [])
            if item["SK"]["S"].endswith("#EMBEDDING")
        )
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    deleted = 0
    for start in range(0, len(keys), 25):
        pending = [
            {"DeleteRequest": {"Key": key}} for key in keys[start : start + 25]
        ]
        for attempt in range(8):
            resp = raw.batch_write_item(RequestItems={table_name: pending})
            pending = resp.get("UnprocessedItems", {}).get(table_name, [])
            if not pending:
                break
            _time.sleep(0.2 * (2**attempt))
        if pending:
            raise RuntimeError(
                f"{len(pending)} embedding deletes unprocessed after "
                "retries"
            )
        deleted += min(25, len(keys) - start)
    return deleted


def write_native_embeddings(
    dynamo: Any,
    *,
    image_id: str,
    receipt_id: int,
    lines: Sequence[Any],
    words: Sequence[Any],
    word_labels: Sequence[Any],
    receipt_place: Any,
    sweep_existing: bool = False,
    openai_client: Any = None,
) -> Dict[str, Any]:
    """Chroma-free native embedding write for a whole receipt.

    The post-teardown replacement for the vectors that
    ``create_embeddings_and_compaction_run`` used to produce: builds
    the SAME ingest-formatted inputs (visual-row context for lines,
    spatial context for words), embeds them in ONE batched OpenAI call
    (not the writer's per-item realtime path), and persists via the
    engine writer. ``sweep_existing=True`` gives overwrite semantics
    for correction flows (re-OCR/resegment) whose text changed.

    Raises on hard failure (empty embed response, sweep exhaustion);
    returns the write report dict otherwise. Callers choose their own
    fatality: correction flows should abort BEFORE destructive cleanup
    on failure; ingest may catch-and-heal (the backfill is the healer).
    """
    # pylint: disable=import-outside-toplevel
    from receipt_chroma.embedding.metadata.line_metadata import (
        enrich_row_metadata_with_anchors as _anchors,
    )
    from receipt_embeddings import EmbeddingWriter, EmbeddingWriteRequest
    from receipt_embeddings.formatting import (
        format_word_context_embedding_input,
        get_row_embedding_inputs,
    )
    from receipt_embeddings.openai.realtime import embed_texts

    live_words = [
        word for word in words if not getattr(word, "is_noise", False)
    ] or list(words)
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
    lines_by_id = {int(line.line_id): line for line in lines}
    statuses = _word_label_statuses(word_labels)

    requests: List[Any] = []
    inputs: List[str] = []
    for embedding_input, line_ids in get_row_embedding_inputs(lines):
        row_lines = [
            lines_by_id[line_id]
            for line_id in line_ids
            if line_id in lines_by_id
        ]
        if not row_lines:
            continue
        text = " ".join(line.text for line in row_lines)
        if not text.strip():
            continue  # blank OCR rows are unembeddable (writer refuses)
        row_line_id_set = {int(v) for v in line_ids}
        anchors = _anchors(
            {},
            [
                word
                for word in live_words
                if int(word.line_id) in row_line_id_set
            ],
        )
        requests.append(
            EmbeddingWriteRequest(
                kind="line",
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=int(line_ids[0]),
                text=text,
                embedding_input=embedding_input,
                merchant_name=merchant_name,
                place_id=place_id,
                row_line_ids=tuple(int(v) for v in line_ids),
                section_type="",
                normalized_phone_10=str(
                    anchors.get("normalized_phone_10", "")
                ),
                normalized_full_address=str(
                    anchors.get("normalized_full_address", "")
                ),
            )
        )
        inputs.append(embedding_input)
    for word in live_words:
        if not str(word.text).strip():
            continue
        requests.append(
            EmbeddingWriteRequest(
                kind="word",
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=int(word.line_id),
                word_id=int(word.word_id),
                text=word.text,
                embedding_input=format_word_context_embedding_input(
                    word, live_words
                ),
                merchant_name=merchant_name,
                label_status=statuses.get(
                    (int(word.line_id), int(word.word_id)), "none"
                ),
            )
        )
        inputs.append(requests[-1].embedding_input)

    vectors = embed_texts(openai_client, inputs) if inputs else []
    if len(vectors) != len(requests):
        raise RuntimeError(
            f"embedding batch returned {len(vectors)} vectors for "
            f"{len(requests)} requests"
        )
    requests = [
        dataclasses_replace(request, vector=vector)
        for request, vector in zip(requests, vectors)
    ]

    swept = 0
    if sweep_existing:
        swept = _sweep_native_embedding_items(
            dynamo._client,  # pylint: disable=protected-access
            dynamo.table_name,
            image_id,
            receipt_id,
        )

    writer = EmbeddingWriter(
        dynamo._client,  # pylint: disable=protected-access
        dynamo.table_name,
    )
    report = writer.write(requests)
    result = {
        "requests": len(requests),
        "written": report.written,
        "skipped_existing": len(report.skipped_existing_keys),
        "failed": len(report.failures),
        "swept": swept,
    }
    logger.info(
        "Native embeddings for %s#%s: %s", image_id, receipt_id, result
    )
    return result


__all__ = [
    "DUAL_WRITE_ENV_VAR",
    "build_ingest_embedding_requests",
    "dual_write_embeddings_enabled",
    "maybe_dual_write_embeddings",
    "write_native_embeddings",
]

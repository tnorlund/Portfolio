"""Native DynamoDB embedding writers (post-Chroma-teardown).

``write_precomputed_embeddings`` persists vectors the ingest embedding
step already computed (zero extra OpenAI calls) as ``*_EMBEDDING`` items
via the ``receipt_embeddings`` engine writer — THE ingest persistence step.
``write_native_embeddings`` is the standalone variant for correction flows
(merge / re-OCR / resegment): it re-embeds from the receipt's current text
in one batched call and optionally sweeps stale items first.

Request construction, the word ``label_status`` rule, and the stale-item
sweep are the canonical ``receipt_embeddings`` implementations
(``write_requests`` / ``label_status`` / ``sweep`` — polish-brief dedup);
this module supplies the ingest/correction-flow policy around them.

Metadata written here is the best available at write time (place may not
be resolved yet, sections do not exist yet); the stream freshening leg
refreshes ``merchant_name``/``place_id``/``label_status``/``section_type``
when those entities land.
"""

import logging
from dataclasses import replace as dataclasses_replace
from typing import Callable, Dict, List, Optional, Sequence

from receipt_embeddings.formatting import LineLike
from receipt_embeddings.formatting.word_format import (
    WordLike as ContextWordLike,
)
from receipt_embeddings.label_status import WordLabelLike, word_label_statuses
from receipt_embeddings.protocols import (
    DynamoEmbeddingClient,
    DynamoQueryWriteClient,
    EmbeddingTableHandle,
    EmbeddingWriterLike,
)
from receipt_embeddings.sweep import delete_native_embedding_items
from receipt_embeddings.write_requests import build_embedding_write_requests
from receipt_embeddings.writer import EmbeddingWriteRequest

logger = logging.getLogger(__name__)

# Canonical terminal-verdict rule (any VALID or INVALID -> "validated",
# else PENDING -> "pending", else "none"); kept under the historical
# private name for existing importers/tests. INVALID-only words must stay
# in the validated population (E3 review P1-2; codex flip P2; #1513).
_word_label_statuses = word_label_statuses


def build_ingest_embedding_requests(
    *,
    image_id: str,
    receipt_id: int,
    lines: Sequence[LineLike],
    words: Sequence[ContextWordLike],
    word_labels: Sequence[WordLabelLike],
    merchant_name: str,
    place_id: str,
    row_embeddings: Sequence[Sequence[float]],
    row_line_ids_list: Sequence[Sequence[int]],
    word_embeddings_list: Sequence[Sequence[float]],
) -> List[EmbeddingWriteRequest]:
    """Build engine write requests carrying the ingest's in-memory vectors.

    ``row_embeddings``/``row_line_ids_list`` come from the same visual-row
    grouping that produced the vectors, so rows are never re-derived here
    (no risk of misaligning a vector with a different grouping). Thin
    policy wrapper over the canonical builder: ingest supplies vectors,
    raises on an impossible row, and stamps no sections (the stream
    freshener writes ``section_type`` when RECEIPT_SECTION lands).
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
    )


def write_precomputed_embeddings(
    *,
    dynamo: EmbeddingTableHandle,
    image_id: str,
    receipt_id: int,
    lines: Sequence[LineLike],
    words: Sequence[ContextWordLike],
    word_labels: Sequence[WordLabelLike],
    receipt_place: object,
    row_embeddings: Sequence[Sequence[float]],
    row_line_ids_list: Sequence[Sequence[int]],
    word_embeddings_list: Sequence[Sequence[float]],
    writer_factory: Optional[
        Callable[[DynamoEmbeddingClient, str], EmbeddingWriterLike]
    ] = None,
) -> Dict[str, object]:
    """Never-raising native write of a receipt's precomputed embeddings.

    THE ingest persistence step post-Chroma-teardown: every vector is
    supplied up front (reusing what the ingest embedding step already
    computed — zero extra OpenAI calls), so the engine writer never calls
    OpenAI. Returns a small report dict; callers decide fatality from its
    ``error``/``failed`` keys (ingest fails the receipt so it can retry).
    """
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
            # Lazy: keeps the module importable without the writer chain.
            # pylint: disable-next=import-outside-toplevel
            from receipt_embeddings import EmbeddingWriter

            writer = EmbeddingWriter(
                dynamo._client,  # pylint: disable=protected-access
                dynamo.table_name,
            )
        else:
            writer = writer_factory(
                dynamo._client,  # pylint: disable=protected-access
                dynamo.table_name,
            )
        report = writer.write(requests)
        result = {
            "requests": len(requests),
            "written": report.written,
            "skipped_existing": len(report.skipped_existing_keys),
            "failed": len(report.failures),
        }
        logger.info(
            "Native embeddings write for %s#%s: %s",
            image_id,
            receipt_id,
            result,
        )
        return result
    # pylint: disable-next=broad-exception-caught
    except Exception as exc:  # noqa: BLE001 - caller decides fatality
        # CONTRACTUAL never-raise: ingest marks the receipt failed from
        # the returned report; raising here would bypass that contract.
        logger.exception(
            "Native embeddings write failed for %s#%s",
            image_id,
            receipt_id,
        )
        return {
            "written": 0,
            "failed": 0,
            "error": str(exc),
        }


def _sweep_native_embedding_items(
    raw: DynamoQueryWriteClient,
    table_name: str,
    image_id: str,
    receipt_id: int,
) -> int:
    """Delete a receipt's existing ``#EMBEDDING`` items (with
    UnprocessedItems retries) so a rewrite can replace them — the
    engine writer skips existing keys. Raises if deletes remain
    unprocessed after retries (an undeleted key would silently keep
    its stale vector). Delegates to the canonical retrying sweeper."""
    return delete_native_embedding_items(raw, table_name, image_id, receipt_id)


def write_native_embeddings(
    dynamo: EmbeddingTableHandle,
    *,
    image_id: str,
    receipt_id: int,
    lines: Sequence[LineLike],
    words: Sequence[ContextWordLike],
    word_labels: Sequence[WordLabelLike],
    receipt_place: object,
    sweep_existing: bool = False,
    openai_client: object = None,
) -> Dict[str, object]:
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
    from receipt_embeddings import EmbeddingWriter
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

    requests = build_embedding_write_requests(
        image_id=image_id,
        receipt_id=receipt_id,
        lines=lines,
        words=live_words,
        word_labels=word_labels,
        merchant_name=merchant_name,
        place_id=place_id,
        include_embedding_input=True,
        missing_row="skip",
    )
    inputs = [request.embedding_input for request in requests]

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
    "build_ingest_embedding_requests",
    "write_native_embeddings",
    "write_precomputed_embeddings",
]

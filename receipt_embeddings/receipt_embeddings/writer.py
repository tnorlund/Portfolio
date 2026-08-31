"""Embed-and-put writer: OpenAI realtime (optional) then BatchWriteItem.

Writes are limited to ``#EMBEDDING`` sort keys. Re-running skips keys that
already exist. Per-item failures are recorded and skipped so one bad vector
never aborts the batch.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from receipt_embeddings.formatting import (
    format_word_context_embedding_input,
    get_primary_line_id,
    get_row_embedding_inputs,
    group_lines_into_visual_rows,
)
from receipt_embeddings.openai.realtime import embed_texts

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data._receipt_line_embedding import EmbeddingWriteReport
from receipt_dynamo.entities.embedding_codec import (
    LABEL_STATUS_NONE,
    LABEL_STATUS_PENDING,
    LABEL_STATUS_VALIDATED,
    vector_search_line_key,
    vector_search_word_key,
)
from receipt_dynamo.entities.receipt_line_embedding import ReceiptLineEmbedding
from receipt_dynamo.entities.receipt_word_embedding import ReceiptWordEmbedding

EmbeddingItem = ReceiptLineEmbedding | ReceiptWordEmbedding


@dataclass
class PreparedEmbeddings:
    """Entities ready to write, plus per-item skips from preparation."""

    items: list[EmbeddingItem] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def label_status_for_word(
    labels: Sequence[Any],
) -> str:
    """Flatten word-label rows onto the words-index inline filter."""

    statuses: set[str] = set()
    for label in labels:
        raw = getattr(label, "validation_status", None)
        statuses.add(getattr(raw, "value", raw) or "")
    if (
        ValidationStatus.VALID.value in statuses
        or ValidationStatus.INVALID.value in statuses
    ):
        return LABEL_STATUS_VALIDATED
    if (
        ValidationStatus.PENDING.value in statuses
        or ValidationStatus.NEEDS_REVIEW.value in statuses
    ):
        return LABEL_STATUS_PENDING
    return LABEL_STATUS_NONE


def section_type_for_row(
    line_ids: Sequence[int], section_by_line: Mapping[int, str]
) -> str | None:
    votes = Counter(
        section_by_line[line_id]
        for line_id in line_ids
        if line_id in section_by_line
    )
    if not votes:
        return None
    return votes.most_common(1)[0][0]


def valid_section_by_line(sections: Sequence[Any]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for section in sections:
        status = getattr(
            section.validation_status, "value", section.validation_status
        )
        if str(status) != ValidationStatus.VALID.value:
            continue
        section_type = getattr(
            section.section_type, "value", section.section_type
        )
        for line_id in section.line_ids:
            mapping[int(line_id)] = str(section_type)
    return mapping


def prepare_embedding_items(
    details: Any,
    *,
    sections: Sequence[Any] = (),
    vectors_by_key: Mapping[str, Sequence[float]],
) -> PreparedEmbeddings:
    """Build embedding entities for one receipt from stored vectors.

    Items whose vectors are missing are skipped, not raised.
    """

    prepared = PreparedEmbeddings()
    place = getattr(details, "place", None)
    merchant_name = getattr(place, "merchant_name", None) if place else None
    place_id = getattr(place, "place_id", None) if place else None
    section_by_line = valid_section_by_line(sections)
    labels_by_word: dict[tuple[int, int], list[Any]] = defaultdict(list)
    for label in getattr(details, "labels", ()) or ():
        labels_by_word[(int(label.line_id), int(label.word_id))].append(label)

    rows = group_lines_into_visual_rows(details.lines)
    inputs = get_row_embedding_inputs(details.lines)
    for (text, line_ids), row in zip(inputs, rows, strict=True):
        primary = get_primary_line_id(row)
        key = vector_search_line_key(
            details.receipt.image_id, details.receipt.receipt_id, primary
        )
        vector = vectors_by_key.get(key)
        if vector is None:
            prepared.skipped.append({"key": key, "reason": "missing_vector"})
            continue
        try:
            prepared.items.append(
                ReceiptLineEmbedding(
                    image_id=details.receipt.image_id,
                    receipt_id=details.receipt.receipt_id,
                    line_id=primary,
                    line_vector=list(vector),
                    text=text,
                    row_line_ids=list(line_ids),
                    merchant_name=merchant_name,
                    place_id=place_id,
                    section_type=section_type_for_row(
                        line_ids, section_by_line
                    ),
                )
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            prepared.skipped.append({"key": key, "reason": str(exc)})

    words = list(details.words)
    for word in words:
        key = vector_search_word_key(
            word.image_id, word.receipt_id, word.line_id, word.word_id
        )
        vector = vectors_by_key.get(key)
        if vector is None:
            prepared.skipped.append({"key": key, "reason": "missing_vector"})
            continue
        try:
            prepared.items.append(
                ReceiptWordEmbedding(
                    image_id=word.image_id,
                    receipt_id=word.receipt_id,
                    line_id=word.line_id,
                    word_id=word.word_id,
                    word_vector=list(vector),
                    text=format_word_context_embedding_input(word, words),
                    label_status=label_status_for_word(
                        labels_by_word[(word.line_id, word.word_id)]
                    ),
                    merchant_name=merchant_name,
                )
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            prepared.skipped.append({"key": key, "reason": str(exc)})
    return prepared


def texts_needing_openai(
    details: Any, vectors_by_key: Mapping[str, Sequence[float]]
) -> list[tuple[str, str]]:
    """Return ``(harness_key, embedding_input)`` pairs missing a vector."""

    needed: list[tuple[str, str]] = []
    rows = group_lines_into_visual_rows(details.lines)
    inputs = get_row_embedding_inputs(details.lines)
    for (text, _line_ids), row in zip(inputs, rows, strict=True):
        key = vector_search_line_key(
            details.receipt.image_id,
            details.receipt.receipt_id,
            get_primary_line_id(row),
        )
        if key not in vectors_by_key:
            needed.append((key, text))
    words = list(details.words)
    for word in words:
        key = vector_search_word_key(
            word.image_id, word.receipt_id, word.line_id, word.word_id
        )
        if key not in vectors_by_key:
            needed.append(
                (key, format_word_context_embedding_input(word, words))
            )
    return needed


def embed_missing_texts(
    pairs: Sequence[tuple[str, str]],
    *,
    openai_client: Any,
) -> dict[str, list[float]]:
    """Embed leftover texts via OpenAI realtime. Empty input is a no-op."""

    if not pairs:
        return {}
    texts = [text for _key, text in pairs]
    vectors = embed_texts(openai_client, texts)
    return {key: vector for (key, _text), vector in zip(pairs, vectors)}


def write_embedding_items(
    dynamo_client: Any, items: Sequence[EmbeddingItem]
) -> EmbeddingWriteReport:
    """Idempotent BatchWriteItem via the receipt_dynamo accessor."""

    return dynamo_client.put_embedding_items_idempotent(items)


__all__ = [
    "EmbeddingItem",
    "PreparedEmbeddings",
    "embed_missing_texts",
    "label_status_for_word",
    "prepare_embedding_items",
    "section_type_for_row",
    "texts_needing_openai",
    "valid_section_by_line",
    "write_embedding_items",
]

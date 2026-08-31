"""OpenAI realtime embed → BatchWriteItem for embedding items.

Idempotent: existing keys are skipped. Per-item failures skip-and-report
and never abort the rest of the batch. Never writes non-embedding items.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from receipt_dynamo.entities.receipt_line_embedding import (
    ReceiptLineEmbedding,
)
from receipt_dynamo.entities.receipt_word_embedding import (
    ReceiptWordEmbedding,
)

from receipt_embeddings.indexes import EMBEDDING_DIMENSION
from receipt_embeddings.openai.realtime import embed_texts

EmbeddingItem = ReceiptLineEmbedding | ReceiptWordEmbedding


@dataclass
class WriteReport:
    written: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)

    def merge(self, other: MappingReport) -> None:
        self.written += other.written
        self.skipped += other.skipped
        self.failed.extend(other.failed)


MappingReport = WriteReport


def embed_texts_checked(
    texts: Sequence[str],
    *,
    client: Any | None = None,
) -> list[list[float]]:
    """Embed texts and reject the wrong dimension."""
    vectors = embed_texts(client, list(texts))
    for vector in vectors:
        if len(vector) != EMBEDDING_DIMENSION:
            raise ValueError(
                f"embedding dimension {len(vector)} != {EMBEDDING_DIMENSION}"
            )
    return vectors


def put_embedding_items(
    dynamo: Any,
    items: Sequence[EmbeddingItem],
) -> WriteReport:
    """Idempotent BatchWrite of embedding items. Per-item skip on failure."""
    report = WriteReport()
    lines = [item for item in items if isinstance(item, ReceiptLineEmbedding)]
    words = [item for item in items if isinstance(item, ReceiptWordEmbedding)]
    for group, putter, label in (
        (
            lines,
            dynamo.put_receipt_line_embeddings_idempotent,
            "line",
        ),
        (
            words,
            dynamo.put_receipt_word_embeddings_idempotent,
            "word",
        ),
    ):
        if not group:
            continue
        try:
            result = putter(list(group))
            report.written += int(result.get("written", 0))
            report.skipped += int(result.get("skipped", 0))
        except Exception as exc:  # noqa: BLE001 — skip-and-report
            # Fall back to one-at-a-time so one bad item cannot abort the rest.
            for item in group:
                try:
                    one = putter([item])
                    report.written += int(one.get("written", 0))
                    report.skipped += int(one.get("skipped", 0))
                except Exception as item_exc:  # noqa: BLE001
                    report.failed.append(
                        f"{label}:{item.harness_key()}:{item_exc}"
                    )
            if not report.failed:
                report.failed.append(f"{label}-batch:{exc}")
    return report


__all__ = [
    "WriteReport",
    "embed_texts_checked",
    "put_embedding_items",
]

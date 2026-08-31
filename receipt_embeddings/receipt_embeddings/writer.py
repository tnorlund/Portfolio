"""Embed-and-put writer: OpenAI realtime -> DynamoDB embedding items.

This is the spec §3.4 write path — embed the receipt's visual rows and
words, ``BatchWriteItem`` the embedding items, done. No CompactionRun,
no S3, no SQS, no lock, no dual-write, no snapshot.

The writer is idempotent (existing embedding items are skipped without
re-embedding, so a re-run performs no writes and no OpenAI calls) and
degrades per item: an item that cannot be embedded or written is
skipped and reported, never aborting the rest of the batch.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
)
from receipt_dynamo.entities.receipt_line_embedding import (
    ReceiptLineEmbedding,
)
from receipt_dynamo.entities.receipt_word_embedding import (
    ReceiptWordEmbedding,
)

from receipt_embeddings.formatting.line_format import (
    format_row_embedding_input,
    format_visual_row,
    get_primary_line_id,
    group_lines_into_visual_rows,
)
from receipt_embeddings.formatting.word_format import (
    format_word_context_embedding_input,
)

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"

# Skip reasons reported by ReceiptEmbedReport.failures.
SKIP_MISSING_VECTOR = "missing_vector"
SKIP_INVALID_ITEM = "invalid_item"
SKIP_WRITE_FAILED = "write_failed"
SKIP_EMPTY_TEXT = "empty_text"


def line_embedding_key(image_id: str, receipt_id: int, line_id: int) -> str:
    """Protocol key for a visual-row embedding (fixture convention)."""

    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"


def word_embedding_key(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    """Protocol key for a word embedding (fixture convention)."""

    return (
        f"{line_embedding_key(image_id, receipt_id, line_id)}"
        f"#WORD#{word_id:05d}"
    )


@dataclass(frozen=True)
class EmbeddingRequest:
    """One vector still needed: a protocol key plus its input text."""

    key: str
    input_text: str


class VectorSource(Protocol):
    """Resolves embedding requests to vectors.

    A source returns vectors for the requests it can satisfy, keyed by
    request key; requests absent from the result are skip-reported by
    the writer. Raising aborts the receipt, so sources should prefer
    returning partial results for per-item problems.
    """

    def vectors_for(
        self, requests: Sequence[EmbeddingRequest]
    ) -> Mapping[str, list[float]]:
        """Return vectors for as many requests as possible."""


class OpenAIVectorSource:
    """Realtime OpenAI embedding source (the spec §3.4 default)."""

    def __init__(
        self,
        *,
        openai_client: Any | None = None,
        api_key: str | None = None,
        model: str = EMBEDDING_MODEL,
        batch_size: int = 128,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._client = openai_client
        self._api_key = api_key
        self._model = model
        self._batch_size = batch_size

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        from receipt_embeddings.openai.realtime import embed_texts

        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self._api_key)
        return embed_texts(self._client, texts, model=self._model)

    def vectors_for(
        self, requests: Sequence[EmbeddingRequest]
    ) -> Mapping[str, list[float]]:
        vectors: dict[str, list[float]] = {}
        for start in range(0, len(requests), self._batch_size):
            batch = requests[start : start + self._batch_size]
            embeddings = self._embed([request.input_text for request in batch])
            if len(embeddings) != len(batch):
                raise ValueError(
                    f"OpenAI returned {len(embeddings)} embeddings for "
                    f"{len(batch)} inputs"
                )
            for request, vector in zip(batch, embeddings):
                vectors[request.key] = [float(value) for value in vector]
        return vectors


@dataclass(frozen=True)
class SkippedItem:
    """One embedding item that was not written, with the reason why."""

    key: str
    reason: str
    detail: str = ""


@dataclass
class ReceiptEmbedReport:
    """What one ``embed_receipt`` call wrote, skipped, and failed."""

    image_id: str
    receipt_id: int
    written_line_keys: list[str] = field(default_factory=list)
    written_word_keys: list[str] = field(default_factory=list)
    existing_line_keys: list[str] = field(default_factory=list)
    existing_word_keys: list[str] = field(default_factory=list)
    failures: list[SkippedItem] = field(default_factory=list)

    @property
    def written_count(self) -> int:
        return len(self.written_line_keys) + len(self.written_word_keys)

    @property
    def skipped_existing_count(self) -> int:
        return len(self.existing_line_keys) + len(self.existing_word_keys)


def _label_status(labels: Sequence[Any]) -> tuple[str, str | None, list[str]]:
    """Derive (label_status, primary_label, valid_labels) for one word."""

    valid = [
        label
        for label in labels
        if label.validation_status == ValidationStatus.VALID.value
    ]
    if valid:
        # Latest validation wins; alphabetical breaks same-second ties.
        primary = sorted(
            valid,
            key=lambda label: (str(label.timestamp_added), label.label),
        )[-1].label
        valid_labels = sorted({label.label for label in valid})
        return "validated", primary, valid_labels
    if any(
        label.validation_status == ValidationStatus.PENDING.value
        for label in labels
    ):
        return "pending", None, []
    return "none", None, []


class EmbedAndPutWriter:
    """Write embedding items for one receipt at a time.

    Args:
        dynamo: A ``receipt_dynamo.DynamoClient`` (or compatible) bound
            to the target table.
        vector_source: Where missing vectors come from. Defaults to
            realtime OpenAI, constructed lazily so no OpenAI client or
            key is required when every item already exists.
    """

    def __init__(
        self,
        dynamo: Any,
        *,
        vector_source: VectorSource | None = None,
    ) -> None:
        self._dynamo = dynamo
        self._vector_source = vector_source

    def _source(self) -> VectorSource:
        if self._vector_source is None:
            self._vector_source = OpenAIVectorSource()
        return self._vector_source

    def embed_receipt(
        self, image_id: str, receipt_id: int
    ) -> ReceiptEmbedReport:
        """Embed and write every missing embedding item for one receipt.

        Raises:
            EntityNotFoundError / ValueError: When the receipt itself is
                absent or has no lines/words — per-receipt failures are
                the caller's (backfill loop's) skip unit.
        """

        report = ReceiptEmbedReport(image_id=image_id, receipt_id=receipt_id)
        details = self._dynamo.get_receipt_details(image_id, receipt_id)
        lines = details.lines
        words = details.words
        if not lines or not words:
            raise ValueError(
                f"receipt {image_id}#{receipt_id} has no lines or words"
            )

        merchant_name, place_id = self._receipt_place(image_id, receipt_id)
        section_by_line = self._valid_sections_by_line(image_id, receipt_id)
        labels_by_word: dict[tuple[int, int], list[Any]] = defaultdict(list)
        for label in details.labels:
            labels_by_word[(label.line_id, label.word_id)].append(label)

        existing_lines = {
            embedding.line_id
            for embedding in (
                self._dynamo.list_receipt_line_embeddings_from_receipt(
                    image_id, receipt_id
                )
            )
        }
        existing_words = {
            (embedding.line_id, embedding.word_id)
            for embedding in (
                self._dynamo.list_receipt_word_embeddings_from_receipt(
                    image_id, receipt_id
                )
            )
        }

        line_plans, word_plans = self._plan(
            report,
            lines=lines,
            words=words,
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=merchant_name,
            place_id=place_id,
            section_by_line=section_by_line,
            labels_by_word=labels_by_word,
            existing_lines=existing_lines,
            existing_words=existing_words,
        )

        requests = [plan["request"] for plan in line_plans + word_plans]
        vectors: Mapping[str, list[float]] = {}
        if requests:
            vectors = self._source().vectors_for(requests)

        line_entities = self._build_entities(
            report, line_plans, vectors, ReceiptLineEmbedding
        )
        word_entities = self._build_entities(
            report, word_plans, vectors, ReceiptWordEmbedding
        )

        self._write(
            report,
            line_entities,
            batch_write=self._dynamo.add_receipt_line_embeddings,
            single_write=self._dynamo.add_receipt_line_embedding,
            written_keys=report.written_line_keys,
        )
        self._write(
            report,
            word_entities,
            batch_write=self._dynamo.add_receipt_word_embeddings,
            single_write=self._dynamo.add_receipt_word_embedding,
            written_keys=report.written_word_keys,
        )
        return report

    # ------------------------------------------------------------------
    # planning

    def _plan(
        self,
        report: ReceiptEmbedReport,
        *,
        lines: Sequence[Any],
        words: Sequence[Any],
        image_id: str,
        receipt_id: int,
        merchant_name: str | None,
        place_id: str | None,
        section_by_line: Mapping[int, str],
        labels_by_word: Mapping[tuple[int, int], list[Any]],
        existing_lines: set[int],
        existing_words: set[tuple[int, int]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        line_plans: list[dict[str, Any]] = []
        rows = group_lines_into_visual_rows(lines)
        for position, row in enumerate(rows):
            primary_line_id = int(get_primary_line_id(row))
            key = line_embedding_key(image_id, receipt_id, primary_line_id)
            if primary_line_id in existing_lines:
                report.existing_line_keys.append(key)
                continue
            text = format_visual_row(row)
            if not text.strip():
                report.failures.append(
                    SkippedItem(key=key, reason=SKIP_EMPTY_TEXT)
                )
                continue
            row_above = rows[position - 1] if position > 0 else None
            row_below = (
                rows[position + 1] if position < len(rows) - 1 else None
            )
            line_plans.append(
                {
                    "request": EmbeddingRequest(
                        key=key,
                        input_text=format_row_embedding_input(
                            row, row_above, row_below
                        ),
                    ),
                    "kwargs": {
                        "receipt_id": receipt_id,
                        "image_id": image_id,
                        "line_id": primary_line_id,
                        "text": text,
                        "row_line_ids": [int(line.line_id) for line in row],
                        "merchant_name": merchant_name,
                        "place_id": place_id,
                        "section_type": self._row_section(
                            row, section_by_line
                        ),
                    },
                    "vector_field": "line_vector",
                }
            )

        word_plans: list[dict[str, Any]] = []
        for word in sorted(
            words, key=lambda word: (word.line_id, word.word_id)
        ):
            line_id = int(word.line_id)
            word_id = int(word.word_id)
            key = word_embedding_key(image_id, receipt_id, line_id, word_id)
            if (line_id, word_id) in existing_words:
                report.existing_word_keys.append(key)
                continue
            if not str(word.text).strip():
                report.failures.append(
                    SkippedItem(key=key, reason=SKIP_EMPTY_TEXT)
                )
                continue
            label_status, primary_label, valid_labels = _label_status(
                labels_by_word.get((line_id, word_id), [])
            )
            word_plans.append(
                {
                    "request": EmbeddingRequest(
                        key=key,
                        input_text=format_word_context_embedding_input(
                            word, words
                        ),
                    ),
                    "kwargs": {
                        "receipt_id": receipt_id,
                        "image_id": image_id,
                        "line_id": line_id,
                        "word_id": word_id,
                        "text": word.text,
                        "label_status": label_status,
                        "merchant_name": merchant_name,
                        "primary_label": primary_label,
                        "valid_labels": valid_labels or None,
                    },
                    "vector_field": "word_vector",
                }
            )
        return line_plans, word_plans

    @staticmethod
    def _row_section(
        row: Sequence[Any], section_by_line: Mapping[int, str]
    ) -> str | None:
        """Majority VALID section across the row's lines; ties abstain."""

        votes: dict[str, int] = defaultdict(int)
        for line in row:
            section_type = section_by_line.get(int(line.line_id))
            if section_type:
                votes[section_type] += 1
        if not votes:
            return None
        ranked = sorted(votes.items(), key=lambda vote: (-vote[1], vote[0]))
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return None
        return ranked[0][0]

    def _receipt_place(
        self, image_id: str, receipt_id: int
    ) -> tuple[str | None, str | None]:
        try:
            place = self._dynamo.get_receipt_place(image_id, receipt_id)
        except EntityNotFoundError:
            return None, None
        return (place.merchant_name or None), (place.place_id or None)

    def _valid_sections_by_line(
        self, image_id: str, receipt_id: int
    ) -> dict[int, str]:
        by_line: dict[int, str] = {}
        sections = self._dynamo.get_receipt_sections_from_receipt(
            image_id, receipt_id
        )
        for section in sections:
            status = getattr(
                section.validation_status, "value", section.validation_status
            )
            if str(status) != "VALID":
                continue
            section_type = getattr(
                section.section_type, "value", section.section_type
            )
            for line_id in section.line_ids:
                by_line[int(line_id)] = str(section_type)
        return by_line

    # ------------------------------------------------------------------
    # building and writing

    @staticmethod
    def _build_entities(
        report: ReceiptEmbedReport,
        plans: Sequence[Mapping[str, Any]],
        vectors: Mapping[str, list[float]],
        entity_class: type,
    ) -> list[Any]:
        entities = []
        for plan in plans:
            key = plan["request"].key
            vector = vectors.get(key)
            if vector is None:
                report.failures.append(
                    SkippedItem(key=key, reason=SKIP_MISSING_VECTOR)
                )
                continue
            kwargs = dict(plan["kwargs"])
            kwargs[plan["vector_field"]] = vector
            try:
                entities.append(entity_class(**kwargs))
            except ValueError as exc:
                report.failures.append(
                    SkippedItem(
                        key=key, reason=SKIP_INVALID_ITEM, detail=str(exc)
                    )
                )
        return entities

    @staticmethod
    def _write(
        report: ReceiptEmbedReport,
        entities: Sequence[Any],
        *,
        batch_write: Callable[[list[Any]], None],
        single_write: Callable[[Any], None],
        written_keys: list[str],
    ) -> None:
        """BatchWriteItem; degrade to per-item puts on batch failure."""

        if not entities:
            return

        def entity_key(entity: Any) -> str:
            primary_key = entity.key["PK"]["S"]
            sort_key = entity.key["SK"]["S"]
            return f"{primary_key}#{sort_key}".removesuffix("#EMBEDDING")

        try:
            batch_write(list(entities))
        except Exception as exc:  # noqa: BLE001 - degrade per item
            logger.warning(
                "batch embedding write failed (%s); retrying per item", exc
            )
            for entity in entities:
                try:
                    single_write(entity)
                except EntityAlreadyExistsError:
                    # The failed batch landed this item before erroring;
                    # it is written, not failed.
                    written_keys.append(entity_key(entity))
                except Exception as item_exc:  # noqa: BLE001
                    report.failures.append(
                        SkippedItem(
                            key=entity_key(entity),
                            reason=SKIP_WRITE_FAILED,
                            detail=str(item_exc),
                        )
                    )
                else:
                    written_keys.append(entity_key(entity))
            return
        written_keys.extend(entity_key(entity) for entity in entities)


__all__ = [
    "EMBEDDING_MODEL",
    "EmbedAndPutWriter",
    "EmbeddingRequest",
    "OpenAIVectorSource",
    "ReceiptEmbedReport",
    "SKIP_EMPTY_TEXT",
    "SKIP_INVALID_ITEM",
    "SKIP_MISSING_VECTOR",
    "SKIP_WRITE_FAILED",
    "SkippedItem",
    "VectorSource",
    "line_embedding_key",
    "word_embedding_key",
]

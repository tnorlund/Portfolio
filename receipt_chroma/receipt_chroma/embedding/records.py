"""Typed embedding record helpers for ChromaDB upserts.

These lightweight dataclasses keep embedding payloads (id, document text,
metadata) aligned with the same schema used for persisted snapshots/deltas.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, cast

from receipt_dynamo.entities import ReceiptLine, ReceiptWord, ReceiptWordLabel

from receipt_chroma.embedding.formatting.line_format import (
    format_line_context_embedding_input,
    parse_prev_next_from_formatted,
)
from receipt_chroma.embedding.formatting.word_format import get_word_neighbors
from receipt_chroma.embedding.metadata.line_metadata import (
    create_line_metadata,
    enrich_line_metadata_with_anchors,
)
from receipt_chroma.embedding.metadata.word_metadata import (
    WordMetadata,
    create_word_metadata,
    enrich_word_metadata_with_anchors,
    enrich_word_metadata_with_labels,
)


@dataclass(frozen=True)
class LineEmbeddingRecord:
    """Embedding + entity payload for a single receipt line."""

    line: ReceiptLine
    embedding: List[float]
    batch_id: Optional[str] = None

    @property
    def chroma_id(self) -> str:
        """Stable Chroma document id matching batch outputs and deltas."""
        return (
            f"IMAGE#{self.line.image_id}"
            f"#RECEIPT#{self.line.receipt_id:05d}"
            f"#LINE#{self.line.line_id:05d}"
        )

    @property
    def document(self) -> str:
        """Document text for Chroma."""
        return self.line.text


@dataclass(frozen=True)
class WordEmbeddingRecord:
    """Embedding + entity payload for a single receipt word."""

    word: ReceiptWord
    embedding: List[float]
    batch_id: Optional[str] = None

    @property
    def chroma_id(self) -> str:
        """Stable Chroma document id matching batch outputs and deltas."""
        return (
            f"IMAGE#{self.word.image_id}"
            f"#RECEIPT#{self.word.receipt_id:05d}"
            f"#LINE#{self.word.line_id:05d}"
            f"#WORD#{self.word.word_id:05d}"
        )

    @property
    def document(self) -> str:
        """Document text for Chroma."""
        return self.word.text


def build_line_payload(
    records: Iterable[LineEmbeddingRecord],
    all_lines: List[ReceiptLine],
    all_words: List[ReceiptWord],
    merchant_name: Optional[str] = None,
) -> Dict[str, List]:
    """Create Chroma-ready payloads (ids/embeddings/docs/metadatas) for lines.

    Uses the same metadata builders used when writing persisted deltas to
    guarantee schema parity between local snapshots and production snapshots.
    """
    ids: List[str] = []
    embeddings: List[List[float]] = []
    documents: List[str] = []
    metadatas: List[Dict[str, Any]] = []

    for record in records:
        line = record.line
        line_words = [w for w in all_words if w.line_id == line.line_id]
        avg_confidence = (
            sum(w.confidence for w in line_words) / len(line_words)
            if line_words
            else line.confidence
        )

        embedding_input = format_line_context_embedding_input(line, all_lines)
        prev_line, next_line = parse_prev_next_from_formatted(embedding_input)

        section_label = getattr(line, "section_label", None) or None
        line_metadata = create_line_metadata(
            line=line,
            prev_line=prev_line,
            next_line=next_line,
            merchant_name=merchant_name,
            avg_word_confidence=avg_confidence,
            section_label=section_label,
            source="openai_embedding_batch",
        )
        line_metadata = enrich_line_metadata_with_anchors(
            line_metadata, line_words
        )

        ids.append(record.chroma_id)
        embeddings.append(record.embedding)
        documents.append(record.document)
        metadatas.append(dict(line_metadata))

    return {
        "ids": ids,
        "embeddings": embeddings,
        "documents": documents,
        "metadatas": metadatas,
    }


def build_word_payload(
    records: Iterable[WordEmbeddingRecord],
    all_words: List[ReceiptWord],
    word_labels: List[ReceiptWordLabel],
    merchant_name: Optional[str] = None,
    context_size: int = 2,
) -> Dict[str, List]:
    """Create Chroma-ready payloads (ids/embeddings/docs/metadatas) for words."""
    ids: List[str] = []
    embeddings: List[List[float]] = []
    documents: List[str] = []
    metadatas: List[Dict[str, Any]] = []

    labels_by_key: Dict[tuple[str, int, int, int], List[ReceiptWordLabel]] = {}
    for label in word_labels:
        key = (label.image_id, label.receipt_id, label.line_id, label.word_id)
        labels_by_key.setdefault(key, []).append(label)

    for record in records:
        word = record.word
        left_context, right_context = get_word_neighbors(
            word, all_words, context_size=context_size
        )
        left_token = left_context[-1] if left_context else "<EDGE>"
        right_token = right_context[0] if right_context else "<EDGE>"

        metadata = create_word_metadata(
            word=word,
            left_word=left_token,
            right_word=right_token,
            merchant_name=merchant_name,
            label_status="unvalidated",
            source="openai_embedding_batch",
        )
        metadata_dict: Dict[str, Any] = dict(metadata)

        word_key = (word.image_id, word.receipt_id, word.line_id, word.word_id)
        metadata_with_labels = enrich_word_metadata_with_labels(
            cast(WordMetadata, metadata_dict), labels_by_key.get(word_key, [])
        )
        metadata_dict = enrich_word_metadata_with_anchors(
            cast(Dict[str, Any], metadata_with_labels), word
        )

        ids.append(record.chroma_id)
        embeddings.append(record.embedding)
        documents.append(record.document)
        metadatas.append(dict(metadata_dict))

    return {
        "ids": ids,
        "embeddings": embeddings,
        "documents": documents,
        "metadatas": metadatas,
    }

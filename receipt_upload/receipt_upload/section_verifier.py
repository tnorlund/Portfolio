"""Asynchronous KNN verification for deterministic section assignments."""

# Protocol surfaces are deliberately narrow; verification keeps all neighbor
# filtering evidence local for one auditable pass.
# pylint: disable=too-few-public-methods,too-many-locals

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from statistics import fmean
from typing import Any, Protocol

import numpy as np
from receipt_chroma import propagate_knn
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptRow, ReceiptSection

from receipt_upload.section_assignment import MODEL_SOURCE

VERIFICATION_SOURCE = "glyphstudio-knn-v1"
KNN_NEIGHBORS = 15


class VerificationStore(Protocol):
    """Strong-read/update operations used by the verifier."""

    def get_receipt_sections_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptSection]:
        """Return all sections for one receipt."""
        raise NotImplementedError

    def update_receipt_section(self, section: ReceiptSection) -> None:
        """Persist verification provenance on one section."""
        raise NotImplementedError


class LinesQuery(Protocol):
    """Chroma query surface used by the verifier."""

    def query(self, **kwargs: Any) -> dict[str, Any]:
        """Query line embeddings and metadata."""
        raise NotImplementedError


@dataclass(frozen=True)
class VerifiedRow:
    """Independent section prediction for one uploaded row."""

    row_id: int
    section_type: str
    confidence: float


def _metadata_line_ids(metadata: Mapping[str, Any]) -> list[int]:
    raw = metadata.get("row_line_ids", "[]")
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        values = []
    if not values and metadata.get("line_id") is not None:
        values = [metadata["line_id"]]
    return [int(value) for value in values]


def _valid_line_sections(
    dynamo: VerificationStore, image_id: str, receipt_id: int
) -> dict[int, str]:
    return {
        line_id: str(section.section_type)
        for section in dynamo.get_receipt_sections_from_receipt(
            image_id, receipt_id
        )
        if section.validation_status == ValidationStatus.VALID.value
        for line_id in section.line_ids
    }


def _candidate_label(
    metadata: Mapping[str, Any],
    cache: dict[tuple[str, int], dict[int, str]],
    dynamo: VerificationStore,
) -> str | None:
    image_id = str(metadata.get("image_id", ""))
    receipt_id = int(metadata.get("receipt_id", 0))
    if not image_id or receipt_id <= 0:
        return None
    key = (image_id, receipt_id)
    if key not in cache:
        cache[key] = _valid_line_sections(dynamo, image_id, receipt_id)
    votes = Counter(
        cache[key][line_id]
        for line_id in _metadata_line_ids(metadata)
        if line_id in cache[key]
    ).most_common(2)
    if not votes or (len(votes) > 1 and votes[0][1] == votes[1][1]):
        return None
    return votes[0][0]


def verify_receipt_sections(
    chroma: LinesQuery,
    dynamo: VerificationStore,
    rows: Sequence[ReceiptRow],
    row_embeddings: Sequence[Sequence[float]],
) -> list[VerifiedRow]:
    """Query cross-receipt VALID neighbors and annotate sync sections.

    The verifier never changes ``section_type`` or row membership. Any
    disagreement leaves (or demotes) the deterministic proposal to PENDING
    and records the independent prediction as provenance.
    """

    if not rows or not row_embeddings:
        return []
    if len(rows) != len(row_embeddings):
        raise ValueError("rows and row_embeddings must have equal length")
    result = chroma.query(
        collection_name="lines",
        query_embeddings=[list(embedding) for embedding in row_embeddings],
        n_results=KNN_NEIGHBORS,
        include=["embeddings", "metadatas"],
    )
    cache: dict[tuple[str, int], dict[int, str]] = {}
    verified: list[VerifiedRow] = []
    for row, query_embedding, metadatas, embeddings in zip(
        rows,
        row_embeddings,
        result.get("metadatas", []),
        result.get("embeddings", []),
        strict=True,
    ):
        training_embeddings = []
        training_labels = []
        for metadata, embedding in zip(metadatas, embeddings, strict=True):
            if (
                metadata.get("image_id") == row.image_id
                and int(metadata.get("receipt_id", 0)) == row.receipt_id
            ):
                continue
            label = _candidate_label(metadata, cache, dynamo)
            if label is not None:
                training_embeddings.append(embedding)
                training_labels.append(label)
        if not training_embeddings:
            continue
        propagated = propagate_knn(
            np.asarray(training_embeddings, dtype=np.float32),
            training_labels,
            np.asarray([query_embedding], dtype=np.float32),
            k=KNN_NEIGHBORS,
        )
        if float(propagated.confidence[0]) <= 0.0:
            continue
        verified.append(
            VerifiedRow(
                row_id=row.row_id,
                section_type=str(propagated.labels[0]),
                confidence=float(propagated.confidence[0]),
            )
        )

    _record_verification(dynamo, rows, verified)
    return verified


def _record_verification(
    dynamo: VerificationStore,
    rows: Sequence[ReceiptRow],
    verified: Sequence[VerifiedRow],
) -> None:
    if not rows:
        return
    by_row = {row.row_id: row for row in rows}
    predictions = {item.row_id: item for item in verified}
    sections = dynamo.get_receipt_sections_from_receipt(
        rows[0].image_id, rows[0].receipt_id
    )
    now = datetime.now(timezone.utc)
    for section in sections:
        if section.model_source != MODEL_SOURCE or not section.row_ids:
            continue
        available = [
            predictions[row_id]
            for row_id in section.row_ids
            if row_id in predictions and row_id in by_row
        ]
        disagreements = sorted(
            item.row_id
            for item in available
            if item.section_type != section.section_type
        )
        if disagreements:
            predicted = Counter(
                item.section_type
                for item in available
                if item.row_id in disagreements
            ).most_common(1)[0][0]
            status = "DISAGREED"
        elif available:
            predicted = str(section.section_type)
            status = "AGREED"
        else:
            predicted = None
            status = "ABSTAINED"
        updated = replace(
            section,
            validation_status=(
                ValidationStatus.PENDING.value
                if disagreements
                and section.validation_status == ValidationStatus.PENDING.value
                else section.validation_status
            ),
            verification_source=VERIFICATION_SOURCE,
            verification_status=status,
            verification_section_type=predicted,
            verification_confidence=(
                fmean(item.confidence for item in available)
                if available
                else None
            ),
            disagreement_row_ids=disagreements or None,
            verified_at=now,
        )
        dynamo.update_receipt_section(updated)


__all__ = [
    "KNN_NEIGHBORS",
    "VERIFICATION_SOURCE",
    "VerifiedRow",
    "verify_receipt_sections",
]

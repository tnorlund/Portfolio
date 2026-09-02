"""Canonical word-embedding ``label_status`` aggregation.

Any terminal human verdict (VALID or INVALID) maps to ``validated``, else
any PENDING maps to ``pending``, else ``none``. INVALID-only words must
stay in the validated population or the word index's
``label_status = validated`` filter would drop exactly the
counterexamples ``similar_labeled_words`` needs for ``evidence_against``
(E3 review P1-2; #1513 class).

This module is the single implementation of that rule. It lives in
``receipt_dynamo`` so every runtime that already depends on this package
can import one copy — including the stream-processor Lambda, whose
layers bundle ``receipt_dynamo`` + ``receipt_dynamo_stream`` but not
``receipt_embeddings``. ``receipt_embeddings`` re-exports these helpers
as the corpus-facing API.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from receipt_dynamo.constants import ValidationStatus

LabelStatusName = str


class WordLabelLike(Protocol):
    """Duck-typed word label: the fields the aggregation reads."""

    line_id: object
    word_id: object
    validation_status: object


def aggregate_word_label_status(statuses: Sequence[str]) -> LabelStatusName:
    """Collapse one word's validation_status values into label_status."""

    values = [str(status) for status in statuses]
    if (
        ValidationStatus.VALID.value in values
        or ValidationStatus.INVALID.value in values
    ):
        return "validated"
    if ValidationStatus.PENDING.value in values:
        return "pending"
    return "none"


def word_label_statuses(
    labels: Sequence[WordLabelLike],
) -> dict[tuple[int, int], LabelStatusName]:
    """Aggregate labels per (line_id, word_id): terminal-verdict rule."""

    by_word: dict[tuple[int, int], list[str]] = {}
    for label in labels:
        key = (int(label.line_id), int(label.word_id))
        by_word.setdefault(key, []).append(str(label.validation_status))
    return {
        key: aggregate_word_label_status(values)
        for key, values in by_word.items()
    }


__all__ = [
    "LabelStatusName",
    "WordLabelLike",
    "aggregate_word_label_status",
    "word_label_statuses",
]

"""Corpus-facing re-export of the word ``label_status`` rule.

The single implementation lives in ``receipt_dynamo.word_label_status``
so the stream-processor Lambda (which does not bundle this package) can
import the same function. Ingest, backfill, and resegment import through
this module — the corpus contract the polish brief names.
"""

from receipt_dynamo.word_label_status import (
    LabelStatusName,
    WordLabelLike,
    aggregate_word_label_status,
    word_label_statuses,
)

__all__ = [
    "LabelStatusName",
    "WordLabelLike",
    "aggregate_word_label_status",
    "word_label_statuses",
]

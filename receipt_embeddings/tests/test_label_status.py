"""Corpus-facing label_status re-export is the same function as dynamo."""

from receipt_dynamo.word_label_status import (
    aggregate_word_label_status as dynamo_aggregate,
)
from receipt_dynamo.word_label_status import (
    word_label_statuses as dynamo_statuses,
)

from receipt_embeddings.label_status import (
    aggregate_word_label_status,
    word_label_statuses,
)


def test_reexport_is_the_canonical_implementation() -> None:
    assert aggregate_word_label_status is dynamo_aggregate
    assert word_label_statuses is dynamo_statuses

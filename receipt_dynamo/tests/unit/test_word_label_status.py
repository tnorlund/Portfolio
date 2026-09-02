"""Canonical word-embedding label_status aggregation."""

from types import SimpleNamespace

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.word_label_status import (
    aggregate_word_label_status,
    word_label_statuses,
)


def test_aggregate_terminal_verdict_is_validated() -> None:
    assert aggregate_word_label_status([ValidationStatus.VALID.value]) == (
        "validated"
    )
    assert aggregate_word_label_status([ValidationStatus.INVALID.value]) == (
        "validated"
    )
    assert (
        aggregate_word_label_status(
            [ValidationStatus.PENDING.value, ValidationStatus.INVALID.value]
        )
        == "validated"
    )


def test_aggregate_pending_without_terminal_is_pending() -> None:
    assert aggregate_word_label_status([ValidationStatus.PENDING.value]) == (
        "pending"
    )


def test_aggregate_empty_or_other_is_none() -> None:
    assert aggregate_word_label_status([]) == "none"
    assert aggregate_word_label_status([ValidationStatus.NONE.value]) == "none"
    assert (
        aggregate_word_label_status([ValidationStatus.NEEDS_REVIEW.value])
        == "none"
    )


def test_word_label_statuses_groups_by_line_and_word() -> None:
    labels = [
        SimpleNamespace(line_id=1, word_id=1, validation_status="VALID"),
        SimpleNamespace(line_id=1, word_id=1, validation_status="PENDING"),
        SimpleNamespace(line_id=2, word_id=1, validation_status="INVALID"),
        SimpleNamespace(line_id=3, word_id=2, validation_status="PENDING"),
    ]
    assert word_label_statuses(labels) == {
        (1, 1): "validated",
        (2, 1): "validated",
        (3, 2): "pending",
    }

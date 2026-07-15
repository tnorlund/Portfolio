from types import SimpleNamespace
from unittest.mock import MagicMock

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_upload.merchant_resolution.embedding_processor import (
    _prepare_pending_core_labels,
    _reconcile_and_refresh_words_delta,
)


def _label(label: str) -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id="00000000-0000-4000-8000-000000000001",
        receipt_id=1,
        line_id=1,
        word_id=1,
        label=label,
        reasoning="test",
        timestamp_added="2026-01-01T00:00:00.000+00:00",
        validation_status=ValidationStatus.PENDING.value,
    )


def _word(word_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(line_id=1, word_id=word_id, text=text)


def test_prepare_pending_core_labels_maps_safe_aliases():
    dynamo = MagicMock()
    original = _label("PAYMENT_TYPE")
    word_labels = [original]

    pending = _prepare_pending_core_labels(
        dynamo=dynamo,
        word_labels=word_labels,
        label_proposed_by="test_guard",
    )

    assert original in word_labels
    assert original.validation_status == ValidationStatus.INVALID.value
    dynamo.update_receipt_word_label.assert_called_once_with(original)
    dynamo.add_receipt_word_label.assert_called_once()
    assert len(pending) == 1
    assert pending[0].label == "PAYMENT_METHOD"
    assert pending[0].validation_status == ValidationStatus.PENDING.value


def test_prepare_pending_core_labels_retains_ambiguous_labels_for_review():
    dynamo = MagicMock()
    original = _label("AMOUNT")

    pending = _prepare_pending_core_labels(
        dynamo=dynamo,
        word_labels=[original],
        label_proposed_by="test_guard",
    )

    assert pending == []
    assert original.validation_status == ValidationStatus.NEEDS_REVIEW.value
    dynamo.update_receipt_word_label.assert_called_once_with(original)
    dynamo.add_receipt_word_label.assert_not_called()


def test_prepare_pending_core_labels_classifies_amount_with_context():
    dynamo = MagicMock()
    original = _label("AMOUNT")
    original.word_id = 2
    word_labels = [original]

    pending = _prepare_pending_core_labels(
        dynamo=dynamo,
        word_labels=word_labels,
        label_proposed_by="test_guard",
        words=[_word(1, "Subtotal"), _word(2, "$8,82")],
    )

    assert pending == []
    assert original in word_labels
    assert original.validation_status == ValidationStatus.INVALID.value
    dynamo.update_receipt_word_label.assert_called_once_with(original)
    dynamo.add_receipt_word_label.assert_called_once()
    added = dynamo.add_receipt_word_label.call_args.args[0]
    assert added.label == "SUBTOTAL"
    assert added.validation_status == ValidationStatus.VALID.value
    assert added in word_labels


def test_prepare_pending_core_labels_keeps_ambiguous_amount_for_llm_with_context():
    dynamo = MagicMock()
    original = _label("AMOUNT")
    original.word_id = 2
    word_labels = [original]

    pending = _prepare_pending_core_labels(
        dynamo=dynamo,
        word_labels=word_labels,
        label_proposed_by="test_guard",
        words=[_word(1, "Random"), _word(2, "$8.82")],
    )

    assert pending == [original]
    assert word_labels == [original]
    dynamo.delete_receipt_word_label.assert_not_called()
    dynamo.add_receipt_word_label.assert_not_called()


def test_reconciliation_refreshes_words_delta_before_compaction(monkeypatch):
    dynamo = MagicMock()
    refreshed = [_label("GRAND_TOTAL")]
    dynamo.list_receipt_word_labels_for_receipt.return_value = (
        refreshed,
        None,
    )
    artifact = SimpleNamespace(
        validation_status="NEEDS_REVIEW",
        corrections=[{"conflict": True}, {"conflict": False}],
    )
    reconcile = MagicMock(return_value=artifact)
    resolved = SimpleNamespace(
        validation_status="VALID",
        field_count=6,
        conflicts=[],
    )
    resolve = MagicMock(return_value=resolved)
    build = MagicMock(return_value=({"ids": ["word"]}, None))
    upload = MagicMock(return_value="deltas/run/words")
    monkeypatch.setattr(
        "receipt_upload.merchant_resolution.embedding_processor."
        "reconcile_receipt_labels",
        reconcile,
    )
    monkeypatch.setattr(
        "receipt_upload.merchant_resolution.embedding_processor."
        "resolve_receipt_details",
        resolve,
    )
    monkeypatch.setattr(
        "receipt_upload.merchant_resolution.embedding_processor."
        "build_words_payload",
        build,
    )
    monkeypatch.setattr(
        "receipt_upload.merchant_resolution.embedding_processor."
        "upload_words_delta",
        upload,
    )

    prefix, stats = _reconcile_and_refresh_words_delta(
        dynamo=dynamo,
        image_id=refreshed[0].image_id,
        receipt_id=1,
        merchant_name="Vons",
        words=[],
        word_embeddings_list=[],
        run_id="run",
        chromadb_bucket="bucket",
        s3_client=object(),
    )

    assert prefix == "deltas/run/words"
    assert stats == {
        "d3_validation_status": "NEEDS_REVIEW",
        "d3_corrections": 2,
        "d3_conflicts": 1,
        "d4_validation_status": "VALID",
        "d4_fields": 6,
        "d4_conflicts": 0,
    }
    assert build.call_args.kwargs["word_labels"] == refreshed
    upload.assert_called_once()

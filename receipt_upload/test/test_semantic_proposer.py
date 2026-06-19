"""Tests for the semantic (Chroma kNN) PRODUCT_NAME proposer."""

from types import SimpleNamespace

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_upload.line_items import propose_product_names

IMAGE_ID = "00000000-0000-4000-8000-000000000def"


def _w(line_id, word_id, text, x, y):
    return SimpleNamespace(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": x, "y": y, "width": 0.1, "height": 0.02},
    )


def _anchor(line_id, word_id, label):
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning="anchor",
        timestamp_added="2026-01-01T00:00:00.000+00:00",
        validation_status=ValidationStatus.VALID.value,
    )


class _FakeClient:
    """Returns validated PRODUCT_NAME neighbors for any query."""

    def __init__(self, primary="PRODUCT_NAME"):
        self.primary = primary

    def query(self, **kwargs):
        return {
            "metadatas": [
                [
                    {"image_id": "other-1", "valid_labels_array": [self.primary]},
                    {"image_id": "other-2", "valid_labels_array": [self.primary]},
                    {"image_id": "other-3", "valid_labels_array": [self.primary]},
                ]
            ],
            "distances": [[0.2, 0.3, 0.4]],
        }


def _setup():
    words = [
        _w(1, 1, "STORE", 0.2, 0.95),
        _w(8, 1, "GREEK", 0.1, 0.70),     # unlabeled product (geometry missed)
        _w(8, 2, "YOGURT", 0.22, 0.70),
        _w(8, 3, "$5.99", 0.72, 0.70),    # money — never a product
        _w(13, 1, "$9.99", 0.72, 0.50),
    ]
    anchors = [_anchor(1, 1, "STORE_HOURS"), _anchor(13, 1, "GRAND_TOTAL")]
    embeddings = {(8, 1): [0.1] * 8, (8, 2): [0.1] * 8, (8, 3): [0.1] * 8}
    return words, anchors, embeddings


def test_proposes_product_name_for_unlabeled_in_band_word():
    words, anchors, embeddings = _setup()
    out = propose_product_names(words, anchors, _FakeClient(), embeddings)
    keys = {(l.line_id, l.word_id): l for l in out}
    assert (8, 1) in keys and keys[(8, 1)].label == "PRODUCT_NAME"
    assert keys[(8, 1)].validation_status == ValidationStatus.PENDING.value
    # money tokens are never proposed as product names
    assert (8, 3) not in keys


def test_skips_when_knn_majority_is_not_product():
    words, anchors, embeddings = _setup()
    out = propose_product_names(words, anchors, _FakeClient(primary="LINE_TOTAL"), embeddings)
    assert out == []


def test_does_not_propose_for_already_labeled_words():
    words, anchors, embeddings = _setup()
    anchors = anchors + [_anchor(8, 1, "PRODUCT_NAME")]
    out = propose_product_names(words, anchors, _FakeClient(), embeddings)
    assert (8, 1) not in {(l.line_id, l.word_id) for l in out}

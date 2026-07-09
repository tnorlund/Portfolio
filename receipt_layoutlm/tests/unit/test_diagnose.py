from types import SimpleNamespace

from receipt_layoutlm.diagnose import (
    _build_train_context,
    _build_evidence_summary,
    _receipt_features,
    _resolve_val_receipts,
    _summarize_groups,
    _token_error_rows,
)


def _word(line_id, word_id, text, x0, y0, x1, y1):
    return SimpleNamespace(
        image_id="img",
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        text=text,
        top_left={"x": x0, "y": y1},
        top_right={"x": x1, "y": y1},
        bottom_left={"x": x0, "y": y0},
        bottom_right={"x": x1, "y": y0},
    )


def _label(line_id, word_id, label):
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        label=label,
        validation_status="VALID",
    )


def test_receipt_features_builds_template_from_product_columns():
    details = SimpleNamespace(
        receipt=SimpleNamespace(image_id="img", receipt_id=1),
        place=SimpleNamespace(merchant_name="Vons", place_id="place-1"),
        lines=[SimpleNamespace(line_id=1), SimpleNamespace(line_id=2)],
        words=[
            _word(1, 1, "MILK", 0.1, 0.5, 0.3, 0.55),
            _word(1, 2, "2.99", 0.8, 0.5, 0.9, 0.55),
            _word(2, 1, "BREAD", 0.1, 0.4, 0.3, 0.45),
            _word(2, 2, "3.49", 0.8, 0.4, 0.9, 0.45),
        ],
        labels=[
            _label(1, 1, "PRODUCT_NAME"),
            _label(1, 2, "LINE_TOTAL"),
            _label(2, 1, "PRODUCT_NAME"),
            _label(2, 2, "LINE_TOTAL"),
        ],
    )

    features = _receipt_features(details, valid_status="VALID")

    assert features["merchant_name"] == "VONS"
    assert features["product_name_line_count"] == 2
    assert features["has_line_total_column"] is True
    assert "VONS" in features["template_signature"]
    assert "total:1" in features["line_item_shape"]


def test_receipt_features_uses_labeled_merchant_text_without_place():
    details = SimpleNamespace(
        receipt=SimpleNamespace(image_id="img", receipt_id=1),
        place=None,
        lines=[SimpleNamespace(line_id=1)],
        words=[
            _word(1, 1, "Acme", 0.1, 0.5, 0.2, 0.55),
            _word(1, 2, "Market", 0.2, 0.5, 0.35, 0.55),
        ],
        labels=[
            _label(1, 1, "MERCHANT_NAME"),
            _label(1, 2, "I-MERCHANT_NAME"),
        ],
    )

    features = _receipt_features(details, valid_status="VALID")

    assert features["merchant_name"] == "ACME MARKET"
    assert features["merchant_key"] == "ACME_MARKET"


def test_token_error_rows_classifies_high_confidence_product_false_positive():
    record = {
        "original": {
            "predictions": [
                {
                    "line_id": 1,
                    "word_id": 1,
                    "text": "Store",
                    "ground_truth_label": None,
                    "ground_truth_label_base": None,
                    "predicted_label": "B-PRODUCT_NAME",
                    "predicted_label_base": "PRODUCT_NAME",
                    "predicted_confidence": 0.41,
                    "is_correct": False,
                    "all_class_probabilities_base": {
                        "PRODUCT_NAME": 0.91,
                        "O": 0.04,
                    },
                }
            ]
        }
    }
    features = {
        "image_id": "img",
        "receipt_id": 1,
        "merchant_name": "VONS",
        "template_signature": "VONS|shape",
        "line_item_shape": "shape",
    }

    errors, summary = _token_error_rows(
        record, features=features, confidence_threshold=0.75
    )

    assert errors[0]["error_kind"] == "false_positive"
    assert errors[0]["product_related"] is True
    assert errors[0]["confidence"] == 0.91
    assert errors[0]["bio_confidence"] == 0.41
    assert summary["high_confidence_product_false_positives"] == 1


def test_resolve_val_receipts_rejects_persisted_hash_mismatch():
    try:
        _resolve_val_receipts(
            dynamo=object(),
            split_meta={"val_receipt_keys": ["img#00001"]},
            recorded_hash="wrong",
            recorded_seed=1,
            allow_hash_mismatch=False,
        )
    except ValueError as exc:
        assert "Validation hash mismatch" in str(exc)
    else:
        raise AssertionError("expected hash mismatch to raise")


def test_build_train_context_excludes_full_validation_split():
    def details_for(image_id, receipt_id):
        return SimpleNamespace(
            receipt=SimpleNamespace(image_id=image_id, receipt_id=receipt_id),
            place=SimpleNamespace(merchant_name=f"merchant-{image_id}"),
            lines=[],
            words=[],
            labels=[],
        )

    class FakeDynamo:
        def list_receipt_word_labels_with_status(self, *_args, **_kwargs):
            labels = [
                SimpleNamespace(image_id="val-a", receipt_id=1),
                SimpleNamespace(image_id="val-b", receipt_id=1),
                SimpleNamespace(image_id="train", receipt_id=1),
            ]
            return labels, None

        def get_receipt_details(self, image_id, receipt_id):
            return details_for(image_id, receipt_id)

    context = _build_train_context(
        FakeDynamo(),
        [("val-a", 1), ("val-b", 1)],
        split_meta={},
        valid_status="VALID",
    )

    assert context["source"] == "current_VALID_corpus_minus_full_validation_split"
    assert context["is_training_snapshot"] is False
    assert [feature["receipt_key"] for feature in context["features"]] == [
        "train#00001"
    ]


def test_evidence_summary_compares_seen_and_unseen_slices():
    rows = [
        {
            "merchant_name": "SEEN",
            "line_item_shape": "shape-a",
            "merchant_seen_in_context": True,
            "template_seen_in_context": True,
            "context_source": "current_VALID_corpus_minus_full_validation_split",
            "context_is_training_snapshot": False,
            "nearest_template_distance": 0.1,
            "product_detail_macro_f1": 0.8,
            "heldout_f1": 0.9,
            "token_accuracy": 0.95,
            "product_gold_entities": 4,
            "product_predicted_entities": 4,
            "incorrect_token_count": 1,
            "high_confidence_product_false_positives": 0,
        },
        {
            "merchant_name": "UNSEEN",
            "line_item_shape": "shape-b",
            "merchant_seen_in_context": False,
            "template_seen_in_context": False,
            "context_source": "current_VALID_corpus_minus_full_validation_split",
            "context_is_training_snapshot": False,
            "nearest_template_distance": 0.9,
            "product_detail_macro_f1": 0.2,
            "heldout_f1": 0.4,
            "token_accuracy": 0.8,
            "product_gold_entities": 4,
            "product_predicted_entities": 2,
            "incorrect_token_count": 5,
            "high_confidence_product_false_positives": 2,
        },
    ]

    evidence = _build_evidence_summary(rows, token_errors=[])
    groups = _summarize_groups(rows, "merchant_seen_in_context")

    assert evidence["template_coverage"]["seen_merchant_avg_product_macro_f1"] == 0.8
    assert evidence["template_coverage"]["unseen_merchant_avg_product_macro_f1"] == 0.2
    assert evidence["template_coverage"]["context_is_training_snapshot"] is False
    assert groups[0]["group"] == "False"

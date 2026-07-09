from types import SimpleNamespace

from receipt_layoutlm.diagnose import (
    _build_data_targeting,
    _build_train_context,
    _build_evidence_summary,
    _nearest_train_template,
    _product_false_positive_review,
    _receipt_features,
    _resolve_val_receipts,
    _summarize_groups,
    _template_distance,
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
                    "text": "Bacon",
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
    assert errors[0]["product_fp_review_bucket"] == "likely_unlabeled_product_text"
    assert errors[0]["product_fp_contract_action"] == "audit_gold_or_add_coverage"
    assert summary["high_confidence_product_false_positives"] == 1
    assert summary[
        "high_confidence_product_false_positive_review_bucket_counts"
    ] == {"likely_unlabeled_product_text": 1}


def test_product_false_positive_review_separates_adjustments_and_amounts():
    adjustment = _product_false_positive_review(
        gold="O",
        guessed="PRODUCT_NAME",
        text="REFUND",
        error_kind="false_positive",
    )
    amount = _product_false_positive_review(
        gold="O",
        guessed="LINE_TOTAL",
        text="12.96",
        error_kind="false_positive",
    )

    assert adjustment["bucket"] == "adjustment_or_fee_term"
    assert adjustment["contract_action"] == "confirm_adjustment_not_product"
    assert amount["bucket"] == "numeric_amount_overprediction"
    assert amount["contract_action"] == "review_amount_column_contract"


def test_product_false_positive_review_keeps_codes_and_meta_out_of_product_text():
    code = _product_false_positive_review(
        gold="O",
        guessed="PRODUCT_NAME",
        text="HDX-123",
        error_kind="false_positive",
    )
    meta = _product_false_positive_review(
        gold="O",
        guessed="PRODUCT_NAME",
        text="APPROVED",
        error_kind="false_positive",
    )

    assert code["bucket"] == "product_name_numeric_or_code"
    assert code["contract_action"] == "audit_sku_or_amount_boundary"
    assert meta["bucket"] == "receipt_meta_term"
    assert meta["contract_action"] == "keep_outside_product_contract"


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


def test_build_train_context_max_receipts_uses_deterministic_sample():
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
                SimpleNamespace(image_id=f"img-{suffix}", receipt_id=1)
                for suffix in ["a", "b", "c", "d", "e"]
            ]
            return labels, None

        def get_receipt_details(self, image_id, receipt_id):
            return details_for(image_id, receipt_id)

    context = _build_train_context(
        FakeDynamo(),
        [],
        split_meta={},
        valid_status="VALID",
        max_receipts=2,
    )

    assert [feature["receipt_key"] for feature in context["features"]] == [
        "img-d#00001",
        "img-e#00001",
    ]


def test_template_distance_applies_merchant_mismatch_penalty():
    target = {"merchant_key": "VONS", "template_vector": [0.0, 0.0]}
    same_merchant_near = {"merchant_key": "VONS", "template_vector": [0.5, 0.0]}
    other_merchant_same_vector = {
        "merchant_key": "TARGET",
        "template_vector": [0.0, 0.0],
    }

    assert _template_distance(target, same_merchant_near) == 0.5
    assert _template_distance(target, other_merchant_same_vector) == 0.75
    assert _template_distance(target, same_merchant_near) < _template_distance(
        target, other_merchant_same_vector
    )


def test_nearest_train_template_returns_closest_candidate_with_rounded_distance():
    target = {"merchant_key": "VONS", "template_vector": [0.0, 0.0]}
    train_features = [
        {
            "merchant_key": "TARGET",
            "template_vector": [0.0, 0.0],
            "receipt_key": "target#00001",
            "merchant_name": "TARGET",
        },
        {
            "merchant_key": "VONS",
            "template_vector": [0.333333, 0.0],
            "receipt_key": "vons#00001",
            "merchant_name": "VONS",
        },
    ]

    nearest = _nearest_train_template(target, train_features)

    assert nearest == {
        "distance": 0.3333,
        "receipt_key": "vons#00001",
        "merchant_name": "VONS",
    }


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


def test_data_targeting_prioritizes_structure_and_contract_queue():
    rows = [
        {
            "receipt_key": "img-a#00001",
            "merchant_name": "HOME DEPOT",
            "template_signature": "HOME_DEPOT|lines:40+|shape-a",
            "line_item_shape": "items:20-39|qty:1|unit:0|total:0",
            "product_detail_macro_f1": 0.1,
            "has_line_total_column": False,
            "product_name_line_count": 22,
        },
        {
            "receipt_key": "img-b#00001",
            "merchant_name": "VONS",
            "template_signature": "VONS|lines:1-4|shape-b",
            "line_item_shape": "items:1-4|qty:0|unit:0|total:1",
            "product_detail_macro_f1": 0.7,
            "has_line_total_column": True,
            "product_name_line_count": 2,
        },
        {
            "receipt_key": "img-c#00001",
            "merchant_name": "VONS",
            "template_signature": "VONS|lines:40+|shape-c",
            "line_item_shape": "items:1-4|qty:0|unit:0|total:1",
            "product_detail_macro_f1": 0.0,
            "has_line_total_column": True,
            "product_name_line_count": 1,
        },
    ]
    token_errors = [
        {
            "receipt_key": "img-a#00001",
            "merchant_name": "HOME DEPOT",
            "template_signature": "HOME_DEPOT|lines:40+|shape-a",
            "line_item_shape": "items:20-39|qty:1|unit:0|total:0",
            "error_kind": "false_positive",
            "high_confidence": True,
            "predicted_label_base": "PRODUCT_NAME",
            "product_fp_review_bucket": "likely_unlabeled_product_text",
        },
        {
            "receipt_key": "img-a#00001",
            "merchant_name": "HOME DEPOT",
            "template_signature": "HOME_DEPOT|lines:40+|shape-a",
            "line_item_shape": "items:20-39|qty:1|unit:0|total:0",
            "error_kind": "false_positive",
            "high_confidence": True,
            "predicted_label_base": "LINE_TOTAL",
            "product_fp_review_bucket": "numeric_amount_overprediction",
        },
    ]

    targets = _build_data_targeting(rows, token_errors)

    assert targets["priority_merchant_templates"][0]["template_signature"] == (
        "VONS|lines:40+|shape-c"
    )
    assert targets["priority_merchant_templates"][1]["template_signature"] == (
        "HOME_DEPOT|lines:40+|shape-a"
    )
    assert targets["structural_gaps"]["no_line_total_layouts"]["receipt_count"] == 1
    assert targets["structural_gaps"]["long_item_tables"]["receipt_count"] == 1
    assert targets["label_contract_queue"][
        "likely_unlabeled_product_text_tokens"
    ] == 1

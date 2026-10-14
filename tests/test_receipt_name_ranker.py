"""Leakage and conservative invariants for the offline name experiment."""

from __future__ import annotations

import copy

from experiments.receipt_name_ranker import (
    FEATURES,
    candidates,
    cents,
    features,
    manual_target,
    merchant_group,
    metrics,
    normalize,
    predict,
    train,
    training_pairs,
)


def word(line: int, text: str, x: float = 0.1) -> dict:
    return {
        "line_id": line,
        "word_id": 1,
        "text": text,
        "x": x,
        "y_mid": 0.5,
        "h": 0.02,
    }


def test_candidates_never_borrow_another_block() -> None:
    item = {"name": "123456 WD-40 12OZ", "line_ids": [1]}
    choices = candidates(item, [word(1, "WD-40"), word(2, "DONOR")])
    assert all("DONOR" not in c["name"] for c in choices)
    assert all(ref["line_id"] == 1 for c in choices for ref in c["refs"])
    assert choices[0]["name"] == item["name"]


def test_sku_only_baseline_survives_missing_description() -> None:
    item = {"name": "123456", "line_ids": [1]}
    choices = candidates(item, [word(1, "123456")])
    assert len(choices) == 1


def test_labels_require_unique_price_carrier_and_price() -> None:
    item = {"price": 3, "price_word_id": {"line_id": 1}}
    truth = [
        {"name": "ONE", "price": 3, "line_ids": [1]},
        {"name": "TWO", "price": 3, "line_ids": [2]},
    ]
    assert manual_target(item, truth) == truth[0]
    assert manual_target(item, truth + [truth[0]]) is None
    assert manual_target({**item, "price": 4}, truth) is None


def test_unrecoverable_truth_is_not_a_negative_training_label() -> None:
    item = {
        "name": "CODE",
        "price": 3,
        "line_ids": [1],
        "price_word_id": {"line_id": 1},
    }
    receipt = {
        "pred": [item],
        "choices": [candidates(item, [])],
        "truth": [{"name": "MISSING OCR", "price": 3, "line_ids": [1]}],
    }
    assert training_pairs([receipt]) == []


def test_prediction_cannot_change_price_count_or_input() -> None:
    item = {
        "name": "CODE",
        "price": 3.15,
        "line_ids": [1],
        "quantity": 1,
        "price_word_id": {"line_id": 1},
    }
    choices = candidates(item, [word(1, "descriptive product")])
    receipt = {"pred": [item], "choices": [choices]}
    original = copy.deepcopy(receipt)
    weights = [0.0] * len(FEATURES)
    weights[-1] = -10  # Deliberately force an alternative candidate.
    result, count = predict(receipt, weights, 0.9)
    assert count == 1
    assert len(result) == 1
    assert result[0]["price"] == 3.15
    assert result[0]["price_word_id"] == item["price_word_id"]
    assert result[0]["quantity"] == 1
    assert receipt == original


def test_zero_weight_model_abstains() -> None:
    item = {"name": "CODE", "line_ids": [1]}
    receipt = {
        "pred": [item],
        "choices": [candidates(item, [word(1, "description")])],
    }
    assert predict(receipt, [0.0] * len(FEATURES), 0.9)[1] == 0


def test_training_is_deterministic_and_prefers_positive_features() -> None:
    pair = [1.0] + [0.0] * (len(FEATURES) - 1)
    assert train([pair]) == train([pair])
    assert train([pair])[0] > 0


def test_canonical_merchant_groups_prevent_chain_leakage() -> None:
    assert merchant_group("Trader Joe's") == merchant_group("TRADER JOE'S")
    assert merchant_group("Wild Fork") == merchant_group(
        "Wild Fork Meat & Seafood Market - Thousand Oaks"
    )


def test_scoring_preserves_names_numbers_signs_and_duplicates() -> None:
    assert normalize("WD-40 12OZ") != normalize("WD-40 10OZ")
    assert cents("-$3.15") == -315
    truth = [{"name": "A", "price": 1}] * 2
    result = metrics(truth, [{"name": "A", "price": 1}], 2)
    assert result["joint_tp"] == 1
    assert result["missing_joint"] == 1
    assert result["subtotal_delta_cents"] == -100


def test_subtotal_includes_signed_discount_but_item_score_excludes_it() -> (
    None
):
    items = [
        {"name": "A", "price": 5},
        {"name": "DISCOUNT", "price": -1, "is_discount": True},
    ]
    result = metrics(items, items, 4)
    assert result["subtotal_exact"] is True
    assert result["truth"] == result["predicted"] == 1


def test_feature_dimension_and_bounds() -> None:
    values = features("123456 WD-40 12OZ", True)
    assert len(values) == len(FEATURES)
    assert all(0 <= value <= 1 for value in values)


def test_filtered_metrics_keep_full_subtotal() -> None:
    certain = [{"name": "A", "price": 5}]
    full = certain + [{"name": "uncertain", "price": 3}]
    result = metrics(certain, certain, 8, subtotal_predictions=full)
    assert result["truth"] == result["predicted"] == 1
    assert result["subtotal_exact"] is True

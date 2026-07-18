from receipt_layoutlm.evaluate_checkpoints import _evaluation_diagnostics


def test_evaluation_diagnostics_explains_prediction_balance():
    diagnostics = _evaluation_diagnostics(
        [
            ["B-PRODUCT_NAME", "I-PRODUCT_NAME", "B-LINE_TOTAL", "O"],
            ["B-QUANTITY", "B-UNIT_PRICE"],
        ],
        [
            ["B-PRODUCT_NAME", "O", "B-SUBTOTAL", "O"],
            ["B-QUANTITY", "B-UNIT_PRICE"],
        ],
        per_label_f1={
            "PRODUCT_NAME": 0.5,
            "QUANTITY": 1.0,
            "UNIT_PRICE": 1.0,
            "LINE_TOTAL": 0.0,
        },
    )

    assert diagnostics["token_counts"]["total"] == 6
    assert diagnostics["token_counts"]["gold_entity"] == 5
    assert diagnostics["token_counts"]["predicted_entity"] == 4
    assert diagnostics["rates"]["gold_entity_token_rate"] == 5 / 6
    assert diagnostics["rates"]["predicted_entity_token_rate"] == 4 / 6
    assert diagnostics["product_detail"]["macro_f1"] == 0.625
    assert diagnostics["per_label_entity_counts"]["PRODUCT_NAME"] == {
        "gold_entities": 1,
        "predicted_entities": 1,
        "prediction_gold_ratio": 1.0,
    }
    assert {
        "gold": "LINE_TOTAL",
        "predicted": "SUBTOTAL",
        "count": 1,
    } in diagnostics["top_token_confusions"]

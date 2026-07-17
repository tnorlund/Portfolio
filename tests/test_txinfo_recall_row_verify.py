from __future__ import annotations

from scripts.txinfo_recall_row_verify import (
    ACCEPT,
    REJECT,
    REVIEW,
    default_decision,
)


def candidate(text: str, *signals: str) -> dict:
    return {"id": f"id:{text}", "line_text": text, "signals": list(signals)}


def row(lines: list[tuple[str, bool]], candidates: list[dict]) -> dict:
    return {
        "row_lines": [
            {"line_id": index, "text": text, "is_final_candidate": is_candidate}
            for index, (text, is_candidate) in enumerate(lines, start=1)
        ],
        "candidates": candidates,
    }


def test_spatial_emv_owner_defeats_context_only_candidate() -> None:
    item = candidate("A800", "paired-terminal")
    decision, family, _reason = default_decision(
        row([("TSI:", False), ("A800", True)], [item])
    )
    assert decision == REJECT
    assert family == "spatial-payment-owner"


def test_compact_reference_row_takes_precedence_over_authorization() -> None:
    item = candidate("Ref# 316494", "transaction", "paired-authorization")
    decision, family, _reason = default_decision(
        row([("Auth# 60786Z", False), ("Ref# 316494", True)], [item])
    )
    assert decision == ACCEPT
    assert family == "compact-identifier-metadata"


def test_payment_payload_row_is_kept_in_payment() -> None:
    item = candidate("Ref# 316494", "transaction")
    decision, family, _reason = default_decision(
        row(
            [
                ("Card # ****1234", False),
                ("Ref# 316494", True),
                ("Total: 9.95", False),
                ("AID: A000000003", False),
            ],
            [item],
        )
    )
    assert decision == REJECT
    assert family == "mixed-payment-payload"


def test_transaction_item_count_is_not_metadata() -> None:
    item = candidate("Items in Transaction: 6", "transaction")
    decision, family, _reason = default_decision(
        row([("Items in Transaction: 6", True)], [item])
    )
    assert decision == REJECT
    assert family == "non-metadata-transaction-phrase"


def test_direct_label_with_value_like_sibling_is_accepted() -> None:
    item = candidate("Terminal ID:", "terminal")
    decision, family, _reason = default_decision(
        row([("Terminal ID:", True), ("***3270", False)], [item])
    )
    assert decision == ACCEPT
    assert family == "compact-identifier-metadata"


def test_context_only_neutral_row_requires_review() -> None:
    item = candidate("77431", "barcode-register-stamp")
    decision, family, _reason = default_decision(
        row([("#0", False), ("77431", True)], [item])
    )
    assert decision == REVIEW
    assert family == "context-only-with-neutral-siblings"

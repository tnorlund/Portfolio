"""Tests for the structural fingerprint + archetype clustering (M6).

Covers fingerprint extraction from export-shaped words+labels, deterministic
archetype classification (line_item_retail / service / restaurant_tip /
unknown), confidence, and cross-merchant clustering with a STRUCTURE-only
aggregate prior (never content).
"""

import pytest

from receipt_agent.agents.label_evaluator.merchant_research.structure import (
    LINE_ITEM_RETAIL,
    RESTAURANT_TIP,
    SERVICE,
    UNKNOWN,
    classify_archetype,
    cluster_fingerprints,
    fingerprint_from_labeled_words,
    service_grounding_contract,
    structure_review_status,
    summarize_merchant_structure,
)


def _word(rid, lid, wid, x, y, w=0.1, h=0.02, text="x"):
    return {
        "receipt_id": rid,
        "line_id": lid,
        "word_id": wid,
        "text": text,
        "bounding_box": {"x": x, "y": y, "width": w, "height": h},
        "top_right": {"x": x + w, "y": y + h},
    }


def _lab(rid, lid, wid, label):
    return {"receipt_id": rid, "line_id": lid, "word_id": wid, "label": label}


def _receipt(item_rows, *, totals=True, grand=True, tip=False, payment=True,
             price_x=0.8, rid=1):
    """Build (words, labels) for a synthetic receipt.

    ``item_rows`` is the number of PRODUCT_NAME + LINE_TOTAL item lines.
    """
    words, labels = [], []
    wid = 0
    lid = 0

    def add(label, x, y, text="x"):
        nonlocal wid, lid
        wid += 1
        lid += 1
        words.append(_word(rid, lid, wid, x, y, text=text))
        labels.append(_lab(rid, lid, wid, label))

    # header at top (y high)
    add("MERCHANT_NAME", 0.3, 0.95)
    add("ADDRESS_LINE", 0.3, 0.92)
    # item rows in the middle
    for i in range(item_rows):
        y = 0.80 - i * 0.03
        add("PRODUCT_NAME", 0.1, y)
        # price column aligned at price_x
        wid += 1
        lid += 1
        words.append(_word(rid, lid, wid, price_x, y, w=0.08, text="1.99"))
        labels.append(_lab(rid, lid, wid, "LINE_TOTAL"))
    yb = 0.80 - item_rows * 0.03 - 0.03
    if totals:
        add("SUBTOTAL", 0.7, yb)
        add("TAX", 0.7, yb - 0.03)
    if grand:
        add("GRAND_TOTAL", 0.7, yb - 0.06)
    if tip:
        add("TIP", 0.7, yb - 0.09)
    if payment:
        add("PAYMENT_METHOD", 0.3, yb - 0.12)
    return words, labels


# --------------------------------------------------------------------------- #
# Fingerprint extraction
# --------------------------------------------------------------------------- #


def test_fingerprint_counts_items_and_regions():
    words, labels = _receipt(5)
    fp = fingerprint_from_labeled_words(words, labels)
    assert fp.line_item_count == 5
    assert fp.has_price_column is True  # aligned at price_x
    assert fp.has_totals_block is True
    assert fp.has_grand_total is True
    assert fp.has_payment is True
    # region order top->bottom
    assert fp.region_sequence[0] == "header"
    assert "items" in fp.region_sequence
    assert fp.price_column_x == pytest.approx(0.88, abs=0.001)  # x(0.8)+width(0.08)


def test_fingerprint_handles_missing_geometry():
    words = [{"receipt_id": 1, "line_id": 1, "word_id": 1, "text": "x"}]
    labels = [_lab(1, 1, 1, "GRAND_TOTAL")]
    fp = fingerprint_from_labeled_words(words, labels)
    assert fp.has_grand_total is True
    assert fp.price_column_x is None
    assert fp.row_spacing is None


def test_ragged_price_column_is_not_flagged_aligned():
    # Items with scattered (unaligned) price x -> has_price_column False, but it
    # is still a grid (>=2 items).
    words, labels = [], []
    for i in range(4):
        y = 0.8 - i * 0.03
        words.append(_word(1, 2 * i + 1, 2 * i + 1, 0.1, y))
        labels.append(_lab(1, 2 * i + 1, 2 * i + 1, "PRODUCT_NAME"))
        # wildly varying x
        px = 0.5 + (i % 2) * 0.3
        words.append(_word(1, 2 * i + 2, 2 * i + 2, px, y, w=0.05))
        labels.append(_lab(1, 2 * i + 2, 2 * i + 2, "LINE_TOTAL"))
    fp = fingerprint_from_labeled_words(words, labels)
    assert fp.line_item_count == 4
    assert fp.has_price_column is False
    assert classify_archetype(fp).archetype == LINE_ITEM_RETAIL  # still a grid


# --------------------------------------------------------------------------- #
# Archetype classification
# --------------------------------------------------------------------------- #


def test_line_item_retail_high_confidence():
    fp = fingerprint_from_labeled_words(*_receipt(6))
    a = classify_archetype(fp)
    assert a.archetype == LINE_ITEM_RETAIL
    assert a.confidence == "high"  # >=4 items, totals, aligned column


def test_small_grid_is_line_item_medium():
    fp = fingerprint_from_labeled_words(*_receipt(2))
    a = classify_archetype(fp)
    assert a.archetype == LINE_ITEM_RETAIL
    assert a.confidence == "medium"


def test_single_service_amount_is_service():
    # 0 item rows but a grand total -> service (high).
    fp = fingerprint_from_labeled_words(*_receipt(0, totals=False, grand=True))
    a = classify_archetype(fp)
    assert a.archetype == SERVICE
    assert a.confidence == "high"


def test_one_item_with_total_is_service_medium():
    fp = fingerprint_from_labeled_words(*_receipt(1, grand=True))
    a = classify_archetype(fp)
    assert a.archetype == SERVICE
    assert a.confidence == "medium"


def test_split_amount_tokens_do_not_inflate_item_count():
    # A single service amount OCR'd as two LINE_TOTAL tokens on the SAME line
    # ("$" + "20.00") must count as ONE priced row, so a clean service receipt
    # is not misclassified as a 2-item grid. (codex M6 MEDIUM.)
    words = [
        _word(1, 1, 1, 0.3, 0.95, text="SALON"),
        _word(1, 5, 5, 0.78, 0.6, w=0.02, text="$"),
        _word(1, 5, 6, 0.80, 0.6, w=0.08, text="20.00"),
        _word(1, 8, 8, 0.7, 0.4, text="20.00"),
    ]
    labels = [
        _lab(1, 1, 1, "MERCHANT_NAME"),
        _lab(1, 5, 5, "LINE_TOTAL"),
        _lab(1, 5, 6, "LINE_TOTAL"),
        _lab(1, 8, 8, "GRAND_TOTAL"),
    ]
    fp = fingerprint_from_labeled_words(words, labels)
    assert fp.line_item_count == 1  # one priced ROW, not two words
    assert classify_archetype(fp).archetype == SERVICE


def test_tip_line_makes_restaurant_tip():
    fp = fingerprint_from_labeled_words(*_receipt(5, tip=True))
    a = classify_archetype(fp)
    assert a.archetype == RESTAURANT_TIP


def test_no_items_no_total_is_unknown():
    fp = fingerprint_from_labeled_words(*_receipt(0, totals=False, grand=False))
    a = classify_archetype(fp)
    assert a.archetype == UNKNOWN
    assert a.confidence == "low"


def test_archetype_is_structural_not_merchant():
    # Same structure on different receipt_ids -> same archetype (no merchant input).
    a1 = classify_archetype(fingerprint_from_labeled_words(*_receipt(5, rid=1)))
    a2 = classify_archetype(fingerprint_from_labeled_words(*_receipt(5, rid=99)))
    assert a1.archetype == a2.archetype == LINE_ITEM_RETAIL


# --------------------------------------------------------------------------- #
# Clustering + aggregate structural prior
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Per-merchant structure summary (M7)
# --------------------------------------------------------------------------- #


def test_merchant_summary_line_item_high_confidence():
    # Dominantly itemized (Vons-like) -> line_item, high, line-item ops included.
    ms = summarize_merchant_structure(
        {"line_item_retail": 24, "service": 2, "restaurant_tip": 1, "unknown": 1}
    )
    assert ms.structure_type == "line_item"
    assert ms.confidence == "high"
    assert "add_line_item" in ms.applicable_operations
    assert structure_review_status(ms.confidence) == "auto_approved"


def test_merchant_summary_service_excludes_line_item_ops():
    # Dominantly single-service (Tan L.A.-like) -> service; NO line-item ops.
    ms = summarize_merchant_structure({"service": 2, "line_item_retail": 1})
    assert ms.structure_type == "service"
    assert "add_line_item" not in ms.applicable_operations
    assert "remove_line_item" not in ms.applicable_operations
    assert set(ms.applicable_operations) == {
        "replace_field", "amount_mutation", "compose_header", "hard_negative",
    }
    # A new/split merchant is parked, not auto-trusted.
    assert ms.confidence == "medium"
    assert structure_review_status(ms.confidence) == "needs_review"


def test_merchant_summary_split_is_hybrid():
    ms = summarize_merchant_structure({"line_item_retail": 3, "service": 3})
    assert ms.structure_type == "hybrid"


def test_merchant_summary_empty_is_low_confidence():
    ms = summarize_merchant_structure({})
    assert ms.confidence == "low"
    assert ms.structure_type == "hybrid"


def test_structure_review_status_only_high_auto_approves():
    assert structure_review_status("high") == "auto_approved"
    assert structure_review_status("medium") == "needs_review"
    assert structure_review_status("low") == "needs_review"


def test_summary_is_deterministic():
    mix = {"service": 2, "line_item_retail": 1}
    assert summarize_merchant_structure(mix).to_dict() == summarize_merchant_structure(mix).to_dict()


# --------------------------------------------------------------------------- #
# Service grounding contract hook — safety on malformed input
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad",
    [None, "bad", 123, [], {"structure_type": "service", "applicable_operations": 7},
     {"structure_type": "service", "status": "needs_review", "applicable_operations": None}],
)
def test_service_grounding_contract_never_raises_and_defaults_safe(bad):
    c = service_grounding_contract(bad)
    # Malformed / non-service / parked -> never grants the override, never raises.
    assert c["valid_grounding_without_line_items"] is False
    assert isinstance(c["applicable_operations"], list)


def test_service_grounding_contract_grants_only_when_approved_service():
    parked = {"structure_type": "service", "status": "needs_review",
              "applicable_operations": ["replace_field"]}
    approved = {"structure_type": "service", "status": "approved",
                "applicable_operations": ["replace_field", "amount_mutation"]}
    auto = {"structure_type": "service", "status": "auto_approved",
            "applicable_operations": ["replace_field"]}
    assert service_grounding_contract(parked)["valid_grounding_without_line_items"] is False
    assert service_grounding_contract(approved)["valid_grounding_without_line_items"] is True
    assert service_grounding_contract(auto)["valid_grounding_without_line_items"] is True


def test_cluster_groups_by_archetype_with_prior():
    fps = [
        fingerprint_from_labeled_words(*_receipt(6)),  # retail
        fingerprint_from_labeled_words(*_receipt(5)),  # retail
        fingerprint_from_labeled_words(*_receipt(0, totals=False, grand=True)),  # service
    ]
    clusters = {c.archetype: c for c in cluster_fingerprints(fps)}
    assert clusters[LINE_ITEM_RETAIL].size == 2
    assert clusters[SERVICE].size == 1
    assert clusters[LINE_ITEM_RETAIL].cluster_id == "cluster:line_item_retail"
    prior = clusters[LINE_ITEM_RETAIL].structural_prior
    # Aggregate prior is STRUCTURE only: layout/spacing/label arrangement, no
    # items/prices/text content.
    assert prior["receipt_count"] == 2
    assert prior["mean_line_item_count"] == pytest.approx(5.5)
    assert "typical_region_sequence" in prior
    assert "label_arrangement" in prior
    assert "PRODUCT_NAME" not in str(prior.get("label_arrangement", {}).get("__content__", ""))
    # prior must not leak any item text/price keys
    assert all(k in {
        "receipt_count", "typical_region_sequence", "mean_line_item_count",
        "mean_row_spacing", "mean_price_column_x", "label_arrangement",
    } for k in prior)

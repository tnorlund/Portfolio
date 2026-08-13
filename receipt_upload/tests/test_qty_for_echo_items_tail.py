"""FOR-deal echo absorption, ``$N.NN /`` unit-rate META, ITEMS-tail skip.

Closes two Sprouts nears without minting prices:

* ``a0301717``: band ``2.00 FOR 3 @ 3`` restates LIMES $2.00. Constraint
  cannot drop it (``FOR`` has letters). Absorb into the adjacent named
  SKU at the same price. Two named SKUs at 8.98 (Wild Fork) both survive.
* ``795bc26a``: in-zone phantom ``$2.99 /`` decoded as ``lb / lb`` cancels
  missing garlic $2.99. Mark slash unit-rates META, then skip unpriced
  BOGO / Sale Price annotation rows so ITEMS-boundary extension can reach
  You-Pay 1.99 + bag 0.10.

Claimed-department-header skip (DAIRY) is a later stacked PR.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from receipt_dynamo.entities.receipt_section import ReceiptSection

from receipt_upload.line_items.geometry import (
    evaluate_items_zone,
    extract_items,
    is_for_deal_annotation,
    is_unit_rate_row,
    propose_items_boundary_extension,
)

IMAGE_ID = "a0301717-d765-4f34-a15d-48c362ebf9fd"


def w(line_id, word_id, text, x, y, h=0.02):
    return {
        "line_id": line_id,
        "word_id": word_id,
        "text": text,
        "x": x,
        "y_mid": y,
        "h": h,
    }


def _items_section(line_ids):
    return ReceiptSection(
        receipt_id=1,
        image_id=IMAGE_ID,
        section_type="ITEMS",
        line_ids=list(line_ids),
        row_ids=list(line_ids),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        model_source="section-seed-v0",
        validation_status="VALID",
    )


def _row(line_id, y):
    return SimpleNamespace(row_id=line_id, line_ids=[line_id], y_min=y)


def test_amount_first_for_deal_is_an_annotation_not_a_sku():
    assert is_for_deal_annotation("2.00 FOR 3 @ 3")
    assert is_for_deal_annotation("$2.00 FOR 3")
    assert is_for_deal_annotation("4 FOR 1.00")
    assert not is_for_deal_annotation("ORGANIC LIMES 2.00 FOR 3")
    assert not is_for_deal_annotation("CHICKEN THIGH 8.98")


def test_for_deal_echo_absorbs_into_named_limes():
    # a0301717: LIMES $2.00 plus deal annotation echoing the same price.
    words = [
        w(1, 1, "ORGANIC", 0.05, 0.50),
        w(1, 2, "LIMES", 0.22, 0.50),
        w(2, 1, "2.00", 0.80, 0.50),
        w(3, 1, "2.00", 0.10, 0.44),
        w(3, 2, "FOR", 0.22, 0.44),
        w(3, 3, "3", 0.32, 0.44),
        w(3, 4, "@", 0.40, 0.44),
        w(3, 5, "3", 0.48, 0.44),
        w(4, 1, "MILK", 0.05, 0.38),
        w(5, 1, "3.99", 0.80, 0.38),
    ]
    items, _ = extract_items(
        words, {1, 2, 3, 4, 5}, summary={"subtotal": 5.99}
    )
    prices = [i["price"] for i in items]
    assert prices.count(2.00) == 1, [i["name"] for i in items]
    by_price = {i["price"]: i["name"] for i in items}
    assert "LIMES" in by_price[2.00]
    assert by_price[3.99] == "MILK"
    zone = evaluate_items_zone(words, {"subtotal": 5.99}, {1, 2, 3, 4, 5})
    assert zone["status"] == "match"
    assert zone["items_sum"] == 5.99


def test_two_named_skus_at_same_price_are_not_absorbed():
    # Wild Fork veto: two real items both $8.98 must both survive.
    words = [
        w(1, 1, "CHICKEN", 0.05, 0.50),
        w(1, 2, "THIGH", 0.22, 0.50),
        w(2, 1, "8.98", 0.80, 0.50),
        w(3, 1, "PORK", 0.05, 0.38),
        w(3, 2, "BELLY", 0.22, 0.38),
        w(4, 1, "8.98", 0.80, 0.38),
    ]
    items, _ = extract_items(words, {1, 2, 3, 4}, summary={"subtotal": 17.96})
    assert sorted(i["price"] for i in items) == [8.98, 8.98]
    names = {i["name"] for i in items}
    assert any("CHICKEN" in n for n in names)
    assert any("PORK" in n for n in names)


def test_slash_unit_rate_is_meta():
    assert is_unit_rate_row("$2.99 /", 1)
    assert is_unit_rate_row("$2.99 / lb", 1)
    assert is_unit_rate_row("lb $2.99 / lb", 1)
    assert not is_unit_rate_row("1.23 lb @ $4.99/lb", 1)
    assert not is_unit_rate_row("GROUND BEEF $4.99 per lb 7.48", 2)


def test_slash_unit_rate_does_not_emit_lb_slash_lb():
    # 795bc26a: shallots 0.96 (qty attach) plus in-zone ``$2.99 /``.
    words = [
        w(1, 1, "SHALLOTS", 0.05, 0.50),
        w(1, 2, "0.57", 0.28, 0.50),
        w(1, 3, "lb", 0.38, 0.50),
        w(1, 4, "@", 0.46, 0.50),
        w(1, 5, "$1.69", 0.54, 0.50),
        w(2, 1, "0.96", 0.80, 0.50),
        w(3, 1, "lb", 0.05, 0.42),
        w(3, 2, "$2.99", 0.20, 0.42),
        w(3, 3, "/", 0.36, 0.42),
        w(3, 4, "lb", 0.44, 0.42),
    ]
    items, _ = extract_items(words, {1, 2, 3}, summary={"subtotal": 0.96})
    prices = [i["price"] for i in items]
    assert 2.99 not in prices
    assert prices == [0.96]
    assert "SHALLOTS" in items[0]["name"]
    names = [i["name"].lower() for i in items]
    assert not any(
        "lb / lb" in n or n.strip() in {"lb", "/ lb"} for n in names
    )


def test_deli_rate_plus_total_still_emits_the_extended_price():
    words = [
        w(1, 1, "GROUND", 0.05, 0.50),
        w(1, 2, "BEEF", 0.22, 0.50),
        w(1, 3, "$4.99", 0.40, 0.50),
        w(1, 4, "per", 0.52, 0.50),
        w(1, 5, "lb", 0.62, 0.50),
        w(2, 1, "7.48", 0.80, 0.50),
    ]
    items, _ = extract_items(words, {1, 2}, summary={"subtotal": 7.48})
    assert [i["price"] for i in items] == [7.48]
    assert "BEEF" in items[0]["name"]


def test_items_tail_skips_bogo_and_sale_price_to_match():
    """795: after dropping ``$2.99 /``, extend over garlic + You-Pay + bag.

    ITEMS: romaine 3.29, broccoli 8.49, shallots 0.96, penne 3.99, phantom
    ``$2.99 /``. Outside: garlic 2.99, unpriced BOGO, Sale Price -2.00,
    You-Pay 1.99 with a Price-column 3.99 echo, bag 0.10. Summary 21.81
    = 3.29+2.99+8.49+0.96+5.98+0.10. Do not count both 3.99 and 1.99 for
    the second penne.
    """
    words = [
        w(1, 1, "ROMAINE", 0.05, 0.10),
        w(1, 2, "3.29", 0.80, 0.10),
        w(2, 1, "BROCCOLI", 0.05, 0.16),
        w(2, 2, "8.49", 0.80, 0.16),
        w(3, 1, "SHALLOTS", 0.05, 0.22),
        w(3, 2, "0.57", 0.28, 0.22),
        w(3, 3, "lb", 0.38, 0.22),
        w(3, 4, "@", 0.46, 0.22),
        w(3, 5, "$1.69", 0.54, 0.22),
        w(3, 6, "0.96", 0.80, 0.22),
        w(4, 1, "PENNE", 0.05, 0.28),
        w(4, 2, "3.99", 0.80, 0.28),
        w(5, 1, "lb", 0.05, 0.34),
        w(5, 2, "$2.99", 0.20, 0.34),
        w(5, 3, "/", 0.36, 0.34),
        w(5, 4, "lb", 0.44, 0.34),
        w(6, 1, "GARLIC", 0.05, 0.40),
        w(6, 2, "2.99", 0.80, 0.40),
        w(7, 1, "BOGO", 0.05, 0.46),
        w(7, 2, "50%", 0.18, 0.46),
        w(7, 3, "OFF", 0.32, 0.46),
        w(7, 4, "GROC", 0.46, 0.46),
        w(8, 1, "PENNE", 0.05, 0.52),
        w(8, 2, "Sale", 0.22, 0.52),
        w(8, 3, "Price", 0.36, 0.52),
        w(8, 4, "-2.00", 0.80, 0.52),
        w(9, 1, "3.99", 0.72, 0.58, 0.012),
        w(9, 2, "1.99", 0.85, 0.58, 0.012),
        w(10, 1, "PAPER", 0.05, 0.64),
        w(10, 2, "BAG", 0.22, 0.64),
        w(10, 3, "0.10", 0.80, 0.64),
    ]
    items_ids = {1, 2, 3, 4, 5}
    summary = {"subtotal": 21.81, "tax": None, "grand_total": None}
    before = evaluate_items_zone(words, summary, items_ids)
    assert 2.99 not in [
        i["price"] for i in extract_items(words, items_ids, summary=summary)[0]
    ]
    assert before["status"] == "mismatch"

    proposal = propose_items_boundary_extension(
        words=words,
        summary=summary,
        current_line_ids=items_ids,
        sections=[_items_section(sorted(items_ids))],
        rows=[_row(i, 0.10 + (i - 1) * 0.06) for i in range(1, 11)],
        current_row_ids=sorted(items_ids),
    )
    assert proposal is not None
    added = set(proposal["added_line_ids"])
    assert 6 in added  # garlic
    assert 9 in added  # You-Pay 1.99
    assert 10 in added  # bag
    assert 7 not in added  # BOGO annotation
    assert 8 not in added  # Sale Price stays non-item
    assert proposal["after"]["status"] == "match"
    assert proposal["after"]["items_sum"] == 21.81
    extended = set(proposal["line_ids"])
    items, _ = extract_items(words, extended, summary=summary)
    prices = [i["price"] for i in items]
    assert prices.count(3.99) == 1
    assert 1.99 in prices
    assert -2.00 not in prices
    assert 2.99 in prices
    assert 0.10 in prices

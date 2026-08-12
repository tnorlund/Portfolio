"""Printed-total constraint: count ITEMS discounts / drop extra bands.

No merchant vocabulary. A change ships only when the same arithmetic
guard as ITEMS-boundary repair accepts it (strictly smaller |delta|
AND a better reconciliation status).
"""

from receipt_upload.line_items.geometry import (
    constrain_items_to_baseline,
    reconcile_extracted_items,
)


def _item(price, name="MILK", is_discount=False, line_ids=None):
    return {
        "name": name,
        "price": price,
        "is_discount": is_discount,
        "line_ids": line_ids or [1],
    }


def test_bogo_discount_counts_when_it_closes_the_delta():
    # Two full-price pennesi plus the printed BOGO -$2. Excluding the
    # discount over-counts by exactly $2; including it matches.
    items = [
        _item(3.99, "PENNE"),
        _item(3.99, "PENNE"),
        _item(-2.00, "BOGO 50% OFF GROC", is_discount=True),
    ]
    summary = {"subtotal": 5.98}
    constrained = constrain_items_to_baseline(items, summary)
    assert [i["price"] for i in constrained] == [3.99, 3.99, -2.00]
    recon = reconcile_extracted_items(constrained, summary)
    assert recon.status == "match"
    assert recon.item_sum == 5.98


def test_already_matching_receipt_is_untouched():
    items = [_item(4.99, "EGGS"), _item(3.99, "MILK")]
    summary = {"subtotal": 8.98}
    assert constrain_items_to_baseline(items, summary) == items
    assert reconcile_extracted_items(items, summary).status == "match"


def test_phantom_unit_price_drops_when_it_is_the_overage():
    # 2 @ 8.99 decoded as $17.98 AND the unit price $8.99.
    items = [_item(17.98, "ORG CHICKEN SAUSAGE"), _item(8.99, "2")]
    summary = {"subtotal": 17.98}
    constrained = constrain_items_to_baseline(items, summary)
    assert [i["price"] for i in constrained] == [17.98]
    assert reconcile_extracted_items(constrained, summary).status == "match"


def test_under_count_does_not_drop_real_items():
    items = [_item(7.99, "MILK"), _item(3.99, "EGGS")]
    summary = {"subtotal": 17.97}
    constrained = constrain_items_to_baseline(items, summary)
    assert [i["price"] for i in constrained] == [7.99, 3.99]
    assert reconcile_extracted_items(constrained, summary).status == "mismatch"


def test_discount_not_counted_when_it_would_overshoot():
    # Sale prices already net; a leftover coupon line must not be
    # subtracted just because it exists.
    items = [
        _item(5.00, "APPLES"),
        _item(5.00, "BANANAS"),
        _item(-1.00, "COUPON", is_discount=True),
    ]
    summary = {"subtotal": 10.00}
    constrained = constrain_items_to_baseline(items, summary)
    recon = reconcile_extracted_items(constrained, summary)
    assert recon.status == "match"
    assert recon.item_sum == 10.00


def test_prefers_dropping_unnamed_band_when_prices_tie():
    items = [_item(5.00, "MILK"), _item(5.00, "")]
    summary = {"subtotal": 5.00}
    constrained = constrain_items_to_baseline(items, summary)
    assert [i["name"] for i in constrained] == ["MILK"]


def test_named_item_at_the_overage_survives():
    # Coffee coincidentally costs the tax amount. Dropping it would
    # match the subtotal and be wrong.
    items = [
        _item(2.33, "COFFEE"),
        _item(5.00, "BAGEL"),
        _item(3.00, "MUFFIN"),
    ]
    summary = {"subtotal": 8.00, "grand_total": 10.33, "tax": 2.33}
    constrained = constrain_items_to_baseline(items, summary)
    assert sorted(i["price"] for i in constrained) == [2.33, 3.0, 5.0]


def test_no_summary_is_a_no_op():
    items = [_item(1.00), _item(2.00)]
    assert constrain_items_to_baseline(items, None) is items


def test_prefers_later_unnamed_when_drop_sets_share_max_index():
    # Three identical unnamed $1 bands; dropping any two matches.
    # Later-band preference must keep the earliest (bottom-first index
    # 0), not the middle: without a full descending-index key, (0, 2)
    # sorts before (1, 2).
    items = [
        _item(1.00, "", line_ids=[1]),
        _item(1.00, "", line_ids=[2]),
        _item(1.00, "", line_ids=[3]),
        _item(5.00, "MILK", line_ids=[4]),
    ]
    summary = {"subtotal": 6.00}
    constrained = constrain_items_to_baseline(items, summary)
    assert [i["line_ids"] for i in constrained] == [[1], [4]]


def test_candidate_cap_searches_bottommost_unnamed():
    # decode_band_blocks emits bottom-first, so index 0 is the foot.
    # Nine unnamed bands; the phantom total is the bottommost. The
    # window must be the first 8 droppables, not the last 8.
    items = [_item(8.99, "")]
    items.append(_item(17.98, "ORG CHICKEN SAUSAGE"))
    items.extend(_item(0.0, "") for _ in range(8))
    summary = {"subtotal": 17.98}
    constrained = constrain_items_to_baseline(items, summary)
    assert [i["price"] for i in constrained] == [17.98] + [0.0] * 8


def test_short_alphanumeric_name_is_not_droppable():
    # 7UP is a real product; dropping it would match the subtotal.
    items = [
        _item(2.00, "7UP"),
        _item(5.00, "MILK"),
        _item(3.00, "EGGS"),
    ]
    summary = {"subtotal": 8.00}
    constrained = constrain_items_to_baseline(items, summary)
    assert [i["name"] for i in constrained] == ["7UP", "MILK", "EGGS"]


def test_discount_subset_counts_only_the_closing_discount():
    items = [
        _item(6.00, "MILK"),
        _item(5.00, "EGGS"),
        _item(-1.00, "COUPON A", is_discount=True),
        _item(-2.00, "COUPON B", is_discount=True),
    ]
    summary = {"subtotal": 10.00}
    recon = reconcile_extracted_items(items, summary)
    assert recon.status == "match"
    assert recon.item_sum == 10.00


def test_discount_rescues_item_sum_induced_no_baseline():
    items = [
        _item(40.00, "STEAK"),
        _item(-30.00, "PROMO", is_discount=True),
    ]
    summary = {"subtotal": 10.00}
    recon = reconcile_extracted_items(items, summary)
    assert recon.status == "match"
    assert recon.item_sum == 10.00


def test_drops_large_unnamed_band_from_no_baseline():
    items = [_item(10.00, "MILK"), _item(100.00, "")]
    summary = {"subtotal": 10.00}
    constrained = constrain_items_to_baseline(items, summary)
    assert [i["price"] for i in constrained] == [10.00]

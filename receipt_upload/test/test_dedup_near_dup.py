"""Tests for transaction-identity near-duplicate detection (re-scans/reprints).

These encode the hard-won lessons: a shared numeric id ALONE is not proof (card
/terminal/loyalty numbers recur), same-total alone is not proof, different
printed time or merchant means different transaction, and glued numeric tokens
must not manufacture phantom ids.
"""

from receipt_upload.dedup.near_dup import (
    find_duplicate,
    frequent_ids,
    same_transaction,
    transaction_fingerprint,
)


def tf(text, total=None, merchant=None):
    return transaction_fingerprint(text.split(), total, merchant)


# --------------------------------------------------------------------------- #
# positive matches (require corroboration)
# --------------------------------------------------------------------------- #
def test_shared_auth_id_plus_content_is_duplicate():
    a = tf("SPROUTS AUTH 337030 MILK 17.99 EGGS 4.50 TOTAL 42.54", 42.54, "Sprouts")
    b = tf("SPROUTS auth 337030 MILK 17.99 EGGS 4.50 total 42.54 xx", 42.54, "Sprouts")
    ok, why = same_transaction(a, b)
    assert ok and "337030" in why


def test_reprint_with_extra_text_still_matches():
    orig = tf("TRADER JOES AUTH 204334 COBB 5.99 SALAD 4.99 TOTAL 29.44", 29.44, "Trader Joes")
    reprint = tf("REPRINT TRADER JOES AUTH 204334 COBB 5.99 SALAD 4.99 TOTAL 29.44 COPY", 29.44, "Trader Joes")
    ok, why = same_transaction(orig, reprint)
    assert ok and "204334" in why


def test_restaurant_check_number_plus_prices():
    a = tf("PASTA SISTERS Check #239 Salame 14.00 Milanese 19.00 Total 52.93", merchant="Pasta Sisters")
    b = tf("Pasta Sisters check 239 Salame 14.00 Milanese 19.00 Total 52.93 reprint", merchant="Pasta Sisters")
    ok, why = same_transaction(a, b)
    assert ok and "239" in why


# --------------------------------------------------------------------------- #
# negative: the failure modes we proved destructive
# --------------------------------------------------------------------------- #
def test_shared_id_alone_is_NOT_duplicate():
    # same card number (>=6 digits) but different total + disjoint items = 2 visits
    a = tf("STORE CARD 412345 MILK 3.99 TOTAL 3.99", 3.99, "Store")
    b = tf("STORE CARD 412345 BREAD 2.50 TOTAL 2.50", 2.50, "Store")
    ok, _ = same_transaction(a, b)
    assert not ok


def test_same_total_alone_is_NOT_duplicate():
    # identical total, no shared id, disjoint items -> NOT a duplicate
    a = tf("STORE WIDGET 10.00 TOTAL 10.00", 10.00, "Store A")
    b = tf("STORE GADGET 10.00 TOTAL 10.00", 10.00, "Store B")
    ok, _ = same_transaction(a, b)
    assert not ok


def test_disjoint_printed_times_rejected():
    # same merchant + same total + shared id, but printed 10 min apart = 2 orders
    a = tf("EASTWOOD AUTH 591311 PLATE 9.20 TOTAL 9.20 20:06", 9.20, "Eastwood")
    b = tf("EASTWOOD AUTH 591311 PLATE 9.20 TOTAL 9.20 20:16", 9.20, "Eastwood")
    ok, why = same_transaction(a, b)
    assert not ok and "time" in why.lower()


def test_different_merchant_rejected():
    a = tf("AUTH 998877 ITEM 5.00 TOTAL 5.00", 5.00, "Sprouts")
    b = tf("AUTH 998877 ITEM 5.00 TOTAL 5.00", 5.00, "Vons")
    ok, why = same_transaction(a, b)
    assert not ok and "merchant" in why.lower()


def test_denylist_excludes_recurring_id():
    # a shared id that recurs across the corpus (card/terminal code) is ignored
    a = tf("STORE 414010 MILK 3.99 TOTAL 3.99", 3.99, "Store")
    b = tf("STORE 414010 MILK 3.99 TOTAL 3.99", 3.99, "Store")
    assert same_transaction(a, b)[0]                      # matches without denylist
    assert not same_transaction(a, b, denylist={"414010"})[0]  # excluded -> no match


def test_no_phantom_id_from_glued_numbers():
    # two different visits sharing only a printed phone number (split tokens):
    # the words "805 495 0110" must NOT be glued into a fake id "8054950110".
    a = tf("STORE A 805 495 0110 MILK 3.99 TOTAL 3.99", 3.99, "Store A")
    b = tf("STORE B 805 495 0110 BREAD 2.50 TOTAL 2.50", 2.50, "Store B")
    assert not a.strong_ids and not b.strong_ids   # no contiguous >=6-digit run
    assert not same_transaction(a, b)[0]


# --------------------------------------------------------------------------- #
# helpers + find_duplicate
# --------------------------------------------------------------------------- #
def test_frequent_ids_flags_recurring():
    fps = [tf("X 111111 1.00"), tf("Y 111111 2.00"), tf("Z 111111 3.00"),
           tf("W 111111 4.00"), tf("U 222222 5.00")]
    freq = frequent_ids(fps, max_count=3)
    assert "111111" in freq and "222222" not in freq


def test_find_duplicate_among_candidates():
    new = "TJ AUTH 204334 COBB 5.99 TOTAL 29.44".split()
    cands = [("other", "STARBUCKS AUTH 999111 LATTE 5.50 TOTAL 5.50".split()),
             ("dup", "REPRINT TJ AUTH 204334 COBB 5.99 TOTAL 29.44".split())]
    hit = find_duplicate(new, cands, new_total=29.44, new_merchant="TJ",
                         candidate_totals={"other": 5.50, "dup": 29.44},
                         candidate_merchants={"other": "Starbucks", "dup": "TJ"})
    assert hit is not None and hit[0] == "dup"


def test_find_duplicate_none_when_unique():
    new = "TJ AUTH 555555 SALAD 7.00 TOTAL 7.00".split()
    cands = [("x", "WHOLE FOODS AUTH 111222 KALE 3.00 TOTAL 3.00".split())]
    assert find_duplicate(new, cands, new_total=7.00,
                          candidate_totals={"x": 3.00}) is None


def test_empty_fingerprint_never_matches():
    assert find_duplicate(["the", "and"], [("x", ["the", "and"])]) is None

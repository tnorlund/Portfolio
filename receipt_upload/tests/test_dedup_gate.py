"""Tests for stage5_plan.gate_groups — pairwise transaction-identity gating."""

from receipt_upload.dedup.stage5_plan import gate_groups


def _words(text):
    # build the words_by inner dict: {(line, word): token}
    return {(0, i): t for i, t in enumerate(text.split())}


def _setup(specs):
    """specs: {key: (text, total, merchant)} -> (rec, words_by, totals, merchants)."""
    rec, words_by, totals, merchants = {}, {}, {}, {}
    for k, (text, total, merch) in specs.items():
        rec[k] = object()
        words_by[k] = _words(text)
        totals[k] = total
        merchants[k] = merch
    return rec, words_by, totals, merchants


def test_clean_pair_passes():
    rec, w, t, m = _setup(
        {
            ("a", 1): (
                "SPROUTS AUTH 337030 MILK 17.99 TOTAL 42.54",
                42.54,
                "Sprouts",
            ),
            ("b", 1): (
                "SPROUTS auth 337030 MILK 17.99 total 42.54",
                42.54,
                "Sprouts",
            ),
        }
    )
    kept, rejected = gate_groups([[["a", 1], ["b", 1]]], rec, w, t, m)
    assert len(kept) == 1 and not rejected


def test_one_bad_member_rejects_whole_group_pairwise():
    # member c is a DIFFERENT merchant — pairwise gating must reject the group,
    # even though a~b are a real match (star-shaped gating against 'a' would miss it).
    rec, w, t, m = _setup(
        {
            ("a", 1): (
                "SPROUTS AUTH 337030 MILK 17.99 TOTAL 42.54",
                42.54,
                "Sprouts",
            ),
            ("b", 1): (
                "SPROUTS AUTH 337030 MILK 17.99 TOTAL 42.54",
                42.54,
                "Sprouts",
            ),
            ("c", 1): ("VONS AUTH 999000 BREAD 2.50 TOTAL 2.50", 2.50, "Vons"),
        }
    )
    kept, rejected = gate_groups(
        [[["a", 1], ["b", 1], ["c", 1]]], rec, w, t, m
    )
    assert not kept and len(rejected) == 1


def test_group_too_large_rejected():
    specs = {
        (chr(97 + i), 1): ("M AUTH 337030 X 1.00 TOTAL 1.00", 1.00, "M")
        for i in range(8)
    }
    rec, w, t, m = _setup(specs)
    group = [[k[0], k[1]] for k in specs]
    kept, rejected = gate_groups([group], rec, w, t, m)
    assert not kept and "too large" in rejected[0]["reasons"][0]["why"]


def test_recurring_id_denylisted_so_pair_fails():
    # the only shared id recurs across MANY receipts (a card/terminal code) -> the
    # frequency guard denylists it, leaving no corroborated match.
    specs = {
        (chr(97 + i), 1): (
            f"M 414010 ITEM {i}.99 TOTAL {i}.99",
            float(f"{i}.99"),
            "M",
        )
        for i in range(5)
    }
    rec, w, t, m = _setup(specs)
    # gate the first two as a candidate pair; 414010 is on all 5 -> denylisted
    kept, rejected = gate_groups([[["a", 1], ["b", 1]]], rec, w, t, m)
    assert not kept and len(rejected) == 1

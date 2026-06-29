#!/usr/bin/env python3
"""Tiny unit test for realism_scorecard on hand-built candidates.

Run: python synthesis_loop/test_realism_scorecard.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import realism_scorecard as rs  # noqa: E402


def _clean_candidate():
    """A tidy receipt: aligned price column, no garble, item count matches,
    no vertical collisions."""
    # rows top->bottom (remember: higher y = top)
    return {
        "candidate_id": "clean",
        "tokens": [
            "STORE", "MILK", "4.99", "EGGS", "3.49",
            "NUMBER", "OF", "ITEMS", "SOLD", "=", "2",
        ],
        "bboxes": [
            [100, 960, 300, 980],   # STORE  header
            [100, 900, 200, 920],   # MILK
            [400, 900, 460, 920],   # 4.99   (LINE_TOTAL, x1=460)
            [100, 870, 200, 890],   # EGGS
            [400, 870, 461, 890],   # 3.49   (LINE_TOTAL, x1=461)
            [100, 820, 160, 840], [165, 820, 195, 840], [200, 820, 260, 840],
            [265, 820, 320, 840], [325, 820, 335, 840], [340, 820, 360, 840],
        ],
        "ner_tags": [
            "B-MERCHANT_NAME", "B-PRODUCT_NAME", "B-LINE_TOTAL",
            "B-PRODUCT_NAME", "B-LINE_TOTAL",
            "O", "O", "O", "O", "O", "O",
        ],
    }


def _bad_candidate():
    """Garbled token, misaligned price column, wrong item count, vertical
    collision between two same-column tokens."""
    return {
        "candidate_id": "bad",
        "tokens": [
            "STORE", "MILK", "4.99", "EGGS", "3.49",
            "NUMBER", "OF", "ITEMS", "SOLD", "=", "5",
            "gar::ble",
        ],
        "bboxes": [
            [100, 960, 300, 980],
            [100, 900, 200, 920],
            [400, 900, 460, 920],   # x1=460
            [100, 870, 200, 890],
            [250, 870, 330, 890],   # x1=330  -> column NOT aligned
            [100, 820, 160, 840], [165, 820, 195, 840], [200, 820, 260, 840],
            [265, 820, 320, 840], [325, 820, 335, 840], [340, 820, 360, 840],
            [400, 905, 460, 925],   # overlaps the 4.99 box (same column, y-overlap)
        ],
        "ner_tags": [
            "B-MERCHANT_NAME", "B-PRODUCT_NAME", "B-LINE_TOTAL",
            "B-PRODUCT_NAME", "B-LINE_TOTAL",
            "O", "O", "O", "O", "O", "O", "O",
        ],
    }


def test_clean():
    m = rs.score_candidates([_clean_candidate()])
    assert m["garbled_token_rate"] == 0.0, m["garbled_token_rate"]
    assert m["price_decimal_x_stddev"] < 1.0, m["price_decimal_x_stddev"]
    assert m["item_count_consistency"] == 1.0, m["item_count_consistency"]
    assert m["vertical_overlap_rate"] == 0.0, m["vertical_overlap_rate"]
    assert m["composite"] is not None and 0.0 <= m["composite"] <= 1.0


def test_bad():
    m = rs.score_candidates([_bad_candidate()])
    assert m["garbled_token_rate"] > 0.0, m["garbled_token_rate"]          # gar::ble caught
    assert m["price_decimal_x_stddev"] > 50.0, m["price_decimal_x_stddev"]  # 460 vs 330
    assert m["item_count_consistency"] == 0.0, m["item_count_consistency"]  # printed 5 != 2 rows
    assert m["vertical_overlap_rate"] == 1.0, m["vertical_overlap_rate"]    # collision present


def test_clean_beats_bad():
    good = rs.score_candidates([_clean_candidate()])["composite"]
    bad = rs.score_candidates([_bad_candidate()])["composite"]
    assert good > bad, (good, bad)


if __name__ == "__main__":
    test_clean()
    test_bad()
    test_clean_beats_bad()
    print("OK - all realism_scorecard tests passed")

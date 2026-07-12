"""Unit + simulation tests for the section-restoration remapper.

Pure Python: no Docker, no AWS, no DynamoDB. The matcher/remapper operate on
in-memory ``(line_id, text)`` sequences and packet dicts, and the simulation
harness perturbs real offline packets the way a re-OCR would.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import remap_sections as rs

# --------------------------------------------------------------------------- #
# similarity / levenshtein                                                     #
# --------------------------------------------------------------------------- #


def test_levenshtein_basic():
    assert rs.levenshtein("kitten", "sitting") == 3
    assert rs.levenshtein("", "abc") == 3
    assert rs.levenshtein("abc", "abc") == 0


def test_similarity_identical_and_case_insensitive():
    assert rs.similarity("TOTAL", "total") == 1.0
    assert rs.similarity("  a   b ", "a b") == 1.0  # whitespace collapse
    assert rs.similarity("", "") == 1.0


def test_similarity_ocr_noise_is_high():
    # a couple of char substitutions on a long-ish line stay well above 0.5
    assert rs.similarity("Las Vegas, NV 89148", "La5 Vega5, NV 89148") > 0.7


def test_similarity_unrelated_is_low():
    assert rs.similarity("TOTAL 12.99", "Thank you for shopping") < 0.5


# --------------------------------------------------------------------------- #
# matcher: alignment edge cases                                                #
# --------------------------------------------------------------------------- #


def _ids(blocks):
    return [(b.old_ids, b.new_ids, b.kind) for b in blocks]


def test_align_pure_renumber_is_all_1to1():
    old = [(1, "ARCO GASOLINE"), (2, "9390 West Russell Rd"), (3, "Las Vegas")]
    new = [(50, "ARCO GASOLINE"), (51, "9390 West Russell Rd"), (52, "Las Vegas")]
    blocks = rs.align_lines(old, new)
    matches = rs.old_line_matches(blocks)
    assert matches[1].new_ids == [50]
    assert matches[2].new_ids == [51]
    assert matches[3].new_ids == [52]
    assert all(b.kind == "1:1" for b in blocks)
    assert all(m.confidence > 0.99 for m in matches.values())


def test_align_merge_two_old_into_one_new():
    old = [(1, "Las Vegas,"), (2, "NV 89148"), (3, "TOTAL 9.99")]
    new = [(10, "Las Vegas, NV 89148"), (11, "TOTAL 9.99")]
    blocks = rs.align_lines(old, new)
    matches = rs.old_line_matches(blocks)
    # both old lines map to the single merged new line
    assert matches[1].new_ids == [10]
    assert matches[2].new_ids == [10]
    assert matches[1].kind == "N:1"
    assert matches[3].new_ids == [11]


def test_align_split_one_old_into_two_new():
    old = [(1, "Las Vegas NV 89148"), (2, "TOTAL 9.99")]
    new = [(10, "Las Vegas"), (11, "NV 89148"), (12, "TOTAL 9.99")]
    blocks = rs.align_lines(old, new)
    matches = rs.old_line_matches(blocks)
    assert matches[1].new_ids == [10, 11]
    assert matches[1].kind == "1:N"
    assert matches[2].new_ids == [12]


def test_align_unmatched_old_line_is_dropped():
    # an old line with no counterpart in new -> unmatched (empty new_ids)
    old = [(1, "HEADER LINE"), (2, "DELETED PROMO XYZZY"), (3, "TOTAL 5.00")]
    new = [(10, "HEADER LINE"), (11, "TOTAL 5.00")]
    blocks = rs.align_lines(old, new)
    matches = rs.old_line_matches(blocks)
    assert matches[1].new_ids == [10]
    assert matches[3].new_ids == [11]
    assert matches[2].new_ids == []  # dropped
    assert matches[2].confidence == 0.0
    assert matches[2].kind == "unmatched"


def test_align_inserted_new_line_has_no_old():
    old = [(1, "HEADER"), (2, "TOTAL 5.00")]
    new = [(10, "HEADER"), (11, "BRAND NEW PROMO"), (12, "TOTAL 5.00")]
    blocks = rs.align_lines(old, new)
    kinds = [b.kind for b in blocks]
    assert "inserted" in kinds
    matches = rs.old_line_matches(blocks)
    assert matches[1].new_ids == [10]
    assert matches[2].new_ids == [12]


def test_align_empty_new_lines_all_unmatched():
    old = [(1, "A"), (2, "B")]
    blocks = rs.align_lines(old, [])
    matches = rs.old_line_matches(blocks)
    assert matches[1].new_ids == []
    assert matches[2].new_ids == []


def test_align_preserves_reading_order_with_duplicate_text():
    # two identical-text lines must map in order, not cross over
    old = [(1, "SUBTOTAL"), (2, "TOTAL"), (3, "SUBTOTAL")]
    new = [(10, "SUBTOTAL"), (11, "TOTAL"), (12, "SUBTOTAL")]
    matches = rs.old_line_matches(rs.align_lines(old, new))
    assert matches[1].new_ids == [10]
    assert matches[2].new_ids == [11]
    assert matches[3].new_ids == [12]


# --------------------------------------------------------------------------- #
# remapper                                                                     #
# --------------------------------------------------------------------------- #


def _section(stype, ids, conf=0.9, status="VALID", src="section-qa-v2"):
    return rs.SectionInput(
        section_type=stype,
        line_ids=ids,
        confidence=conf,
        model_source=src,
        validation_status=status,
    )


def test_remap_clean_11_keeps_valid_and_tags_source():
    matches = {
        3: rs.LineMatch([30], 1.0, "1:1"),
        4: rs.LineMatch([31], 1.0, "1:1"),
        5: rs.LineMatch([32], 1.0, "1:1"),
    }
    sec = _section("ADDRESS", [3, 4, 5], conf=0.95)
    rm = rs.remap_section(sec, matches, match_threshold=0.5, pending_loss_frac=0.3)
    assert rm.new_line_ids == [30, 31, 32]
    assert rm.validation_status == "VALID"
    assert not rm.downgraded
    assert rm.model_source == "section-qa-v2+remap-v1"
    assert rm.confidence == pytest.approx(0.95)  # * mean(1.0)


def test_remap_confidence_scaled_by_mean_match():
    matches = {
        1: rs.LineMatch([10], 0.8, "1:1"),
        2: rs.LineMatch([11], 0.6, "1:1"),
    }
    sec = _section("TOTAL_LINE", [1, 2], conf=1.0)
    rm = rs.remap_section(sec, matches, 0.5, 0.3)
    assert rm.confidence == pytest.approx(0.7)  # 1.0 * mean(0.8, 0.6)


def test_remap_drops_low_confidence_line_and_logs():
    matches = {
        1: rs.LineMatch([10], 0.95, "1:1"),
        2: rs.LineMatch([11], 0.2, "1:1"),  # below threshold -> dropped
    }
    sec = _section("HEADER", [1, 2])
    rm = rs.remap_section(sec, matches, match_threshold=0.5, pending_loss_frac=0.3)
    assert rm.new_line_ids == [10]
    assert rm.dropped_old_ids == [2]
    # lost 1/2 = 0.5 > 0.3 -> downgraded
    assert rm.validation_status == "PENDING"
    assert rm.downgraded


def test_remap_downgrades_when_loss_exceeds_threshold():
    matches = {i: rs.LineMatch([100 + i], 0.95, "1:1") for i in range(1, 5)}
    matches[4] = rs.LineMatch([], 0.0, "unmatched")  # 1 of 4 lost = 25%
    sec = _section("ITEMS", [1, 2, 3, 4])
    rm = rs.remap_section(sec, matches, 0.5, 0.30)
    # 25% <= 30% -> stays VALID
    assert rm.validation_status == "VALID"
    assert not rm.downgraded
    # now lose 2 of 4 = 50% -> downgrade
    matches[3] = rs.LineMatch([], 0.0, "unmatched")
    rm2 = rs.remap_section(sec, matches, 0.5, 0.30)
    assert rm2.validation_status == "PENDING"
    assert rm2.downgraded


def test_remap_empty_section_returns_none():
    matches = {1: rs.LineMatch([], 0.0, "unmatched")}
    sec = _section("FOOTER", [1])
    assert rs.remap_section(sec, matches, 0.5, 0.3) is None


def test_remap_merge_dedups_new_ids():
    # two old lines both map to the same merged new id
    matches = {1: rs.LineMatch([10], 0.9, "N:1"), 2: rs.LineMatch([10], 0.9, "N:1")}
    sec = _section("ADDRESS", [1, 2])
    rm = rs.remap_section(sec, matches, 0.5, 0.3)
    assert rm.new_line_ids == [10]
    assert rm.validation_status == "VALID"  # no lines dropped


def test_remap_non_valid_status_not_promoted():
    matches = {1: rs.LineMatch([10], 0.9, "1:1"), 2: rs.LineMatch([], 0.0, "unmatched")}
    sec = _section("SURVEY", [1, 2], status="NEEDS_REVIEW")
    rm = rs.remap_section(sec, matches, 0.5, 0.3)
    # 50% lost but original wasn't VALID, so status is preserved, not downgraded
    assert rm.validation_status == "NEEDS_REVIEW"
    assert not rm.downgraded


# --------------------------------------------------------------------------- #
# receipt driver                                                               #
# --------------------------------------------------------------------------- #


def test_remap_receipt_no_new_lines_marks_unmatched():
    packet = {
        "image_id": "img",
        "receipt_id": 1,
        "lines": {"1": "A", "2": "B"},
        "sections": [
            {"section_type": "HEADER", "line_ids": [1, 2], "confidence": 0.9,
             "model_source": "qa", "validation_status": "VALID"}
        ],
    }
    res = rs.remap_receipt(packet, [], 0.5, 0.3)
    assert not res.matched
    assert res.skipped_sections == 1
    assert res.remapped == []


def test_is_local_endpoint():
    assert rs.is_local_endpoint("http://127.0.0.1:8100")
    assert rs.is_local_endpoint("http://localhost:8000")
    assert not rs.is_local_endpoint("https://dynamodb.us-east-1.amazonaws.com")
    assert not rs.is_local_endpoint(None)


# --------------------------------------------------------------------------- #
# simulation harness (accuracy on real offline packets)                        #
# --------------------------------------------------------------------------- #


PACKETS_PATH = rs.DEFAULT_PACKETS


def test_simulate_reocr_truth_covers_all_old_ids():
    import random

    old = [(1, "ARCO GASOLINE"), (2, "9390 West Russell Rd"),
           (3, "Las Vegas NV 89148"), (4, "TOTAL 9.99"), (5, "THANK YOU")]
    sim = rs.simulate_reocr(old, random.Random(0), merge_frac=0.5, split_frac=0.5)
    assert set(sim.truth) == {1, 2, 3, 4, 5}
    # every truth new id actually exists in new_lines
    new_ids = {nid for nid, _ in sim.new_lines}
    for ids in sim.truth.values():
        assert set(ids) <= new_ids


@pytest.mark.skipif(
    not PACKETS_PATH.exists(), reason="offline packets not present in this env"
)
def test_simulation_restoration_accuracy_meets_bar():
    packets = json.loads(PACKETS_PATH.read_text())
    report = rs.run_simulation(
        packets,
        n_receipts=20,
        seed=1234,
        match_threshold=0.5,
        pending_loss_frac=0.30,
    )
    # sanity on the harness
    assert report["receipts_tested"] == 20
    assert report["sections"]["total"] > 0
    # Restoration quality bar: the matcher should recover the large majority of
    # section memberships exactly under realistic re-OCR perturbation.
    assert report["sections"]["membership_accuracy_pct"] >= 80.0
    assert report["line_alignment"]["accuracy_pct"] >= 85.0

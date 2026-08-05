"""Tests for the zone-gap ITEMS boundary extension (failure-mode H).

The discovery pass in scripts/extend_items_zone_gaps.py must only ever
PROPOSE: candidates are unsectioned priced bands adjacent to the ITEMS
zone, gated by the price-column alignment (|x - col_x| < 0.15), the
settlement/arithmetic vocabulary, and the printed-summary-figure veto.
Final acceptance belongs to extend_items_section_impl's arithmetic
guard, exercised end-to-end here with the same stub-client pattern as
tests/test_receipt_mcp_line_item_tools.py.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

# isort: off
# receipt_upload lands in a different import group depending on whether
# the venv has it installed, so the grouping here is not reproducible
# across environments. Pin it (same fence as
# tests/test_evaluate_section_geometry.py).
from scripts.extend_items_zone_gaps import (
    PRICE_COLUMN_TOL,
    discover_candidates,
    items_corridor,
    propose_extension,
    receipt_price_column_x,
    split_groups_from_summaries,
)

# isort: on

VALID_IMAGE_ID = "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8"
OTHER_IMAGE_ID = "9e8d7c6b-5a49-4382-a1b0-c9d8e7f6a5b4"


# ---------------------------------------------------------------------------
# Fixture world: a receipt whose arithmetic is fully known.
#   line 1  APPLES    3.00   (ITEMS)
#   line 2  BANANAS   2.00   (ITEMS)
#   line 3  COFFEE    4.00   (unsectioned, price in column — the zone gap)
#   line 4  BALANCE DUE 9.00 (unsectioned — settlement vocabulary)
#   line 5  SIDECOL  10.00   (unsectioned, price OFF the column)
#   line 6  MYSTERY   9.00   (unsectioned, amount == printed subtotal)
#   line 7  TOTAL     9.72   (SUMMARY section — already claimed)
# summary: subtotal 9.00, tax 0.72, grand_total 9.72
#
# Two names are load-bearing and must not be "tidied":
#   * COFFEE contains "OFF" as a SUBSTRING. It is a product, and the
#     zone-gap tests below absorb it into an exact match — so if discount
#     detection ever regresses to a substring scan (it did: "OFF" matched
#     inside COFFEE / TOFFEE / Office and flagged 15 real prod items as
#     discounts), COFFEE is dropped from the arithmetic and
#     test_discovered_extension_passes_the_arithmetic_guard goes red.
#   * SIDECOL carries NO guard vocabulary at all, so the off-column veto
#     test can only pass on the price-column gate it claims to test. It
#     was previously named OFFCOL, which the substring bug classified as
#     a discount — that silently removed it from every sum and made
#     test_guard_refuses_a_forced_bad_extension pass for the wrong
#     reason.
# ---------------------------------------------------------------------------


def _w(line_id, word_id, text, x, y):
    return {
        "line_id": line_id,
        "word_id": word_id,
        "text": text,
        "x": x,
        "y_mid": y,
        "h": 0.02,
    }


def _world_words():
    return [
        _w(1, 1, "APPLES", 0.05, 0.10),
        _w(1, 2, "3.00", 0.80, 0.10),
        _w(2, 1, "BANANAS", 0.05, 0.15),
        _w(2, 2, "2.00", 0.80, 0.15),
        _w(3, 1, "COFFEE", 0.05, 0.20),
        _w(3, 2, "4.00", 0.80, 0.20),
        _w(4, 1, "BALANCE", 0.05, 0.25),
        _w(4, 2, "DUE", 0.30, 0.25),
        _w(4, 3, "9.00", 0.80, 0.25),
        _w(5, 1, "SIDECOL", 0.05, 0.30),
        _w(5, 2, "10.00", 0.40, 0.30),
        _w(6, 1, "MYSTERY", 0.05, 0.35),
        _w(6, 2, "9.00", 0.80, 0.35),
        _w(7, 1, "TOTAL", 0.05, 0.45),
        _w(7, 2, "9.72", 0.80, 0.45),
    ]


def _world_sections():
    return [
        {"section_type": "ITEMS", "line_ids": [1, 2]},
        {"section_type": "SUMMARY", "line_ids": [7]},
    ]


_SUMMARY = {"subtotal": 9.00, "tax": 0.72, "grand_total": 9.72}
_ROWS = [SimpleNamespace(price_column_x=0.80)]


# ---------------------------------------------------------------------------
# price column
# ---------------------------------------------------------------------------


def test_price_column_prefers_receipt_row_convention():
    rows = [
        SimpleNamespace(price_column_x=0.78),
        SimpleNamespace(price_column_x=0.82),
        SimpleNamespace(price_column_x=None),
    ]
    assert receipt_price_column_x(rows, _world_words(), {1, 2}) == 0.80


def test_price_column_falls_back_to_items_zone_amounts():
    # No ReceiptRow carries a column: median x of ITEMS-zone amount words.
    assert receipt_price_column_x([], _world_words(), {1, 2}) == 0.80


def test_price_column_none_without_any_signal():
    words = [_w(1, 1, "APPLES", 0.05, 0.10)]  # no amounts anywhere
    assert receipt_price_column_x([], words, {1}) is None


# ---------------------------------------------------------------------------
# corridor
# ---------------------------------------------------------------------------


def test_corridor_bounded_by_adjacent_sections():
    words = _world_words() + [
        _w(9, 1, "STORE", 0.05, 0.02),
        _w(9, 2, "NAME", 0.30, 0.02),
    ]
    sections = _world_sections() + [
        {"section_type": "STOREFRONT", "line_ids": [9]}
    ]
    lo, hi = items_corridor(words, sections)
    assert lo == 0.02  # header span bounds the top side
    assert hi == 0.45  # summary span bounds the bottom side


def test_corridor_none_without_items_section():
    assert (
        items_corridor(_world_words(), [{"section_type": "SUMMARY"}]) is None
    )


def test_corridor_excludes_bands_beyond_another_section():
    # A priced band ABOVE the STOREFRONT header would have to jump over
    # it to reach ITEMS — never a zone gap.
    words = _world_words() + [
        _w(8, 1, "GHOST", 0.05, 0.01),
        _w(8, 2, "4.00", 0.80, 0.01),
        _w(9, 1, "STOREHEADER", 0.05, 0.05),
    ]
    sections = _world_sections() + [
        {"section_type": "STOREFRONT", "line_ids": [9]}
    ]
    cands = discover_candidates(words, sections, _SUMMARY, _ROWS, n_items=2)
    assert all(8 not in c["lids"] for c in cands)


# ---------------------------------------------------------------------------
# discovery vetoes
# ---------------------------------------------------------------------------


def test_discovery_finds_the_zone_gap_line_and_only_it():
    cands = discover_candidates(
        _world_words(), _world_sections(), _SUMMARY, _ROWS, n_items=2
    )
    assert [c["lids"] for c in cands] == [[3]]
    assert cands[0]["price"] == 4.00
    assert "COFFEE" in cands[0]["text"]


def test_discovery_vetoes_settlement_bands():
    cands = discover_candidates(
        _world_words(), _world_sections(), _SUMMARY, _ROWS, n_items=2
    )
    assert all(4 not in c["lids"] for c in cands)  # BALANCE DUE


def test_discovery_vetoes_off_column_amounts():
    # SIDECOL's price sits at x=0.40 vs column 0.80 — outside the
    # load-bearing |x - col_x| < 0.15 gate. The name deliberately carries
    # no settlement/discount vocabulary, so the column gate is the ONLY
    # thing that can be doing the vetoing here.
    assert abs(0.40 - 0.80) >= PRICE_COLUMN_TOL
    cands = discover_candidates(
        _world_words(), _world_sections(), _SUMMARY, _ROWS, n_items=2
    )
    assert all(5 not in c["lids"] for c in cands)


def test_discovery_vetoes_printed_summary_figures_with_two_items():
    cands = discover_candidates(
        _world_words(), _world_sections(), _SUMMARY, _ROWS, n_items=2
    )
    assert all(6 not in c["lids"] for c in cands)  # MYSTERY 9.00


def test_summary_figure_allowed_on_single_item_receipts():
    # On a 1-item receipt the item legitimately equals the total.
    cands = discover_candidates(
        _world_words(), _world_sections(), _SUMMARY, _ROWS, n_items=1
    )
    assert [3] in [c["lids"] for c in cands]
    assert [6] in [c["lids"] for c in cands]


def test_discovery_ignores_lines_claimed_by_other_sections():
    cands = discover_candidates(
        _world_words(), _world_sections(), _SUMMARY, _ROWS, n_items=2
    )
    assert all(7 not in c["lids"] for c in cands)  # SUMMARY-claimed


def test_discovery_fails_closed_without_a_price_column():
    words = [
        w
        for w in _world_words()
        if not (w["line_id"] in (1, 2) and w["word_id"] == 2)
    ]
    # ITEMS zone carries no amounts and no ReceiptRow has a column.
    cands = discover_candidates(
        words, _world_sections(), _SUMMARY, [], n_items=2
    )
    assert cands == []


def test_discovery_orders_candidates_nearest_first():
    cands = discover_candidates(
        _world_words(), _world_sections(), _SUMMARY, _ROWS, n_items=1
    )
    distances = [c["distance"] for c in cands]
    assert distances == sorted(distances)


# ---------------------------------------------------------------------------
# subset proposal
# ---------------------------------------------------------------------------


def _cand(lids, price):
    return {"lids": lids, "price": price, "text": "X", "distance": 0.01}


def test_propose_extension_closes_gap_within_match_tolerance():
    assert propose_extension([_cand([3], 4.00)], 4.00, 9.00) == [3]


def test_propose_extension_accepts_near_band_only_from_mismatch():
    # 3.60 vs gap 4.00 misses match tol (0.09) but lands inside the
    # near tol (1.00), and the current gap is beyond near — a
    # mismatch -> near improvement the guard accepts.
    assert propose_extension([_cand([3], 3.60)], 4.00, 9.00) == [3]
    # A gap already within the near band never takes a near-only fix:
    # near -> near is not a status improvement.
    assert propose_extension([_cand([3], 0.50)], 0.90, 9.00) is None


def test_propose_extension_refuses_overshoot():
    assert propose_extension([_cand([4], 50.00)], 4.00, 9.00) is None


def test_propose_extension_combines_multiple_bands():
    cands = [_cand([3], 2.50), _cand([5], 1.50)]
    assert propose_extension(cands, 4.00, 9.00) == [3, 5]


def test_propose_extension_handles_missing_baseline():
    assert propose_extension([_cand([3], 4.00)], 4.00, None) is None
    assert propose_extension([], 4.00, 9.00) is None


# ---------------------------------------------------------------------------
# split-receipt exclusion (audit caveat: fragments can never reconcile)
# ---------------------------------------------------------------------------


def _summary_row(image_id, receipt_id, merchant, date, total):
    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": merchant,
        "date": date,
        "grand_total": total,
    }


def test_split_groups_flags_same_total_across_image_ids():
    rows = [
        _summary_row(VALID_IMAGE_ID, 1, "Gelson's", "2024-12-16", 39.58),
        _summary_row(OTHER_IMAGE_ID, 1, "GELSON'S", "2024-12-16", 39.58),
        _summary_row(VALID_IMAGE_ID, 2, "Vons", "2024-12-16", 12.00),
    ]
    excluded = split_groups_from_summaries(rows)
    assert (VALID_IMAGE_ID, 1) in excluded
    assert (OTHER_IMAGE_ID, 1) in excluded
    assert (VALID_IMAGE_ID, 2) not in excluded


def test_split_groups_same_image_multiple_receipts_not_flagged():
    # Two receipts on ONE image (a genuine two-receipt photo) are not
    # split fragments.
    rows = [
        _summary_row(VALID_IMAGE_ID, 1, "Vons", "2024-12-16", 12.00),
        _summary_row(VALID_IMAGE_ID, 2, "Vons", "2024-12-16", 12.00),
    ]
    assert split_groups_from_summaries(rows) == set()


def test_split_groups_requires_merchant_date_and_total():
    rows = [
        _summary_row(VALID_IMAGE_ID, 1, "", "2024-12-16", 39.58),
        _summary_row(OTHER_IMAGE_ID, 1, "", "2024-12-16", 39.58),
        _summary_row(VALID_IMAGE_ID, 2, "Vons", "", 12.00),
        _summary_row(OTHER_IMAGE_ID, 2, "Vons", "", 12.00),
        _summary_row(VALID_IMAGE_ID, 3, "Ralphs", "2024-12-16", None),
        _summary_row(OTHER_IMAGE_ID, 3, "Ralphs", "2024-12-16", None),
    ]
    assert split_groups_from_summaries(rows) == set()


# ---------------------------------------------------------------------------
# end-to-end: discovery feeds the extend_items_section arithmetic guard
# (same fake-mcp + stub-client pattern as
# tests/test_receipt_mcp_line_item_tools.py)
# ---------------------------------------------------------------------------


def _install_mcp_stubs():
    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    stdio_mod = types.ModuleType("mcp.server.stdio")
    types_mod = types.ModuleType("mcp.types")

    class _FakeServer:
        def __init__(self, name):
            self.name = name

        def list_tools(self):
            return lambda func: func

        def call_tool(self):
            return lambda func: func

    class _FakeContent:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    server_mod.Server = _FakeServer
    stdio_mod.stdio_server = lambda *a, **k: None
    types_mod.Tool = _FakeContent
    types_mod.TextContent = _FakeContent
    types_mod.ImageContent = _FakeContent
    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.stdio"] = stdio_mod
    sys.modules["mcp.types"] = types_mod


class _StubDynamoClient:
    def __init__(self, sections):
        self.sections = sections
        self.updated_sections = []
        self.updated_summaries = []

    def get_receipt_details(self, image_id, receipt_id):
        words = [
            SimpleNamespace(
                line_id=w["line_id"],
                word_id=w["word_id"],
                text=w["text"],
                bounding_box={
                    "x": w["x"],
                    "y": w["y_mid"] - 0.01,
                    "width": 0.1,
                    "height": 0.02,
                },
            )
            for w in _world_words()
        ]
        return SimpleNamespace(
            lines=[SimpleNamespace(line_id=i) for i in range(1, 8)],
            words=words,
        )

    def get_receipt_sections_from_receipt(self, image_id, receipt_id):
        return self.sections

    def get_receipt_summary(self, image_id, receipt_id):
        from receipt_dynamo.entities.receipt_summary import (
            MonetaryTotals,
            ReceiptSummary,
        )
        from receipt_dynamo.entities.receipt_summary_record import (
            ReceiptSummaryRecord,
        )

        return ReceiptSummaryRecord(
            summary=ReceiptSummary(
                image_id=VALID_IMAGE_ID,
                receipt_id=1,
                merchant_name="Test Mart",
                totals=MonetaryTotals(
                    grand_total=9.72, subtotal=9.00, tax=0.72
                ),
                item_count=3,
            ),
            timestamp_computed="2026-01-01T00:00:00+00:00",
        )

    def update_receipt_section(self, section):
        self.updated_sections.append(section)

    def update_receipt_summary(self, record):
        self.updated_summaries.append(record)


def _entity_sections():
    from receipt_dynamo.entities.receipt_section import ReceiptSection

    def sec(section_type, line_ids):
        return ReceiptSection(
            receipt_id=1,
            image_id=VALID_IMAGE_ID,
            section_type=section_type,
            line_ids=line_ids,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            model_source="section-seed-v0",
            validation_status="VALID",
        )

    return [sec("ITEMS", [1, 2]), sec("SUMMARY", [7])]


def test_discovered_extension_passes_the_arithmetic_guard():
    pytest.importorskip("receipt_dynamo")
    pytest.importorskip("receipt_upload")
    _install_mcp_stubs()
    from scripts.extend_items_zone_gaps import _load_extend_impl

    cands = discover_candidates(
        _world_words(), _world_sections(), _SUMMARY, _ROWS, n_items=2
    )
    added = propose_extension(cands, 4.00, 9.00)
    assert added == [3]

    client = _StubDynamoClient(_entity_sections())
    verdict = asyncio.run(
        _load_extend_impl()(client, VALID_IMAGE_ID, 1, added)
    )
    assert "error" not in verdict
    assert verdict["verified"] is True
    assert verdict["before"]["status"] == "mismatch"
    assert verdict["after"]["status"] == "match"
    assert client.updated_sections == []  # dry run never writes


def test_guard_refuses_a_forced_bad_extension():
    # Lines the discovery vetoes would ALSO be refused by the guard
    # (defense in depth): absorbing SIDECOL (10.00) + MYSTERY (9.00)
    # takes the item sum from 5.00 to 24.00 against a 9.00 baseline, so
    # |delta| grows 4.00 -> 15.00 and the extension must be refused. The
    # sum stays under 3x the baseline, so this exercises the arithmetic
    # guard proper rather than the implausibility escape hatch.
    #
    # The growth is asserted explicitly: an earlier version of this test
    # "passed" only because the fixture row was named OFFCOL and the
    # substring discount bug removed it from the sum entirely, leaving
    # the delta unchanged. Pinning the numbers makes that failure mode
    # visible instead of silent.
    pytest.importorskip("receipt_dynamo")
    pytest.importorskip("receipt_upload")
    _install_mcp_stubs()
    from scripts.extend_items_zone_gaps import _load_extend_impl

    client = _StubDynamoClient(_entity_sections())
    verdict = asyncio.run(
        _load_extend_impl()(client, VALID_IMAGE_ID, 1, [5, 6])
    )
    assert verdict.get("verified") is not True
    assert verdict["before"]["items_sum"] == 5.00
    assert verdict["after"]["items_sum"] == 24.00
    assert verdict["after"]["status"] == "mismatch"
    assert abs(verdict["after"]["delta"]) > abs(verdict["before"]["delta"])
    assert client.updated_sections == []


def test_guard_refuses_absorbing_a_printed_summary_figure():
    # MYSTERY restates the printed 9.00 subtotal. Absorbing it overshoots
    # (5.00 -> 14.00) instead of closing the 4.00 gap, so the guard must
    # refuse it on its own, not only in company with SIDECOL.
    pytest.importorskip("receipt_dynamo")
    pytest.importorskip("receipt_upload")
    _install_mcp_stubs()
    from scripts.extend_items_zone_gaps import _load_extend_impl

    client = _StubDynamoClient(_entity_sections())
    verdict = asyncio.run(_load_extend_impl()(client, VALID_IMAGE_ID, 1, [6]))
    assert verdict.get("verified") is not True
    assert verdict["after"]["items_sum"] == 14.00
    assert abs(verdict["after"]["delta"]) > abs(verdict["before"]["delta"])
    assert client.updated_sections == []


def test_off_substring_name_is_a_product_not_a_discount():
    """Regression pin for the DISCOUNT_WORDS substring bug.

    "OFF" inside COFFEE / TOFFEE / Office made 15 real prod items
    classify as discounts, which excluded them from reconciliation and
    left those receipts permanently unbalanceable. The zone-gap fixture's
    COFFEE row is a product worth 4.00, and absorbing it is what closes
    this receipt's 4.00 gap exactly.
    """
    pytest.importorskip("receipt_upload")
    from receipt_upload.line_items.geometry import extract_items

    items, _ = extract_items(_world_words(), {1, 2, 3}, summary=_SUMMARY)
    by_name = {i["name"]: i for i in items}
    assert "COFFEE" in by_name, by_name
    assert by_name["COFFEE"]["is_discount"] is False
    assert round(sum(i["price"] for i in items), 2) == 9.00

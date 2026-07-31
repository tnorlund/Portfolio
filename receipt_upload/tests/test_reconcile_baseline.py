"""Unit tests for three-figure reconciliation baseline hardening.

Covers the two user-observed defects: receipts dying as no-baseline
despite a printed grand total (grand_total - tax fallback, including a
zero subtotal shadowing a real total), and receipts "passing" against
one figure while the others disagree (baseline_figures_agreeing grade).
Also covers the failure-report fix #3: a broken printed baseline
(subtotal > grand_total, or implausible against the item sum) is
reclassified no-baseline instead of blaming the extractor -- without
ever rerouting an existing match/near verdict.
"""

from receipt_upload.line_items.geometry import (
    ReconcileResult,
    reconcile,
    reconcile_detailed,
)


def items(*prices):
    return [{"price": p} for p in prices]


# --- baseline selection -------------------------------------------------


def test_subtotal_preferred_when_present():
    r = reconcile_detailed(items(5.00, 5.00), {"subtotal": 10.00})
    assert r.status == "match"
    assert r.baseline == 10.00
    assert r.baseline_source == "subtotal"


def test_fallback_to_grand_total_minus_tax():
    r = reconcile_detailed(
        items(5.00, 5.00), {"grand_total": 10.83, "tax": 0.83}
    )
    assert r.status == "match"
    assert r.baseline == 10.00
    assert r.baseline_source == "grand_total_minus_tax"


def test_fallback_tax_defaults_to_zero():
    r = reconcile_detailed(items(4.48), {"grand_total": 4.48})
    assert r.status == "match"
    assert r.baseline == 4.48
    assert r.baseline_source == "grand_total_minus_tax"


def test_zero_subtotal_does_not_shadow_grand_total():
    # Previously a printed $0.00 subtotal was taken as the baseline and
    # the receipt died as no-baseline despite a real grand total.
    r = reconcile_detailed(
        items(5.00, 5.00), {"subtotal": 0, "grand_total": 10.00}
    )
    assert r.status == "match"
    assert r.baseline_source == "grand_total_minus_tax"


def test_no_figures_is_no_baseline():
    assert reconcile_detailed(items(1.00), {}).status == "no-baseline"
    assert reconcile_detailed(items(1.00), None).status == "no-baseline"
    assert (
        reconcile_detailed(items(1.00), {"subtotal": None}).status
        == "no-baseline"
    )


def test_unparseable_figures_are_ignored():
    r = reconcile_detailed(
        items(3.00), {"subtotal": "abc", "grand_total": "3.00"}
    )
    assert r.status == "match"
    assert r.baseline_source == "grand_total_minus_tax"


# --- baseline sanity (failure report fix #3) ----------------------------


def test_subtotal_above_grand_total_mismatch_becomes_no_baseline():
    # Home Depot e1f519d5: subtotal 1696.80 > grand_total 20.47.
    r = reconcile_detailed(
        items(2502.08), {"subtotal": 1696.80, "grand_total": 20.47}
    )
    assert r.status == "no-baseline"


def test_implausible_subtotal_mismatch_becomes_no_baseline():
    # Sprouts 2050f988: printed base 19.97 implausible vs items 90.40.
    r = reconcile_detailed(
        items(90.40), {"subtotal": 19.97, "grand_total": 21.42}
    )
    assert r.status == "no-baseline"


def test_implausible_fallback_baseline_becomes_no_baseline():
    # No subtotal; extracted items overwhelm the printed total (Amazon
    # Fresh b4c2a475: items 961.08 vs printed 11.14 -- OCR dropped
    # digits from the total, not from the items).
    r = reconcile_detailed(items(961.08), {"grand_total": 11.14})
    assert r.status == "no-baseline"


def test_sku_parsed_as_money_subtotal_is_no_baseline():
    # Zen Leaf Las Vegas 5b1ea5d7 r1: the SKU "1A4040300003CF2000271909"
    # OCR-parsed as money, storing subtotal $2,000,271,909.00. An
    # impossible printed figure is no figure at all.
    sku = "1A4040300003CF2000271909"
    bogus_subtotal = float(sku[-10:])  # 2000271909.0, as parsed upstream
    assert bogus_subtotal == 2000271909.0
    r = reconcile_detailed(items(45.00), {"subtotal": bogus_subtotal})
    assert r.status == "no-baseline"
    # A sane grand total alongside still reconciles via the fallback.
    r2 = reconcile_detailed(
        items(45.00),
        {"subtotal": bogus_subtotal, "grand_total": 48.83, "tax": 3.83},
    )
    assert r2.status == "match"
    assert r2.baseline_source == "grand_total_minus_tax"
    # Bound applies to grand_total too.
    r3 = reconcile_detailed(items(45.00), {"grand_total": bogus_subtotal})
    assert r3.status == "no-baseline"


def test_under_extraction_stays_mismatch():
    # Implausibility is one-directional: a baseline far ABOVE the item
    # sum is severe under-extraction (zone gap / zero items), which is
    # the extractor's fault. A sane baseline must keep the hard
    # mismatch (CVS de6ae183: sub 15.59, gt 16.90, zero items).
    r = reconcile_detailed(
        items(), {"subtotal": 15.59, "grand_total": 16.90, "tax": 1.31}
    )
    assert r.status == "mismatch"
    r2 = reconcile_detailed(items(0.10), {"subtotal": 16.89})
    assert r2.status == "mismatch"
    r3 = reconcile_detailed(items(1.50), {"grand_total": 400.00})
    assert r3.status == "mismatch"


def test_broken_subtotal_rescued_by_grand_total():
    # Items match the paid total exactly; only the subtotal digits are
    # OCR-broken. Fall back instead of discarding the baseline.
    r = reconcile_detailed(
        items(17.00), {"subtotal": 51.62, "grand_total": 17.00}
    )
    assert r.status == "match"
    assert r.baseline == 17.00
    assert r.baseline_source == "grand_total_minus_tax"


def test_sane_subtotal_mismatch_stays_mismatch():
    # Baseline is plausible: this is real extractor failure. Golden
    # floors depend on this staying a hard mismatch.
    r = reconcile_detailed(
        items(30.00), {"subtotal": 20.00, "grand_total": 21.50}
    )
    assert r.status == "mismatch"


def test_match_never_rerouted_even_with_insane_figures():
    # Flip guard: an existing match/near verdict is kept even when
    # subtotal > grand_total.
    r = reconcile_detailed(
        items(17.62), {"subtotal": 17.62, "grand_total": 17.00}
    )
    assert r.status == "match"
    assert r.baseline == 17.62


def test_near_never_rerouted():
    r = reconcile_detailed(
        items(1.30), {"subtotal": 0.40, "grand_total": 0.30}
    )
    assert r.status == "near"


# --- graded match (baseline_figures_agreeing) ---------------------------


def test_grade_three_when_all_figures_agree():
    r = reconcile_detailed(
        items(10.00), {"subtotal": 10.00, "tax": 0.83, "grand_total": 10.83}
    )
    assert r.status == "match"
    assert r.baseline_figures_agreeing == 3


def test_grade_two_when_no_tax_but_totals_chain():
    r = reconcile_detailed(
        items(10.00), {"subtotal": 10.00, "grand_total": 10.00}
    )
    assert r.status == "match"
    assert r.baseline_figures_agreeing == 2


def test_grade_one_when_grand_total_disagrees():
    # Items "pass" against the subtotal alone while the printed grand
    # total disagrees -- the second user-observed defect. Status stays
    # match (vocabulary unchanged) but the grade exposes it.
    r = reconcile_detailed(
        items(10.00), {"subtotal": 10.00, "tax": 0.83, "grand_total": 15.00}
    )
    assert r.status == "match"
    assert r.baseline_figures_agreeing == 1


def test_grade_one_single_figure_receipt():
    r = reconcile_detailed(items(10.00), {"subtotal": 10.00})
    assert r.status == "match"
    assert r.baseline_figures_agreeing == 1
    r2 = reconcile_detailed(items(4.48), {"grand_total": 4.48})
    assert r2.baseline_figures_agreeing == 1


def test_grade_two_fallback_with_printed_tax():
    # items + printed tax == grand total: grand and tax corroborate.
    r = reconcile_detailed(items(6.99), {"grand_total": 7.50, "tax": 0.51})
    assert r.status == "match"
    assert r.baseline_figures_agreeing == 2


def test_grade_none_for_hard_failures():
    assert (
        reconcile_detailed(
            items(30.00), {"subtotal": 20.00}
        ).baseline_figures_agreeing
        is None
    )
    assert (
        reconcile_detailed(items(1.00), {}).baseline_figures_agreeing is None
    )


def test_grade_tolerance_matches_match_band():
    # subtotal + tax within max(0.02, 1%) of grand_total still grade 3.
    r = reconcile_detailed(
        items(10.00), {"subtotal": 10.00, "tax": 0.83, "grand_total": 10.85}
    )
    assert r.baseline_figures_agreeing == 3


# --- tuple wrapper compatibility ----------------------------------------


def test_reconcile_wrapper_shape_unchanged():
    assert reconcile(items(5.00), {"subtotal": 5.00}) == ("match", 5.00, 5.00)
    assert reconcile(items(5.00), None) == ("no-baseline", None, None)
    status, item_sum, baseline = reconcile(
        items(2502.08), {"subtotal": 1696.80, "grand_total": 20.47}
    )
    assert (status, item_sum, baseline) == ("no-baseline", None, None)


def test_result_dataclass_defaults():
    r = ReconcileResult("no-baseline", None, None)
    assert r.baseline_source is None
    assert r.baseline_figures_agreeing is None

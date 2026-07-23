"""Unit tests for the full-fidelity metrics over synthetic fixtures (#1188 P1).

Each metric gets a constructed defect it must FAIL and a clean pair it must
PASS -- the in-vitro leg of the validation; the historical-defect table in
REFACTOR_P1P2_RESULT.md is the in-vivo leg.
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (
    os.path.join(REPO, "synthesis_loop"),
    os.path.join(REPO, "scripts"),
    os.path.join(REPO, "receipt_agent"),
    os.path.join(REPO, "tools", "glyph-studio", "py"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import full_fidelity_eval as ffe  # noqa: E402

W, H = 400, 300


def blank():
    return np.full((H, W), 235, dtype=np.uint8)


def stamp(img, l, t, r, b, value=30):
    img[int(t) : int(b), int(l) : int(r)] = value


def word(text, l, t, r, b, labels=()):
    """Pixel-space word dict (the post-words_to_px shape)."""
    return {
        "text": text,
        "labels": list(labels),
        "l": float(l),
        "r": float(r),
        "t": float(t),
        "b": float(b),
        "cy": (t + b) / 2.0,
        "h": float(b - t),
    }


# ---------------------------------------------------------------------------
# columns
# ---------------------------------------------------------------------------
def _amount_rows(right_edges):
    rows = []
    for i, right in enumerate(right_edges):
        y = 40 + i * 24
        rows.append(
            [
                word("ITEM", 12, y, 90, y + 12),
                word("2.99", right - 40, y, right, y + 12),
            ]
        )
    return rows


def _ink_for_rows(img, rows, jitter):
    for i, row in enumerate(rows):
        for w in row:
            dx = jitter[i % len(jitter)] if w is row[-1] else 0
            stamp(img, w["l"] + dx, w["t"], w["r"] + dx, w["b"])


def test_column_metric_fails_on_wobble_and_passes_when_tight():
    rows = _amount_rows([300] * 8)
    cols = ffe.derive_columns_bootstrap(rows, W)
    assert cols and cols[0]["role"] == "amount"
    real = blank()
    _ink_for_rows(real, rows, jitter=[0])
    tight = blank()
    _ink_for_rows(tight, rows, jitter=[0])
    wobbly = blank()
    _ink_for_rows(wobbly, rows, jitter=[-14, 11, -8, 13])
    cell = 8.0
    ok = ffe.metric_columns(real, tight, rows, rows, cols, cell)
    bad = ffe.metric_columns(real, wobbly, rows, rows, cols, cell)
    assert ok["verdict"] == "PASS"
    assert bad["verdict"] == "FAIL"


def test_column_metric_tolerates_scan_tilt():
    # a tilted real scan (lane slides 1px/row = ~4px per 100 y-px, within
    # the real-scan range) is geometry, not wobble and not shear
    rows = _amount_rows([300] * 8)
    cols = ffe.derive_columns_bootstrap(rows, W)
    real = blank()
    for i, row in enumerate(rows):
        for w in row:
            stamp(real, w["l"] + 1.0 * i, w["t"], w["r"] + 1.0 * i, w["b"])
    tight = blank()
    _ink_for_rows(tight, rows, jitter=[0])
    out = ffe.metric_columns(real, tight, rows, rows, cols, 8.0)
    assert out["verdict"] == "PASS"


def _flagged_rows():
    rows = []
    for i in range(8):
        y = 40 + i * 24
        rows.append(
            [
                word("ITEM", 12, y, 90, y + 12),
                word("2.99", 260, y, 300, y + 12),
                word("F", 340, y, 350, y + 12),
            ]
        )
    return rows


def test_column_metric_fails_on_lane_gap_drift():
    # real: flag in its dedicated column (gap 40px); synth: flag glued
    # one cell after the price (gap 12px) -- the F-drift defect
    rows = _flagged_rows()
    cols = ffe.derive_columns_bootstrap(rows, W)
    assert {c["role"] for c in cols} == {"amount", "flag"}
    real = blank()
    _ink_for_rows_flags(real, rows, flag_x=340)
    syn = blank()
    _ink_for_rows_flags(syn, rows, flag_x=312)
    out = ffe.metric_columns(real, syn, rows, rows, cols, 8.0)
    assert out["verdict"] == "FAIL"
    assert any(g["verdict"] == "FAIL" for g in out["lane_gaps"])


def _ink_for_rows_flags(img, rows, flag_x):
    for row in rows:
        for w in row:
            if w["text"] == "F":
                stamp(img, flag_x, w["t"], flag_x + 10, w["b"])
            else:
                stamp(img, w["l"], w["t"], w["r"], w["b"])


def test_flag_column_derived():
    rows = []
    for i in range(6):
        y = 40 + i * 24
        rows.append(
            [
                word("ITEM", 12, y, 90, y + 12),
                word("2.99", 260, y, 300, y + 12),
                word("F", 320, y, 330, y + 12),
            ]
        )
    cols = ffe.derive_columns_bootstrap(rows, W)
    roles = {c["role"] for c in cols}
    assert roles == {"amount", "flag"}


# ---------------------------------------------------------------------------
# style
# ---------------------------------------------------------------------------
def _style_fixture(header_bold_in_syn):
    real = blank()
    syn = blank()
    rows = []
    classes = []
    # two "section_header" rows + four body rows; weight = STROKE width
    # (6px bars for bold, 2px for regular -- box fill is deliberately NOT
    # the signal, matching the real bold-measure contract)
    for i in range(6):
        y = 30 + i * 30
        is_header = i < 2
        row = [
            word("GROCERY" if is_header else "ITEM ROW", 40, y, 240, y + 14)
        ]
        rows.append(row)
        classes.append("section_header" if is_header else "item")
        for img, bold in (
            (real, is_header),
            (syn, is_header and header_bold_in_syn),
        ):
            bar = 6 if bold else 2
            for x in range(40, 240, 10):
                stamp(img, x, y + 2, x + bar, y + 12, 40)
    return real, syn, rows, classes


def test_style_metric_fails_when_headers_unstyled_in_synth():
    real, syn, rows, classes = _style_fixture(header_bold_in_syn=False)
    out = ffe.metric_style(real, syn, rows, rows, classes, classes)
    entry = next(e for e in out["classes"] if e["class"] == "section_header")
    assert entry["verdict"] == "FAIL" and "bold" in entry["missing_style"]
    assert out["verdict"] == "FAIL"


def test_dotted_underline_detected_and_text_rows_rejected():
    img = blank()
    row = [word("GROCERY", 40, 60, 240, 80)]
    # glyph ink + a DOTTED rule 3px under the baseline (dot-matrix style)
    stamp(img, 40, 60, 240, 76)
    for x in range(38, 242, 7):
        stamp(img, x, 82, x + 4, 84)
    assert ffe._row_underlined(img, row)
    # same row followed immediately by a tall digit block: no gap below the
    # candidate rows -> not an underline
    img2 = blank()
    stamp(img2, 40, 60, 240, 76)
    stamp(img2, 40, 82, 240, 110)
    assert not ffe._row_underlined(img2, row)
    # no rule at all
    img3 = blank()
    stamp(img3, 40, 60, 240, 76)
    assert not ffe._row_underlined(img3, row)


def test_style_metric_passes_when_both_styled():
    real, syn, rows, classes = _style_fixture(header_bold_in_syn=True)
    out = ffe.metric_style(real, syn, rows, rows, classes, classes)
    assert out["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------
def _manifest():
    return [
        {
            "text": t,
            "bbox": [50, 900 - i * 40, 300, 920 - i * 40],
            "labels": [],
        }
        for i, t in enumerate(
            ["GELSONS", "TOWNSGATE", "WESTLAKE", "MILK", "2.99"]
        )
    ]


def test_token_text_recall_fails_on_missing_content():
    man = _manifest()
    drawn = [w for w in man if w["text"] not in ("TOWNSGATE", "WESTLAKE")]
    out = ffe.metric_tokens(man, drawn, None, composed=False)
    assert out["verdict"] == "FAIL"
    assert "TOWNSGATE" in out["missing_tokens"]


def test_token_ink_recall_fails_on_erased_region():
    man = _manifest()
    syn = blank()
    # ink for every word EXCEPT the two address tokens (the eraser defect:
    # input words intact, pixels painted over)
    for w in ffe.words_to_px(man, W, H):
        if w["text"] not in ("TOWNSGATE", "WESTLAKE"):
            stamp(syn, w["l"], w["t"], w["r"], w["b"])
    out = ffe.metric_tokens(man, man, syn, composed=False)
    assert out["verdict"] == "FAIL"
    assert out["text_recall"] == 1.0  # only the INK check can see an eraser
    assert "TOWNSGATE" in out["ink_missing_tokens"]


def test_token_metric_passes_when_all_drawn():
    man = _manifest()
    syn = blank()
    for w in ffe.words_to_px(man, W, H):
        stamp(syn, w["l"], w["t"], w["r"], w["b"])
    out = ffe.metric_tokens(man, man, syn, composed=False)
    assert out["verdict"] == "PASS"


def test_token_composed_ignores_numeric_repair():
    man = _manifest() + [
        {"text": ".25", "bbox": [500, 500, 540, 520], "labels": []}
    ]
    drawn = _manifest() + [
        {"text": "1.25", "bbox": [500, 500, 540, 520], "labels": []}
    ]
    out = ffe.metric_tokens(man, drawn, None, composed=True)
    assert out["verdict"] == "PASS"  # amounts are arithmetic's job


# ---------------------------------------------------------------------------
# separators
# ---------------------------------------------------------------------------
def _with_rules(ys):
    img = blank()
    for y in ys:
        # dashed full-width rule: 6px dashes with 3px gaps
        for x in range(int(0.05 * W), int(0.95 * W) - 6, 9):
            stamp(img, x, y, x + 6, y + 3)
    return img


def test_separator_metric_fails_on_missing_rule():
    real = _with_rules([80, 200])
    syn = _with_rules([80])
    out = ffe.metric_separators(real, syn)
    assert out["verdict"] == "FAIL"
    assert len(out["missing_in_synth"]) == 1


def test_separator_metric_passes_on_matching_structure():
    real = _with_rules([80, 200])
    syn = _with_rules([81, 202])
    out = ffe.metric_separators(real, syn)
    assert out["verdict"] == "PASS"
    assert out["real_count"] == 2


def test_separator_detector_ignores_text_rows():
    img = blank()
    # a text-like row: word blobs with wide gaps
    for x in (40, 120, 210, 300):
        stamp(img, x, 100, x + 50, 112)
    assert ffe.detect_separators(img) == []


# ---------------------------------------------------------------------------
# graphics (detector unavailable path only -- the Swift binary is exercised
# in the live validation runs, not unit tests)
# ---------------------------------------------------------------------------
def test_graphics_skips_without_detector(monkeypatch, tmp_path):
    monkeypatch.setattr(ffe, "_BARCODE_BIN", str(tmp_path / "missing-bin"))
    out = ffe.metric_graphics("/nonexistent/a.png", "/nonexistent/b.png")
    assert out["verdict"] == "SKIPPED"


def test_adversarial_qr_to_aztec_swap_fails_graphics(monkeypatch):
    qr = {
        "symbology": "qr",
        "kind": "qr",
        "y_frac": 0.75,
        "x_frac": 0.5,
        "payload": "same-payload",
    }
    aztec = {
        **qr,
        "symbology": "aztec",
    }

    def fake_detect(path):
        return [qr] if path == "real.png" else [aztec]

    monkeypatch.setattr(ffe, "detect_graphics", fake_detect)
    out = ffe.metric_graphics("real.png", "synth.png")

    assert out["verdict"] == "FAIL"
    assert out["matched"] == []
    assert out["missing_in_synth"] == [qr]
    assert out["phantom_in_synth"] == [aztec]


# ---------------------------------------------------------------------------
# logo
# ---------------------------------------------------------------------------
def test_logo_metric_flags_missing_and_offset():
    real = blank()
    stamp(real, 140, 8, 260, 34)  # centered logo blob
    empty = blank()
    out = ffe.metric_logo(real, empty, (0.0, 0.15), expects_logo=True)
    assert out["verdict"] == "FAIL"
    shifted = blank()
    stamp(shifted, 10, 8, 130, 34)  # far-left blob
    out2 = ffe.metric_logo(real, shifted, (0.0, 0.15), expects_logo=True)
    assert out2["verdict"] == "FAIL"
    ok = blank()
    stamp(ok, 143, 9, 255, 33)
    out3 = ffe.metric_logo(real, ok, (0.0, 0.15), expects_logo=True)
    assert out3["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# arithmetic
# ---------------------------------------------------------------------------
def _wordline(text, y, *, right=None, left=None, labels=()):
    """Renderer-format (0-1000, y-up) word on its own line."""
    n = len(text)
    wpx = n * 15
    x0 = (1000 - right * 1000) if right else 0  # unused; kept simple
    x0 = left if left is not None else 40
    return {
        "text": text,
        "bbox": [x0, y, x0 + wpx, y + 22],
        "labels": list(labels),
    }


def _receipt_words(item_price):
    words = []
    y = 900
    for i in range(3):
        words.append(
            _wordline(f"THING{i}", y, left=20, labels=["PRODUCT_NAME"])
        )
        words.append(_wordline("1", y, left=560, labels=["QUANTITY"]))
        words.append(_wordline(item_price, y, left=650, labels=["UNIT_PRICE"]))
        words.append(_wordline(item_price, y, left=800, labels=["LINE_TOTAL"]))
        y -= 40
    words.append(_wordline("SUB TOTAL", y, left=300))
    words.append(_wordline("3.75", y, left=800, labels=["SUBTOTAL"]))
    y -= 40
    words.append(_wordline("SALES TAX", y, left=300))
    words.append(_wordline("0.31", y, left=800, labels=["TAX"]))
    y -= 40
    words.append(_wordline("TOTAL", y, left=300))
    words.append(_wordline("4.06", y, left=800, labels=["GRAND_TOTAL"]))
    y -= 40
    words.append(_wordline("VISA", y, left=300))
    words.append(_wordline("4.06", y, left=800))
    return words


def test_arithmetic_passes_on_consistent_receipt():
    out = ffe.arithmetic_check(_receipt_words("1.25"))
    assert out["verdict"] == "PASS", out
    assert out["violated"] == 0


def test_arithmetic_fails_on_stale_dollar_tree_prices():
    # the 01:25 DT sheet defect: items printed 0.25 while the summary block
    # still says 3.75/0.31/4.06 -- sum(lines) and qty x unit break
    out = ffe.arithmetic_check(_receipt_words("0.25"))
    assert out["verdict"] == "FAIL"
    names = {i["name"] for i in out["identities"] if i["status"] == "VIOLATED"}
    assert "sum_lines_eq_subtotal" in names


def test_arithmetic_untestable_without_amounts():
    words = [_wordline("HELLO", 500, left=100)]
    out = ffe.arithmetic_check(words)
    assert out["verdict"] == "UNTESTED"


def test_arithmetic_ignores_payment_detail_and_sums_split_tenders():
    words = []

    def add_row(y, entries):
        for text, left, labels in entries:
            words.append(_wordline(text, y, left=left, labels=labels))

    add_row(
        900,
        [
            ("ITEM A", 20, ["PRODUCT_NAME"]),
            ("10.00", 800, ["LINE_TOTAL"]),
        ],
    )
    add_row(
        860,
        [
            ("ITEM B", 20, ["PRODUCT_NAME"]),
            ("20.00", 800, ["LINE_TOTAL"]),
        ],
    )
    add_row(820, [("SUBTOTAL", 300, []), ("30.00", 800, ["SUBTOTAL"])])
    add_row(780, [("TAX", 300, []), ("3.00", 800, ["TAX"])])
    add_row(740, [("TOTAL", 300, []), ("33.00", 800, ["GRAND_TOTAL"])])
    add_row(700, [("AMOUNT:", 40, []), ("$11.00", 800, [])])
    add_row(660, [("REMAINING BALANCE:", 40, []), ("$0.00", 800, [])])
    add_row(620, [("Shop Card", 40, []), ("11.00", 800, [])])
    add_row(580, [("AMOUNT:", 40, []), ("$22.00", 800, [])])
    add_row(540, [("EFT/Debit", 40, ["PAYMENT_METHOD"]), ("22.00", 800, [])])
    add_row(500, [("CHANGE", 40, []), ("0.00", 800, [])])
    add_row(460, [("A 9.75% Tax", 40, []), ("3.00", 800, [])])
    add_row(420, [("TOTAL TAX", 40, []), ("3.00", 800, [])])

    out = ffe.arithmetic_check(words)

    assert out["verdict"] == "PASS", out
    assert out["n_items"] == 2
    assert out["summary"]["tender"] == 33.0
    assert all(i["status"] == "HOLDS" for i in out["identities"])


# ---------------------------------------------------------------------------
# codex-review hardening (round 1)
# ---------------------------------------------------------------------------
def test_column_metric_fails_on_uniform_lane_shift():
    # a straight lane translated bodily by 2 cells with no second lane:
    # the abs-drift backstop must catch what residuals/gaps cannot
    rows = _amount_rows([300] * 8)
    cols = ffe.derive_columns_bootstrap(rows, W)
    real = blank()
    _ink_for_rows(real, rows, jitter=[0])
    shifted = blank()
    _ink_for_rows(shifted, rows, jitter=[16])  # 2 cells at cell_w=8
    out = ffe.metric_columns(real, shifted, rows, rows, cols, 8.0)
    assert out["verdict"] == "FAIL"
    entry = out["columns"][0]
    assert "abs_drift" in entry["failed_on"]


def test_column_metric_fails_on_missing_synth_lane():
    rows = _amount_rows([300] * 8)
    cols = ffe.derive_columns_bootstrap(rows, W)
    real = blank()
    _ink_for_rows(real, rows, jitter=[0])
    empty = blank()  # synth never draws the lane at all
    out = ffe.metric_columns(real, empty, rows, rows, cols, 8.0)
    assert out["verdict"] == "FAIL"
    assert any(
        "missing_lane" in e.get("failed_on", ()) for e in out["columns"]
    )


def test_token_precision_fails_fabrication_on_faithful():
    man = _manifest()
    drawn = man + [
        {
            "text": t,
            "bbox": [500, 500 - i * 40, 560, 520 - i * 40],
            "labels": [],
        }
        for i, t in enumerate(["PHANTOM", "INVENTED", "EXTRA"])
    ]
    out = ffe.metric_tokens(man, drawn, None, composed=False)
    assert out["verdict"] == "FAIL"
    # composed layouts re-emit repaired tokens: WARN, not FAIL
    out2 = ffe.metric_tokens(man, drawn, None, composed=True)
    assert out2["verdict"] == "PASS"


def test_style_metric_fails_on_synth_only_styling():
    # real side unstyled, synth side bold headers: invented styling fails
    _, syn_unstyled, rows, classes = _style_fixture(header_bold_in_syn=False)
    _, syn_styled, _, _ = _style_fixture(header_bold_in_syn=True)
    out = ffe.metric_style(
        syn_unstyled, syn_styled, rows, rows, classes, classes
    )
    entry = next(e for e in out["classes"] if e["class"] == "section_header")
    assert entry["verdict"] == "FAIL" and "bold" in entry.get(
        "extra_style", ()
    )


def test_amount_of_signs():
    assert ffe._amount_of("-4.00") == -4.0
    assert ffe._amount_of("-$4.00") == -4.0
    assert ffe._amount_of("$-4.00") == -4.0
    assert ffe._amount_of("4.00-") == -4.0
    assert ffe._amount_of("$1,299.00") == 1299.0
    assert ffe._amount_of("1299.00") == 1299.0
    assert ffe._amount_of("3.49T") == 3.49
    # double-signed tokens are malformed, not amounts
    assert ffe._amount_of("-4.00+") is None
    assert ffe._amount_of("+4.00-") is None
    assert ffe._amount_of("-$-4.00") is None


def test_overall_reports_coverage_gaps(monkeypatch, tmp_path):
    # graphics SKIPPED (no detector) + everything else PASS must not read
    # as an unqualified PASS
    monkeypatch.setattr(ffe, "_BARCODE_BIN", str(tmp_path / "missing"))
    out = ffe.metric_graphics("/none/a.png", "/none/b.png")
    assert out["verdict"] == "SKIPPED"


def test_render_path_call_sites_reference_shared_patterns():
    import compose_dollartree as cdt
    import render_synthetic_receipts as rsr

    from receipt_agent.agents.label_evaluator.rendering.price_tokens import (
        DOLLARTREE_PRICE_TOKEN,
        SYNTH_PRICE_TOKEN,
    )

    assert rsr._PRICE_TOKEN_RE is SYNTH_PRICE_TOKEN
    assert cdt._PRICE_RE is DOLLARTREE_PRICE_TOKEN


# ---------------------------------------------------------------------------
# adversarial fixtures (independent review of PR #1192): each is a case
# codex constructed that previously PASSed and must now FAIL
# ---------------------------------------------------------------------------
def test_adversarial_blank_composed_image_fails_tokens():
    # F1: a completely blank canvas for a composed render previously passed
    # (ink verification was skipped for composed layouts)
    man = _manifest()
    blank_img = blank()
    out = ffe.metric_tokens(man, man, blank_img, composed=True)
    assert out["verdict"] == "FAIL"
    assert out["ink_recall"] == 0.0


def test_adversarial_blank_single_character_tokens_fail():
    man = [
        {"text": "A", "bbox": [80, 800, 180, 900], "labels": []},
        {"text": "1", "bbox": [220, 800, 320, 900], "labels": []},
    ]
    blank_img = blank()

    for composed in (False, True):
        out = ffe.metric_tokens(man, man, blank_img, composed=composed)
        assert out["verdict"] == "FAIL"
        assert out["ink_checked"] == 2
        assert out["ink_recall"] == 0.0
        assert set(out["ink_missing_tokens"]) == {"A", "1"}


def test_token_metric_never_passes_without_checkable_ink_evidence():
    punctuation_only = [
        {"text": "---", "bbox": [80, 800, 180, 900], "labels": []}
    ]
    out = ffe.metric_tokens(
        punctuation_only,
        punctuation_only,
        blank(),
        composed=False,
    )
    assert out["verdict"] == "FAIL"
    assert out["ink_checked"] == 0
    assert out["ink_recall"] is None
    assert out["ink_evidence_missing"] is True


def test_adversarial_dot_tokens_fail_ink_check():
    # F1: every token replaced by a 2x2 dot previously cleared the >=4px bar
    man = _manifest()
    dots = blank()
    for w in ffe.words_to_px(man, W, H):
        cx, cy = int((w["l"] + w["r"]) / 2), int((w["t"] + w["b"]) / 2)
        stamp(dots, cx, cy, cx + 2, cy + 2)
    out = ffe.metric_tokens(man, man, dots, composed=False)
    assert out["verdict"] == "FAIL"
    assert out["ink_recall"] == 0.0
    # and real glyph-shaped ink still passes
    full = blank()
    for w in ffe.words_to_px(man, W, H):
        for x in range(int(w["l"]), int(w["r"]) - 3, 7):
            stamp(full, x, w["t"] + 1, x + 4, w["b"] - 1)
    out2 = ffe.metric_tokens(man, man, full, composed=False)
    assert out2["verdict"] == "PASS"


def test_adversarial_sheared_synth_lane_fails():
    # F2: a lane drifting linearly -10..+10px was invisible to per-side
    # Theil-Sen fits; the shear gate sees the tilt difference
    rows = _amount_rows([300] * 8)
    cols = ffe.derive_columns_bootstrap(rows, W)
    real = blank()
    _ink_for_rows(real, rows, jitter=[0])
    sheared = blank()
    for i, row in enumerate(rows):
        dx = -10 + int(round(20 * i / 7))
        for w in row:
            stamp(sheared, w["l"] + dx, w["t"], w["r"] + dx, w["b"])
    out = ffe.metric_columns(real, sheared, rows, rows, cols, 8.0)
    assert out["verdict"] == "FAIL"
    assert "shear" in out["columns"][0]["failed_on"]


def test_adversarial_typed_lane_needs_typed_carriers():
    # F3: a declared qty lane over rows carrying no qty tokens previously
    # PASSed on unrelated ink; typed carriers leave it UNTESTED, and the
    # columns metric reports the gap instead of a clean PASS
    rows = _amount_rows([300] * 8)  # rows have ITEM + price, no qty tokens
    qty_col = {
        "role": "qty",
        "anchor": "right",
        "x": 90 / W,  # the ITEM text right edge: plenty of unrelated ink
        "spread": 0.0,
        "support": 8,
    }
    img = blank()
    _ink_for_rows(img, rows, jitter=[0])
    out = ffe.metric_columns(img, img, rows, rows, [qty_col], 8.0)
    assert out["verdict"] == "UNTESTED"
    amount_col = ffe.derive_columns_bootstrap(rows, W)
    out2 = ffe.metric_columns(
        img, img, rows, rows, amount_col + [qty_col], 8.0
    )
    assert out2["verdict"] == "PASS_WITH_GAPS"
    assert out2["untested_roles"] == ["qty"]


def test_adversarial_doubled_stroke_fails_style():
    # F5: uniformly doubling every synth stroke passed body-relative
    # normalization (per-class ratios are unchanged when everything
    # doubles); the body stroke-rel ratio backstop catches it
    real, _, rows, classes = _style_fixture(header_bold_in_syn=True)
    doubled = blank()
    for i, row in enumerate(rows):
        is_header = i < 2
        y = 30 + i * 30
        bar = 12 if is_header else 4  # 2x the fixture's 6/2
        for x in range(40, 240, 20):  # wider step: bars stay distinct runs
            stamp(doubled, x, y + 2, x + bar, y + 12, 40)
    out = ffe.metric_style(real, doubled, rows, rows, classes, classes)
    assert out["verdict"] == "FAIL"
    assert out["body_stroke_fail"]


def test_adversarial_solid_separator_substitute_fails():
    # F7: dash -> solid at the same y previously matched and passed
    real = _with_rules([80, 200])
    solid = blank()
    for y in (80, 200):
        stamp(solid, int(0.05 * W), y, int(0.95 * W), y + 3)
    out = ffe.metric_separators(real, solid)
    assert out["verdict"] == "FAIL"
    assert out["kind_mismatches"] == 2


def test_adversarial_thin_line_logo_fails():
    # F7: a centered 1px vertical line of the right height passed the
    # height+center gates; the ink-mass gate rejects it
    real = blank()
    stamp(real, 140, 8, 260, 34)
    line = blank()
    stamp(line, 199, 8, 200, 34)
    out = ffe.metric_logo(real, line, (0.0, 0.15), expects_logo=True)
    assert out["verdict"] == "FAIL"


def test_evaluate_pair_style_and_columns_gaps_surface_in_overall():
    # F4: sub-metric UNTESTED entries must reach coverage_gaps / overall
    out = ffe.metric_style(blank(), blank(), [], [], [], [])
    assert out["verdict"] == "UNTESTED"

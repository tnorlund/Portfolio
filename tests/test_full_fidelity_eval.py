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
    # a tilted real scan (lane slides 1.5px per row) is geometry, not wobble
    rows = _amount_rows([300] * 8)
    cols = ffe.derive_columns_bootstrap(rows, W)
    real = blank()
    for i, row in enumerate(rows):
        for w in row:
            stamp(real, w["l"] + 1.5 * i, w["t"], w["r"] + 1.5 * i, w["b"])
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

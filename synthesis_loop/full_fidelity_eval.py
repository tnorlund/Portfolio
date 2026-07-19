#!/usr/bin/env python3.12
"""full_fidelity_eval.py -- the 7-metric full-fidelity gate (#1188 P1).

Supersedes section_compare's eyeball role: renders one receipt through the
production recipe (identical to ``glyph_review.py receipt``), fetches the real
pixels, and MEASURES both images instead of asking a reviewer to stare at
crops. Seven metrics, each with a machine verdict:

1. columns     -- per measured column: median x-deviation and per-row IQR
                  (wobble), ink-scanned within +/-3 cells of the expected
                  column, on BOTH images.
2. style       -- bold (ink-density ratio vs body median) + underline
                  (below-baseline rule probe) per row-class, both images.
3. tokens      -- recall/precision vs the real receipt's VALID-filtered OCR
                  manifest (catches erased / fabricated content).
4. separators  -- full-width rule rows (=, -, *) detected in both images;
                  count/order/approx-y compared.
5. graphics    -- barcode/QR inventory via VNDetectBarcodes (the repo's Swift
                  ``receipt-ocr --detect-barcodes-only``); phantom or missing
                  codes fail.
6. logo        -- presence / size-ratio / center-offset of the storefront
                  graphic vs the real receipt.
7. arithmetic  -- the generalized reconciler identities: qty x unit = line,
                  sum(lines) = subtotal, subtotal + tax = total = tender.

Every run emits ``<slug>.checks.json`` + ``<slug>.report.md`` + a stamped
``<slug>.sheet.png``, all carrying the git SHA, merchant-profile hash and
atlas hash; a dirty worktree refuses to run unless ``--allow-dirty``.

Usage:
  full_fidelity_eval.py run <merchant> <image_id> <receipt_id> <slug>
      [--out-root DIR] [--allow-dirty] [--columns-source bootstrap|profile]
  full_fidelity_eval.py real-real <merchant> <image_id_a> <receipt_id_a>
      <image_id_b> <receipt_id_b> <slug> [--out-root DIR] [--allow-dirty]

``real-real`` compares two REAL receipts of the same merchant (no synth):
the cross-receipt sanity leg of the metric-validation table -- structural
metrics must PASS there or they measure noise, not defects.

Env: same as glyph_review receipt mode (PYTHONPATH, DYNAMODB_TABLE_NAME,
AWS_REGION, BITMATRIX_DIR, PORTFOLIO_ENV=dev).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from statistics import median

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (
    HERE,
    os.path.join(REPO, "scripts"),
    os.path.join(REPO, "receipt_agent"),
    os.path.join(REPO, "tools", "glyph-studio", "py"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from receipt_agent.agents.label_evaluator.rendering.price_tokens import (  # noqa: E402
    is_price_token,
)
from receipt_agent.agents.label_evaluator.rendering.row_bands import (  # noqa: E402
    group_rows_greedy,
)

# ---------------------------------------------------------------------------
# fixed vertical bands (top-down fractions of receipt height) per slug.
# gelsons/thestand/dollartree reuse section_compare's measured splits; costco
# added here (eval-only geometry -- band edges do not touch the renderer).
# ---------------------------------------------------------------------------
SECTION_BANDS = {
    "dollartree": [
        ("STOREFRONT", 0.00, 0.105),
        ("ADDRESS", 0.105, 0.205),
        ("ITEMS", 0.205, 0.585),
        ("SUMMARY", 0.585, 0.670),
        ("PAYMENT", 0.670, 0.775),
        ("FOOTER", 0.775, 1.00),
    ],
    "gelsons": [
        ("STOREFRONT", 0.00, 0.05),
        ("ADDRESS", 0.05, 0.095),
        ("PAYMENT_HDR", 0.095, 0.34),
        ("ITEMS", 0.34, 0.80),
        ("SUMMARY", 0.80, 0.88),
        ("FOOTER", 0.88, 1.00),
    ],
    "thestand": [
        ("STOREFRONT", 0.00, 0.14),
        ("ADDRESS", 0.14, 0.26),
        ("ITEMS", 0.26, 0.66),
        ("SUMMARY", 0.66, 0.80),
        ("PAYMENT", 0.80, 0.90),
        ("FOOTER", 0.90, 1.00),
    ],
    "costco": [
        ("STOREFRONT", 0.00, 0.09),
        ("ITEMS", 0.09, 0.55),
        ("SUMMARY", 0.55, 0.66),
        ("PAYMENT", 0.66, 0.82),
        ("FOOTER", 0.82, 1.00),
    ],
}

# ---------------------------------------------------------------------------
# metric thresholds. Every constant here was calibrated by the
# historical-defect validation table (REFACTOR_P1P2_RESULT.md): each metric
# FAILS its known past defect and PASSES real-vs-real cross-receipt.
# ---------------------------------------------------------------------------
# columns: the synth column may wobble at most this much beyond the real one.
# Wobble is the IQR of residuals around a robust (Theil-Sen) lane fit, so a
# tilted real scan does not read as wobble -- only per-row jitter does.
COLUMN_WOBBLE_FACTOR = 2.0
COLUMN_WOBBLE_MARGIN_PX = 2.5
# columns: rows whose |residual| exceeds this many cells are off-lane
# outliers; the synth may have at most this much more of them than the real.
COLUMN_OUTLIER_CELLS = 0.75
COLUMN_OUTLIER_FRAC_MARGIN = 0.15
# columns: the flag->amount (or any lane pair) GAP is skew-invariant; synth
# and real gaps may differ by at most this many cells (the F-flag
# "cursor+1 after the price" drift moves the gap by >= 1 cell).
COLUMN_GAP_LIMIT_CELLS = 0.75
# columns: a row only measures a lane when its carrier token's own OCR edge
# sits within this many cells of the lane (keeps a summary amount printed
# inside the items band from contaminating the item lane).
COLUMN_MEMBER_CELLS = 1.5
COLUMN_MIN_ROWS = 4  # fewer measured rows than this -> UNTESTED, not PASS
# style: a row-class counts as bold when its median ink STROKE width (run
# length, normalized by row height) is >= this multiple of the body median.
# Stroke tracks print weight where box-fill density cannot (a regular-weight
# header at header size fills its box like a bold one). 1.4 sits above the
# one-pixel stroke quantization step at eval resolution (4px vs 5px = 1.25),
# and well below the measured real bold separation (Gelson's headers/address
# measure 1.4-2.0x body). Underlined = >= half the class's rows probe so.
STYLE_BOLD_RATIO = 1.4
STYLE_REAL_STYLED_MIN = 0.5
STYLE_SYN_STYLED_MIN = 0.25
STYLE_MIN_ROWS = 2
# tokens
TOKEN_INK_RECALL_MIN = 0.98
TOKEN_TEXT_RECALL_MIN = 0.98
TOKEN_TEXT_PRECISION_WARN = 0.85
# separators: y agreement tolerance (fraction of height); count must match.
SEPARATOR_Y_TOL = 0.025
# separators: a rule band is THIN; anything taller is a graphic or the scan
# background (a receipt photographed on a dark surface reads as a giant
# full-width "rule" without this cap).
SEPARATOR_MAX_H_PX = 24
# graphics: y agreement tolerance for matched codes.
GRAPHIC_Y_TOL = 0.05
# logo
LOGO_SIZE_RATIO_RANGE = (0.6, 1.6)
LOGO_CENTER_OFFSET_MAX = 0.12  # fraction of width
# arithmetic
CENTS_TOL = 0.005

_SEPARATOR_TEXT_RE = re.compile(r"^[*\-=_ ]{6,}$")


# ---------------------------------------------------------------------------
# stamping
# ---------------------------------------------------------------------------
def worktree_state() -> tuple[str, bool]:
    """(git HEAD sha, dirty?) for the repo this eval runs from."""
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    return sha, dirty


def profile_hash(merchant: str) -> str:
    with open(
        os.path.join(REPO, "scripts", "merchant_profiles.json"),
        encoding="utf-8",
    ) as fh:
        profiles = json.load(fh)["profiles"]
    block = profiles.get(merchant, {})
    payload = json.dumps(block, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def atlas_hash(merchant: str) -> str:
    """SHA over the atlas npz + logo bytes the profile references."""
    with open(
        os.path.join(REPO, "scripts", "merchant_profiles.json"),
        encoding="utf-8",
    ) as fh:
        block = json.load(fh)["profiles"].get(merchant, {})
    bitdir = os.environ.get("BITMATRIX_DIR", "/tmp/bitmatrix")
    names = sorted(
        set(
            list((block.get("typography") or {}).get("bitmap_font", {}).values())
            + ([block["logo"]] if block.get("logo") else [])
        )
    )
    digest = hashlib.sha256()
    for name in names:
        path = os.path.join(bitdir, name)
        digest.update(name.encode())
        if os.path.exists(path):
            with open(path, "rb") as fh:
                digest.update(fh.read())
        else:
            digest.update(b"<missing>")
    return digest.hexdigest()[:16]


def build_stamp(merchant: str, *, allow_dirty: bool) -> dict:
    sha, dirty = worktree_state()
    if dirty and not allow_dirty:
        raise SystemExit(
            "refusing to eval on a dirty worktree (results would not be "
            "reproducible from the stamped SHA) -- pass --allow-dirty for dev"
        )
    return {
        "git_sha": sha,
        "dirty": dirty,
        "profile_hash": profile_hash(merchant),
        "atlas_hash": atlas_hash(merchant),
        "merchant": merchant,
    }


# ---------------------------------------------------------------------------
# geometry: renderer-format words -> pixel rows
# ---------------------------------------------------------------------------
def words_to_px(words: list[dict], W: int, H: int) -> list[dict]:
    """Renderer-format words (bbox 0-1000, y-up) -> pixel dicts."""
    out = []
    for w in words:
        bb = w.get("bbox")
        if not bb:
            continue
        x0, y0, x1, y1 = (float(v) for v in bb[:4])
        left = min(x0, x1) / 1000.0 * W
        right = max(x0, x1) / 1000.0 * W
        top = (1 - max(y0, y1) / 1000.0) * H
        bottom = (1 - min(y0, y1) / 1000.0) * H
        out.append(
            {
                "text": str(w.get("text") or ""),
                "labels": list(w.get("labels") or []),
                "l": left,
                "r": right,
                "t": top,
                "b": bottom,
                "cy": (top + bottom) / 2.0,
                "h": bottom - top,
            }
        )
    return out


def group_visual_rows(px_words: list[dict]) -> list[list[dict]]:
    """Visual rows (stylescan contract: ascending, row-median, strict)."""
    rows = group_rows_greedy(
        px_words,
        lambda w: w["cy"],
        lambda w: w["h"] * 0.6,
        reference="row_median",
        strict=True,
    )
    return [sorted(r, key=lambda w: w["l"]) for r in rows]


def ocr_cell_width(rows: list[list[dict]]) -> float:
    """Median per-character advance (px) from adjacent same-row word pairs."""
    pitches = []
    for row in rows:
        for a, b in zip(row, row[1:]):
            cells = len(a["text"].replace(" ", "")) + 1
            if cells < 3:
                continue
            pitch = (b["l"] - a["l"]) / cells
            if 3.0 <= pitch <= 40.0:
                pitches.append(pitch)
    return float(median(pitches)) if pitches else 10.0


def paper_threshold(gray: np.ndarray) -> float:
    paper = float(np.percentile(gray, 85))
    return max(0.0, min(230.0, paper - 40.0))


# ---------------------------------------------------------------------------
# metric 1: columns (x-deviation + wobble)
# ---------------------------------------------------------------------------
def derive_columns_bootstrap(
    rows: list[list[dict]], W: int, *, tol: float = 0.04
) -> list[dict]:
    """Receipt-local column derivation (the pre-P2 source).

    Clusters the RIGHT edges of each row's rightmost amount token (greedy 1-D,
    ``tol`` of paper width -- the ``_price_column_x`` mechanics) and, when
    present, the LEFT edges of a detached single-letter tax flag after the
    amount. Returns ``[{role, anchor, x, spread, support}]`` (x normalized).
    """
    amounts: list[float] = []
    flags: list[float] = []
    for row in rows:
        prices = [w for w in row if is_price_token(w["text"])]
        if not prices:
            continue
        p = prices[-1]
        amounts.append(p["r"] / W)
        after = [
            w
            for w in row
            if w["l"] >= p["r"]
            and len(w["text"].strip()) == 1
            and w["text"].strip().isalpha()
        ]
        if after:
            flags.append(min(after, key=lambda w: w["l"])["l"] / W)

    def _cluster(edges: list[float], role: str, anchor: str) -> dict | None:
        if len(edges) < 2:
            return None
        ordered = sorted(edges, reverse=True)
        clusters: list[list[float]] = []
        for e in ordered:
            if clusters and abs(clusters[-1][0] - e) <= tol:
                clusters[-1].append(e)
            else:
                clusters.append([e])
        best = max(clusters, key=len)
        if len(best) < 2:
            return None
        qs = sorted(best)
        spread = qs[int(0.75 * (len(qs) - 1))] - qs[int(0.25 * (len(qs) - 1))]
        return {
            "role": role,
            "anchor": anchor,
            "x": round(float(median(best)), 4),
            "spread": round(float(spread), 4),
            "support": len(best),
        }

    out = []
    amount_col = _cluster(amounts, "amount", "right")
    if amount_col:
        out.append(amount_col)
    flag_col = _cluster(flags, "flag", "left")
    if flag_col:
        out.append(flag_col)
    return out


def _theil_sen(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Robust line fit x = a + b*y (median of pairwise slopes)."""
    if len(points) < 2:
        y0, x0 = points[0] if points else (0.0, 0.0)
        return x0, 0.0
    slopes = [
        (x2 - x1) / (y2 - y1)
        for i, (y1, x1) in enumerate(points)
        for (y2, x2) in points[i + 1 :]
        if abs(y2 - y1) > 1e-6
    ]
    b = median(slopes) if slopes else 0.0
    a = median(x - b * y for y, x in points)
    return a, b


def _iqr(vals: list[float]) -> float:
    qs = sorted(vals)
    return qs[int(0.75 * (len(qs) - 1))] - qs[int(0.25 * (len(qs) - 1))]


def measure_column(
    gray: np.ndarray,
    rows: list[list[dict]],
    column: dict,
    cell_w: float,
    neighbors: list[dict] | None = None,
) -> dict:
    """Ink-scan one column on one image.

    For every row whose carrier token BELONGS to this lane (its own OCR edge
    within ``COLUMN_MEMBER_CELLS`` of the lane x -- a summary amount printed
    inside the items band must not contaminate the item lane), scan +/-3
    cells around the expected x and record the relevant ink edge (rightmost
    for right-anchored, leftmost for left-anchored). The window is CLIPPED at
    the midpoint to any neighboring lane so one lane's ink can never
    masquerade as another's (a tax flag sits barely one cell right of the
    amount lane).

    Real scans are tilted/warped, so the lane is fit with Theil-Sen and the
    stats are RESIDUAL-based: ``wobble_iqr_px`` (IQR of residuals around the
    fit -- per-row jitter, not scan tilt), ``outlier_frac`` (rows further
    than ``COLUMN_OUTLIER_CELLS`` off the lane), ``lane_x_px`` (fitted lane
    position at the measured rows' median y -- for skew-invariant lane-GAP
    comparisons), and informational ``median_dev_px`` vs the OCR-expected x.
    """
    H, W = gray.shape
    x_expect = column["x"] * W
    thresh = paper_threshold(gray)
    lo = max(0, int(round(x_expect - 3 * cell_w)))
    hi = min(W, int(round(x_expect + 3 * cell_w)))
    for other in neighbors or []:
        ox = other["x"] * W
        mid = int(round((x_expect + ox) / 2.0))
        if ox > x_expect:
            hi = min(hi, mid)
        elif ox < x_expect:
            lo = max(lo, mid)
    points: list[tuple[float, float]] = []
    for row in rows:
        if column["role"] == "amount":
            carriers = [w for w in row if is_price_token(w["text"])]
        elif column["role"] == "flag":
            carriers = [
                w
                for w in row
                if len(w["text"].strip()) == 1 and w["text"].strip().isalpha()
            ]
        else:
            carriers = row
        edge_of = (lambda w: w["r"]) if column["anchor"] == "right" else (
            lambda w: w["l"]
        )
        carriers = [
            w
            for w in carriers
            if abs(edge_of(w) - x_expect) <= COLUMN_MEMBER_CELLS * cell_w
        ]
        if not carriers:
            continue
        t = int(min(w["t"] for w in carriers))
        b = int(max(w["b"] for w in carriers)) + 1
        if b - t < 3 or hi - lo < 3:
            continue
        band = gray[max(0, t) : b, lo:hi]
        ink_cols = np.nonzero((band < thresh).any(axis=0))[0]
        if not ink_cols.size:
            continue
        edge = (
            float(ink_cols.max()) if column["anchor"] == "right"
            else float(ink_cols.min())
        )
        points.append(((t + b) / 2.0, edge + lo))
    if len(points) < 2:
        return {"n_rows": len(points)}
    a, b_slope = _theil_sen(points)
    residuals = [x - (a + b_slope * y) for y, x in points]
    outliers = sum(
        1 for r in residuals if abs(r) > COLUMN_OUTLIER_CELLS * cell_w
    )
    mid_y = median(y for y, _ in points)
    measured = [x for _, x in points]
    return {
        "n_rows": len(points),
        "median_dev_px": round(float(median(measured)) - x_expect, 2),
        "wobble_iqr_px": round(float(_iqr(residuals)), 2),
        "outlier_frac": round(outliers / len(points), 3),
        "lane_x_px": round(a + b_slope * mid_y, 2),
        "lane_mid_y_px": round(float(mid_y), 1),
        "tilt_px_per_100rows": round(b_slope * 100.0, 3),
    }


def metric_columns(
    real_gray: np.ndarray,
    syn_gray: np.ndarray,
    rows_real: list[list[dict]],
    rows_syn: list[list[dict]],
    columns: list[dict],
    cell_w: float,
) -> dict:
    """Compare per-column deviation/wobble between the two images."""
    results = []
    verdict = "PASS"
    tested = 0
    for col in columns:
        neighbors = [c for c in columns if c is not col]
        real_m = measure_column(real_gray, rows_real, col, cell_w, neighbors)
        syn_m = measure_column(syn_gray, rows_syn, col, cell_w, neighbors)
        entry = {"column": col, "real": real_m, "synth": syn_m}
        n = min(real_m.get("n_rows", 0), syn_m.get("n_rows", 0))
        if n < COLUMN_MIN_ROWS:
            entry["verdict"] = "UNTESTED"
            results.append(entry)
            continue
        tested += 1
        wobble_limit = (
            real_m["wobble_iqr_px"] * COLUMN_WOBBLE_FACTOR
            + COLUMN_WOBBLE_MARGIN_PX
        )
        fail_wobble = syn_m["wobble_iqr_px"] > wobble_limit
        outlier_limit = (
            real_m["outlier_frac"] + COLUMN_OUTLIER_FRAC_MARGIN
        )
        fail_outliers = syn_m["outlier_frac"] > outlier_limit
        entry["wobble_limit_px"] = round(wobble_limit, 2)
        entry["outlier_limit"] = round(outlier_limit, 3)
        fails = []
        if fail_wobble:
            fails.append("wobble")
        if fail_outliers:
            fails.append("outliers")
        entry["verdict"] = "FAIL" if fails else "PASS"
        if fails:
            entry["failed_on"] = fails
            verdict = "FAIL"
        results.append(entry)

    # Lane-GAP agreement between measured column pairs: skew-invariant (both
    # lanes ride the same scan tilt), so it is the honest cross-image drift
    # test -- it is what catches "flags placed cursor+1 after the price"
    # instead of in the real receipt's dedicated flag column.
    gaps = []
    measurable = [
        r
        for r in results
        if r["verdict"] != "UNTESTED"
        and "lane_x_px" in r["real"]
        and "lane_x_px" in r["synth"]
    ]
    for i, ra in enumerate(measurable):
        for rb in measurable[i + 1 :]:
            real_gap = rb["real"]["lane_x_px"] - ra["real"]["lane_x_px"]
            syn_gap = rb["synth"]["lane_x_px"] - ra["synth"]["lane_x_px"]
            delta = abs(syn_gap - real_gap)
            gap_entry = {
                "pair": [
                    ra["column"]["role"],
                    rb["column"]["role"],
                ],
                "real_gap_px": round(real_gap, 2),
                "synth_gap_px": round(syn_gap, 2),
                "delta_px": round(delta, 2),
                "limit_px": round(COLUMN_GAP_LIMIT_CELLS * cell_w, 2),
                "verdict": (
                    "FAIL"
                    if delta > COLUMN_GAP_LIMIT_CELLS * cell_w
                    else "PASS"
                ),
            }
            if gap_entry["verdict"] == "FAIL":
                verdict = "FAIL"
            gaps.append(gap_entry)
    if not tested:
        verdict = "UNTESTED"
    return {
        "verdict": verdict,
        "cell_w_px": round(cell_w, 2),
        "columns": results,
        "lane_gaps": gaps,
    }


# ---------------------------------------------------------------------------
# metric 2: style agreement (bold + underline per row-class)
# ---------------------------------------------------------------------------
def _row_density(gray: np.ndarray, row: list[dict], thresh: float) -> float:
    t = int(min(w["t"] for w in row))
    b = int(max(w["b"] for w in row)) + 1
    l = int(min(w["l"] for w in row))
    r = int(max(w["r"] for w in row)) + 1
    crop = gray[max(0, t) : b, max(0, l) : r]
    return float((crop < thresh).mean()) if crop.size else 0.0


def _row_stroke(gray: np.ndarray, row: list[dict], thresh: float) -> float:
    """Median horizontal ink-run width of a row's WORDS (print weight, px).

    Measured per word box, not the row's union box, so ink between words
    (separator rules, a wordmark descender crossing the band) cannot inflate
    the stroke. Runs longer than 20px are structure (underlines, reverse
    boxes), not strokes, and are excluded -- same bound stylescan uses.
    """
    H, W = gray.shape
    widths: list[int] = []
    for w in row:
        t = max(0, int(w["t"]))
        b = min(H, int(w["b"]) + 1)
        l = max(0, int(w["l"]))
        r = min(W, int(w["r"]) + 1)
        crop = gray[t:b, l:r] < thresh
        if not crop.size:
            continue
        for line in crop:
            padded = np.concatenate([[0], line.view(np.uint8), [0]])
            starts = np.where(np.diff(padded) == 1)[0]
            ends = np.where(np.diff(padded) == -1)[0]
            widths.extend(
                int(e - s) for s, e in zip(starts, ends) if 1 <= e - s <= 20
            )
    return float(median(widths)) if widths else 0.0


def _row_underlined(gray: np.ndarray, row: list[dict]) -> bool:
    """Below-baseline rule detector that works on DOT-MATRIX underlines.

    stylescan's probe requires one continuous ink run, which a dotted thermal
    underline never produces at eval resolution. Here a rule row is a pixel
    row with >= 0.30 coverage spanning >= 0.70 of the row width with no hole
    wider than a quarter of it, that is VERTICALLY isolated -- some nearly
    empty row within 4px above AND below -- so glyph bottoms (ink above) and
    a following text line (ink below) never read as underlines.
    """
    H, W = gray.shape
    t = min(w["t"] for w in row)
    b = max(w["b"] for w in row)
    l = max(0, int(min(w["l"] for w in row)) - 3)
    r = min(W, int(max(w["r"] for w in row)) + 3)
    h = b - t
    y0 = max(0, int(b - 0.30 * h))
    y1 = min(H, int(b + 0.50 * h))
    if y1 - y0 < 3 or r - l <= 20:
        return False
    thresh = paper_threshold(gray)
    band = gray[y0:y1, l:r] < thresh
    width = r - l
    coverage = band.mean(axis=1)
    for i in range(len(coverage)):
        if coverage[i] < 0.30:
            continue
        cols = np.nonzero(band[i])[0]
        span = (cols.max() - cols.min()) / width
        gaps = np.diff(cols)
        max_gap = int(gaps.max()) if gaps.size else 0
        if span < 0.70 or max_gap > 0.25 * width:
            continue
        above = coverage[max(0, i - 4) : i]
        below = coverage[i + 1 : i + 5]
        gy = y0 + i
        # A real thermal rule prints tight under the glyph feet, so "clear
        # above" only means dropping below mid-glyph stroke coverage (~0.25),
        # not truly empty; below the rule the paper must actually clear.
        above_clear = (above < 0.25).any() if above.size else gy <= 2
        below_clear = (below < 0.10).any() if below.size else gy >= H - 3
        if above_clear and below_clear:
            return True
    return False


def classify_rows(rows: list[list[dict]], slug: str) -> list[str]:
    """Row-class per visual row via the shared stylemap classifier."""
    from receipt_agent.agents.label_evaluator.rendering.receipt_stylemap import (
        classify_row,
    )

    out = []
    for row in rows:
        text = " ".join(w["text"] for w in row).strip()
        out.append(classify_row(text, merchant=slug))
    return out


def metric_style(
    real_gray: np.ndarray,
    syn_gray: np.ndarray,
    rows_real: list[list[dict]],
    rows_syn: list[list[dict]],
    classes_real: list[str],
    classes_syn: list[str],
) -> dict:
    """Bold/underline agreement per row-class, measured on both images.

    A class is STYLED on a side when >= ``STYLE_REAL_STYLED_MIN`` of its rows
    are bold (density >= ``STYLE_BOLD_RATIO`` x that side's body median) or
    underlined. A class styled on the real side but not on the synth side
    fails (the pre-stylemap "headers unstyled" defect).
    """

    def side_stats(gray, rows, classes):
        thresh = paper_threshold(gray)
        # Weight signal = stroke width NORMALIZED by row height: a big-print
        # address line has proportionally thicker strokes without being bold
        # (stylescan's large-before-bold tiering, collapsed into one ratio).
        rel = []
        for row in rows:
            s_w = _row_stroke(gray, row, thresh)
            row_h = median(w["h"] for w in row)
            rel.append(s_w / row_h if row_h > 0 else 0.0)
        body = [
            s
            for s, c in zip(rel, classes)
            if c in ("item", "other", "footer", "survey") and s > 0
        ]
        body_med = median(body) if body else 0.0
        per: dict[str, dict] = {}
        for row, cls, s_rel in zip(rows, classes, rel):
            s = per.setdefault(cls, {"n": 0, "bold": 0, "underline": 0})
            s["n"] += 1
            if body_med and s_rel >= STYLE_BOLD_RATIO * body_med:
                s["bold"] += 1
            if _row_underlined(gray, row):
                s["underline"] += 1
        return per, body_med

    per_real, body_real = side_stats(real_gray, rows_real, classes_real)
    per_syn, body_syn = side_stats(syn_gray, rows_syn, classes_syn)
    classes = sorted(set(per_real) | set(per_syn))
    entries = []
    verdict = "PASS"
    for cls in classes:
        if cls in ("item", "other"):
            continue  # body IS the baseline; styling deltas live elsewhere
        r = per_real.get(cls, {"n": 0, "bold": 0, "underline": 0})
        s = per_syn.get(cls, {"n": 0, "bold": 0, "underline": 0})
        entry: dict = {"class": cls, "real": r, "synth": s}
        if r["n"] < STYLE_MIN_ROWS or s["n"] < STYLE_MIN_ROWS:
            entry["verdict"] = "UNTESTED"
            entries.append(entry)
            continue
        fails = []
        for attr in ("bold", "underline"):
            real_rate = r[attr] / r["n"]
            syn_rate = s[attr] / s["n"]
            if (
                real_rate >= STYLE_REAL_STYLED_MIN
                and syn_rate < STYLE_SYN_STYLED_MIN
            ):
                fails.append(attr)
        entry["verdict"] = "FAIL" if fails else "PASS"
        if fails:
            entry["missing_style"] = fails
            verdict = "FAIL"
        entries.append(entry)
    return {
        "verdict": verdict,
        "body_stroke_rel": {
            "real": round(body_real, 4),
            "synth": round(body_syn, 4),
        },
        "classes": entries,
    }


# ---------------------------------------------------------------------------
# metric 3: token recall / precision
# ---------------------------------------------------------------------------
def _norm_token(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).upper()


def metric_tokens(
    manifest_words: list[dict],
    drawn_words: list[dict],
    syn_gray: np.ndarray | None,
    *,
    composed: bool,
) -> dict:
    """Manifest-vs-drawn content check.

    ``text_recall``: fraction of manifest tokens present in the drawn word
    multiset (composed layouts compare ALPHABETIC tokens only -- amount repair
    is arithmetic's job). ``ink_recall`` (faithful renders only): fraction of
    manifest word boxes that contain ink in the synth image -- this is what
    catches an eraser that paints over content the input still carries.
    """
    from collections import Counter

    def toks(words, alpha_only):
        out = []
        for w in words:
            t = _norm_token(w.get("text"))
            if not t or not any(ch.isalnum() for ch in t):
                continue
            if alpha_only and not any(ch.isalpha() for ch in t):
                continue
            out.append(t)
        return Counter(out)

    man = toks(manifest_words, composed)
    drawn = toks(drawn_words, composed)
    hit = sum(min(man[t], drawn[t]) for t in man)
    total = sum(man.values())
    text_recall = hit / total if total else 1.0
    extra = sum(max(0, drawn[t] - man.get(t, 0)) for t in drawn)
    drawn_total = sum(drawn.values())
    text_precision = 1.0 - (extra / drawn_total) if drawn_total else 1.0
    missing = sorted(
        t for t in man if drawn[t] < man[t]
    )

    ink_recall = None
    ink_missing: list[str] = []
    if syn_gray is not None and not composed:
        H, W = syn_gray.shape
        thresh = paper_threshold(syn_gray)
        n_checked = n_hit = 0
        for w in words_to_px(manifest_words, W, H):
            t = _norm_token(w["text"])
            if len(t) < 2 or not any(ch.isalnum() for ch in t):
                continue
            l = max(0, int(w["l"]) - 2)
            r = min(W, int(w["r"]) + 3)
            tt = max(0, int(w["t"]) - 2)
            bb = min(H, int(w["b"]) + 3)
            if r - l < 2 or bb - tt < 2:
                continue
            n_checked += 1
            crop = syn_gray[tt:bb, l:r]
            if int((crop < thresh).sum()) >= 4:
                n_hit += 1
            else:
                ink_missing.append(t)
        ink_recall = n_hit / n_checked if n_checked else 1.0

    fail = text_recall < TOKEN_TEXT_RECALL_MIN or (
        ink_recall is not None and ink_recall < TOKEN_INK_RECALL_MIN
    )
    return {
        "verdict": "FAIL" if fail else "PASS",
        "text_recall": round(text_recall, 4),
        "text_precision": round(text_precision, 4),
        "precision_warn": text_precision < TOKEN_TEXT_PRECISION_WARN,
        "ink_recall": (
            round(ink_recall, 4) if ink_recall is not None else None
        ),
        "composed": composed,
        "missing_tokens": missing[:25],
        "ink_missing_tokens": ink_missing[:25],
    }


# ---------------------------------------------------------------------------
# metric 4: separator structure
# ---------------------------------------------------------------------------
def detect_separators(gray: np.ndarray) -> list[dict]:
    """Full-width rule rows in one image: ``[{y_frac, height_px, kind}]``.

    A separator row band has high ink coverage, near-full span, and no wide
    holes (dashed rules have periodic SMALL gaps; text rows have word-sized
    gaps). ``kind`` is a duty-cycle guess: ``double`` (=), ``dash`` (-/_),
    ``dense`` (*).
    """
    H, W = gray.shape
    x0, x1 = int(0.05 * W), int(0.95 * W)
    span_w = x1 - x0
    thresh = paper_threshold(gray)
    ink = gray[:, x0:x1] < thresh
    coverage = ink.mean(axis=1)
    rows = []
    for y in range(H):
        if coverage[y] < 0.30:
            continue
        cols = np.nonzero(ink[y])[0]
        span = (cols.max() - cols.min()) / span_w if cols.size else 0.0
        if span < 0.75:
            continue
        # widest hole inside the span
        gaps = np.diff(cols)
        max_gap = int(gaps.max()) if gaps.size else 0
        if max_gap > 0.08 * span_w:
            continue
        rows.append(y)
    if not rows:
        return []
    # Merge detected rows into bands. A starred/dotted rule reads as two thin
    # stripes (glyph top + bottom), so nearby stripes (<= 6px apart) are one
    # separator; 1px-high leftovers are scan noise, not rules.
    bands = []
    start = prev = rows[0]
    for y in rows[1:]:
        if y - prev <= 6:
            prev = y
            continue
        bands.append((start, prev))
        start = prev = y
    bands.append((start, prev))
    out = []
    for a, b in bands:
        h = b - a + 1
        if h < 2 or h > SEPARATOR_MAX_H_PX:
            continue
        # double-stripe (=) shows an internal low-coverage row between two
        # high-coverage stripes
        internal = coverage[a : b + 1]
        kind = "dash"
        if h >= 4 and internal.size >= 3 and internal.min() < 0.5 * internal.max():
            kind = "double"
        else:
            duty = float(ink[a : b + 1].mean())
            if duty > 0.85:
                kind = "solid"
        out.append(
            {
                "y_frac": round(((a + b) / 2.0) / H, 4),
                "height_px": h,
                "kind": kind,
            }
        )
    return out


def _drop_text_bands(
    seps: list[dict], rows: list[list[dict]] | None, H: int
) -> list[dict]:
    """Remove detected bands that are really TEXT rows.

    A dense condensed-pitch text line ("DOWNLOAD & LEARN MORE AT") can clear
    the coverage/span/gap bars. Any band overlapping an OCR row whose text is
    substantially alphanumeric is text; a char-rule row (----, ****, ====)
    either has no OCR row or OCRs as separator glyphs.
    """
    if rows is None:
        return seps
    spans = []
    for row in rows:
        text = "".join(w["text"] for w in row)
        alnum = sum(ch.isalnum() for ch in text)
        if alnum >= 4 and not _SEPARATOR_TEXT_RE.match(text):
            spans.append(
                (min(w["t"] for w in row), max(w["b"] for w in row))
            )
    kept = []
    for sep in seps:
        y = sep["y_frac"] * H
        if any(t - 2 <= y <= b + 2 for t, b in spans):
            continue
        kept.append(sep)
    return kept


def metric_separators(
    real_gray: np.ndarray,
    syn_gray: np.ndarray,
    rows_real: list[list[dict]] | None = None,
    rows_syn: list[list[dict]] | None = None,
) -> dict:
    real_seps = _drop_text_bands(
        detect_separators(real_gray), rows_real, real_gray.shape[0]
    )
    syn_seps = _drop_text_bands(
        detect_separators(syn_gray), rows_syn, syn_gray.shape[0]
    )
    matched = []
    unmatched_real = list(real_seps)
    unmatched_syn = list(syn_seps)
    for rs in real_seps:
        best = None
        for ss in unmatched_syn:
            dy = abs(ss["y_frac"] - rs["y_frac"])
            if dy <= SEPARATOR_Y_TOL and (best is None or dy < best[0]):
                best = (dy, ss)
        if best:
            matched.append({"real": rs, "synth": best[1], "dy": round(best[0], 4)})
            unmatched_real.remove(rs)
            unmatched_syn.remove(best[1])
    fail = bool(unmatched_real or unmatched_syn)
    return {
        "verdict": "FAIL" if fail else "PASS",
        "real_count": len(real_seps),
        "synth_count": len(syn_seps),
        "matched": matched,
        "missing_in_synth": unmatched_real,
        "phantom_in_synth": unmatched_syn,
    }


# ---------------------------------------------------------------------------
# metric 5: graphic inventory (barcodes / QR)
# ---------------------------------------------------------------------------
_BARCODE_BIN = os.environ.get(
    "RECEIPT_OCR_BIN",
    os.path.join(
        REPO,
        "receipt_ocr_swift",
        ".build",
        "arm64-apple-macosx",
        "release",
        "receipt-ocr",
    ),
)


def detect_graphics(png_path: str) -> list[dict] | None:
    """Barcode/QR inventory of one image via the Swift Vision detector."""
    if not os.path.exists(_BARCODE_BIN):
        return None
    with tempfile.TemporaryDirectory() as td:
        result = subprocess.run(
            [_BARCODE_BIN, "--detect-barcodes-only", png_path, "--output-dir", td],
            capture_output=True,
            timeout=120,
            check=False,
        )
        stem = os.path.splitext(os.path.basename(png_path))[0]
        out_json = os.path.join(td, stem + ".json")
        if result.returncode != 0 or not os.path.exists(out_json):
            return None
        with open(out_json, encoding="utf-8") as fh:
            codes = json.load(fh).get("barcodes") or []
    out = []
    for c in codes:
        sym = str(c.get("symbology") or "").lower()
        bb = c.get("boundingBox") or {}
        # Vision boundingBox is normalized with y-up origin bottom-left.
        y_frac = 1.0 - (
            float(bb.get("y", 0.0)) + float(bb.get("height", 0.0)) / 2.0
        )
        kind = (
            "qr"
            if any(k in sym for k in ("qr", "aztec", "datamatrix"))
            else "1d"
        )
        out.append(
            {"symbology": sym, "kind": kind, "y_frac": round(y_frac, 4)}
        )
    return sorted(out, key=lambda c: c["y_frac"])


def metric_graphics(real_png: str, syn_png: str) -> dict:
    real_codes = detect_graphics(real_png)
    syn_codes = detect_graphics(syn_png)
    if real_codes is None or syn_codes is None:
        return {
            "verdict": "SKIPPED",
            "note": f"barcode detector unavailable ({_BARCODE_BIN})",
        }
    matched = []
    unmatched_real = list(real_codes)
    unmatched_syn = list(syn_codes)
    for rc in real_codes:
        best = None
        for sc in unmatched_syn:
            if sc["kind"] != rc["kind"]:
                continue
            dy = abs(sc["y_frac"] - rc["y_frac"])
            if dy <= GRAPHIC_Y_TOL and (best is None or dy < best[0]):
                best = (dy, sc)
        if best:
            matched.append({"real": rc, "synth": best[1], "dy": round(best[0], 4)})
            unmatched_real.remove(rc)
            unmatched_syn.remove(best[1])
    fail = bool(unmatched_real or unmatched_syn)
    return {
        "verdict": "FAIL" if fail else "PASS",
        "real": real_codes,
        "synth": syn_codes,
        "matched": matched,
        "missing_in_synth": unmatched_real,
        "phantom_in_synth": unmatched_syn,
    }


# ---------------------------------------------------------------------------
# metric 6: logo presence / size / offset
# ---------------------------------------------------------------------------
def _largest_blob(gray: np.ndarray) -> dict | None:
    """Largest 8-connected ink component in a band (pure-numpy label pass)."""
    thresh = paper_threshold(gray)
    ink = (gray < thresh).astype(np.int32)
    if int(ink.sum()) < 16:
        return None
    H, W = ink.shape
    labels = np.zeros((H, W), dtype=np.int32)
    next_label = 0
    best = None
    stack: list[tuple[int, int]] = []
    for sy in range(H):
        for sx in range(W):
            if not ink[sy, sx] or labels[sy, sx]:
                continue
            next_label += 1
            stack.append((sy, sx))
            labels[sy, sx] = next_label
            n = 0
            y0 = y1 = sy
            x0 = x1 = sx
            while stack:
                y, x = stack.pop()
                n += 1
                y0, y1 = min(y0, y), max(y1, y)
                x0, x1 = min(x0, x), max(x1, x)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if (
                            0 <= ny < H
                            and 0 <= nx < W
                            and ink[ny, nx]
                            and not labels[ny, nx]
                        ):
                            labels[ny, nx] = next_label
                            stack.append((ny, nx))
            if best is None or n > best["area"]:
                best = {
                    "area": n,
                    "w": x1 - x0 + 1,
                    "h": y1 - y0 + 1,
                    "cx": (x0 + x1) / 2.0,
                    "cy": (y0 + y1) / 2.0,
                }
    return best


def metric_logo(
    real_gray: np.ndarray,
    syn_gray: np.ndarray,
    band: tuple[float, float],
    *,
    expects_logo: bool,
) -> dict:
    """Storefront-band graphic comparison.

    Compares the dominant ink blob of the top band in both images: presence,
    height ratio, and horizontal center offset. ``expects_logo=False``
    (wordmark-as-text merchants) only checks the band is non-empty in both.
    """
    H, W = real_gray.shape
    a, b = int(band[0] * H), int(band[1] * H)
    real_blob = _largest_blob(real_gray[a:b])
    syn_blob = _largest_blob(syn_gray[a:b])
    if real_blob is None:
        return {"verdict": "UNTESTED", "note": "no storefront ink in real"}
    if syn_blob is None:
        return {"verdict": "FAIL", "note": "storefront band empty in synth"}
    entry = {
        "real": {k: round(float(v), 1) for k, v in real_blob.items()},
        "synth": {k: round(float(v), 1) for k, v in syn_blob.items()},
    }
    if not expects_logo:
        entry["verdict"] = "PASS"
        entry["note"] = "wordmark_as_text: presence-only check"
        return entry
    size_ratio = syn_blob["h"] / max(1.0, real_blob["h"])
    center_off = abs(syn_blob["cx"] - real_blob["cx"]) / W
    entry["size_ratio"] = round(size_ratio, 3)
    entry["center_offset_frac"] = round(center_off, 4)
    lo, hi = LOGO_SIZE_RATIO_RANGE
    entry["verdict"] = (
        "FAIL"
        if not (lo <= size_ratio <= hi) or center_off > LOGO_CENTER_OFFSET_MAX
        else "PASS"
    )
    return entry


# ---------------------------------------------------------------------------
# metric 7: arithmetic consistency
# ---------------------------------------------------------------------------
_SUBTOTAL_RE = re.compile(r"^SUB\s?-?\s?TOTAL", re.I)
_TAX_RE = re.compile(r"^(SALES\s+TAX|SALES$|TAX\b|CA\s+TAX|NV\s+TAX)", re.I)
_TOTAL_RE = re.compile(r"^\**\s*(TOTAL|BALANCE\s+DUE|AMOUNT\s+DUE)\b", re.I)
_TENDER_RE = re.compile(
    r"^(AMERICAN|EXPRES|VISA|MASTERCARD|MC\b|DEBIT|CASH\b|EFT|CHARGE\b|"
    r"US\s+DEBIT|CREDIT\s+CARD)",
    re.I,
)
_CHANGE_RE = re.compile(r"^CHANGE\b", re.I)
_TIP_RE = re.compile(r"^(TIP|GRATUITY)\b", re.I)
_DISCOUNT_LABELS = {"DISCOUNT", "COUPON", "REFUND", "SAVINGS"}


def _amount_of(text: str) -> float | None:
    t = str(text or "").strip().lstrip("$").replace(",", "")
    m = re.match(r"^(\d+\.\d{2})[A-Z]?[-+]?$", t)
    return float(m.group(1)) if m else None


def arithmetic_check(words: list[dict]) -> dict:
    """Generalized reconciler identities over renderer-format words.

    Groups words into visual rows, extracts item rows (qty / unit / line
    total via labels with text fallbacks) and summary anchors, then checks:
    qty x unit = line, sum(lines) = subtotal (only when no discount labels
    muddy the sum), subtotal + tax = total, total = tender (tender - change
    when change is printed). Amounts compare at +/-``CENTS_TOL``.
    """
    px = words_to_px(words, 1000, 1000)
    rows = group_visual_rows(px)
    items = []
    summary: dict[str, float] = {}
    has_discount = any(
        set(w.get("labels") or []) & _DISCOUNT_LABELS for w in words
    )
    for row in rows:
        text = " ".join(w["text"] for w in row).strip()
        amounts = [
            (w, _amount_of(w["text"]))
            for w in row
            if _amount_of(w["text"]) is not None
        ]
        label_of = {
            lbl for w in row for lbl in (w.get("labels") or [])
        }
        if _SUBTOTAL_RE.match(text) and amounts:
            summary.setdefault("subtotal", amounts[-1][1])
        elif _TAX_RE.match(text) and amounts:
            summary.setdefault("tax", amounts[-1][1])
        elif _CHANGE_RE.match(text) and amounts:
            summary.setdefault("change", amounts[-1][1])
        elif _TIP_RE.match(text) and amounts:
            summary.setdefault("tip", amounts[-1][1])
        elif _TOTAL_RE.match(text) and amounts:
            summary.setdefault("total", amounts[-1][1])
        elif _TENDER_RE.match(text) and amounts:
            summary.setdefault("tender", amounts[-1][1])
        elif "LINE_TOTAL" in label_of or (
            amounts
            and any(ch.isalpha() for ch in text)
            and not any(
                rx.match(text)
                for rx in (
                    _SUBTOTAL_RE,
                    _TAX_RE,
                    _TOTAL_RE,
                    _TENDER_RE,
                    _CHANGE_RE,
                )
            )
        ):
            qty = None
            unit = None
            line = None
            for w, a in amounts:
                if "UNIT_PRICE" in (w.get("labels") or []):
                    unit = a
                elif "LINE_TOTAL" in (w.get("labels") or []):
                    line = a
            if line is None and amounts:
                line = amounts[-1][1]
            if unit is None and len(amounts) >= 2:
                unit = amounts[-2][1]
            # Quantity: a QUANTITY-labeled token wins; otherwise the LAST
            # standalone small int printed LEFT of the first amount (the qty
            # column) -- never a digit inside the description ("... SIZE 9").
            labeled_qty = [
                w
                for w in row
                if "QUANTITY" in (w.get("labels") or [])
                and re.fullmatch(r"\d{1,2}", w["text"].strip())
            ]
            if labeled_qty:
                qty = int(labeled_qty[-1]["text"].strip())
            else:
                first_amount_x = amounts[0][0]["l"] if amounts else None
                candidates = [
                    w
                    for w in row
                    if re.fullmatch(r"\d{1,2}", w["text"].strip())
                    and (first_amount_x is None or w["r"] <= first_amount_x)
                ]
                # only trust a bare int as qty when it stands apart from the
                # description (at least a couple of cells of gap)
                if candidates:
                    cand = candidates[-1]
                    left_of = [
                        w for w in row if w["r"] <= cand["l"] and w is not cand
                    ]
                    gap = (
                        cand["l"] - max(w["r"] for w in left_of)
                        if left_of
                        else float("inf")
                    )
                    if gap >= 1.5 * max(1.0, cand["h"]):
                        qty = int(cand["text"].strip())
            if line is not None:
                items.append(
                    {"text": text[:40], "qty": qty, "unit": unit, "line": line}
                )

    identities = []

    def check(name, lhs, rhs, detail):
        if lhs is None or rhs is None:
            identities.append(
                {"name": name, "status": "UNTESTABLE", "detail": detail}
            )
            return
        ok = abs(lhs - rhs) <= CENTS_TOL
        identities.append(
            {
                "name": name,
                "status": "HOLDS" if ok else "VIOLATED",
                "lhs": round(lhs, 2),
                "rhs": round(rhs, 2),
                "detail": detail,
            }
        )

    for it in items:
        if it["qty"] is not None and it["unit"] is not None:
            check(
                "qty_x_unit_eq_line",
                it["qty"] * it["unit"],
                it["line"],
                it["text"],
            )
    if items and "subtotal" in summary and not has_discount:
        check(
            "sum_lines_eq_subtotal",
            sum(it["line"] for it in items),
            summary["subtotal"],
            f"{len(items)} items",
        )
    if "subtotal" in summary and "tax" in summary and "total" in summary:
        check(
            "subtotal_plus_tax_eq_total",
            summary["subtotal"] + summary["tax"],
            summary["total"],
            "",
        )
    if "total" in summary and "tender" in summary:
        # tender - change = total, or total + tip = tender (restaurants).
        # With a tip line absent but tender > total, the difference may be an
        # unprinted gratuity -- only testable when a tip/change row anchors it.
        tender_net = summary["tender"] - summary.get("change", 0.0)
        expected_tender = summary["total"] + summary.get("tip", 0.0)
        if "tip" in summary or "change" in summary or abs(
            tender_net - summary["total"]
        ) <= CENTS_TOL:
            check("total_eq_tender", expected_tender, tender_net, "")
        else:
            identities.append(
                {
                    "name": "total_eq_tender",
                    "status": "UNTESTABLE",
                    "detail": (
                        f"tender {summary['tender']:.2f} != total "
                        f"{summary['total']:.2f} with no printed tip/change "
                        "row to anchor the difference"
                    ),
                }
            )
    violated = sum(1 for i in identities if i["status"] == "VIOLATED")
    testable = sum(1 for i in identities if i["status"] != "UNTESTABLE")
    return {
        "verdict": (
            "FAIL" if violated else ("PASS" if testable else "UNTESTED")
        ),
        "violated": violated,
        "testable": testable,
        "summary": {k: round(v, 2) for k, v in summary.items()},
        "n_items": len(items),
        "identities": identities,
    }


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def _load_real(merchant: str, image_id: str, receipt_id: int):
    """Real image + VALID-filtered words for one receipt (no rendering)."""
    from io import BytesIO

    import boto3
    from PIL import Image

    from receipt_dynamo.data.dynamo_client import DynamoClient

    region = os.environ.get("AWS_REGION", "us-east-1")
    table = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    c = DynamoClient(table)
    s3 = boto3.client("s3", region_name=region)
    d = c.get_receipt_details(image_id, receipt_id)
    rec = d.receipt
    lbl = {
        (l.line_id, l.word_id): l.label
        for l in d.labels
        if l.receipt_id == receipt_id
        and l.label not in (None, "O")
        and str(getattr(l, "validation_status", "") or "").upper() != "INVALID"
    }
    words = [
        {
            "text": w.text,
            "line_id": w.line_id,
            "word_id": w.word_id,
            "bbox": [
                w.top_left["x"] * 1000,
                w.top_left["y"] * 1000,
                w.bottom_right["x"] * 1000,
                w.bottom_right["y"] * 1000,
            ],
            "labels": (
                [lbl[(w.line_id, w.word_id)]]
                if lbl.get((w.line_id, w.word_id)) not in (None, "O")
                else []
            ),
        }
        for w in d.words
        if w.receipt_id == receipt_id
    ]
    real = None
    for bkt, key in [
        (rec.cdn_s3_bucket, rec.cdn_s3_key),
        (rec.raw_s3_bucket, rec.raw_s3_key),
    ]:
        if not bkt or not key:
            continue
        try:
            real = Image.open(
                BytesIO(s3.get_object(Bucket=bkt, Key=key)["Body"].read())
            ).convert("RGB")
            break
        except Exception:  # noqa: BLE001
            continue
    return real, words, rec


def profile_block(merchant: str) -> dict:
    with open(
        os.path.join(REPO, "scripts", "merchant_profiles.json"),
        encoding="utf-8",
    ) as fh:
        return json.load(fh)["profiles"].get(merchant, {})


def profile_columns(merchant: str, section: str) -> list[dict]:
    """Measured columns from the profile's layout_template (P2 source)."""
    block = profile_block(merchant)
    template = block.get("layout_template") or {}
    cols = (template.get("columns") or {}).get(section) or []
    return [dict(c) for c in cols]


def evaluate_pair(
    real_img,
    syn_img,
    words_real: list[dict],
    words_syn: list[dict],
    *,
    slug: str,
    merchant: str,
    real_png: str,
    syn_png: str,
    composed: bool,
    columns_source: str = "bootstrap",
) -> dict:
    """All 7 metrics over an aligned (real, synth) image pair."""
    W, H = syn_img.size
    real_gray = np.asarray(real_img.convert("L"), dtype=np.uint8)
    syn_gray = np.asarray(syn_img.convert("L"), dtype=np.uint8)
    px_real = words_to_px(words_real, W, H)
    px_syn = words_to_px(words_syn, W, H)
    rows_real = group_visual_rows(px_real)
    rows_syn = group_visual_rows(px_syn)
    cell_w = ocr_cell_width(rows_real)

    bands = SECTION_BANDS.get(slug) or [("ALL", 0.0, 1.0)]
    from glyphstudio.sections import normalize_band_name

    items_band = next(
        ((y0, y1) for name, y0, y1 in bands if name == "ITEMS"), (0.0, 1.0)
    )
    storefront_band = next(
        ((y0, y1) for name, y0, y1 in bands if name == "STOREFRONT"),
        (0.0, 0.12),
    )

    def rows_in(rows, band):
        y0, y1 = band[0] * H, band[1] * H
        return [
            r
            for r in rows
            if y0 <= median(w["cy"] for w in r) < y1
        ]

    # columns: per band with amount evidence (items + summary)
    per_band_columns = {}
    columns_verdict = "UNTESTED"
    for name, y0, y1 in bands:
        canonical = normalize_band_name(name) or name.lower()
        band_rows_real = rows_in(rows_real, (y0, y1))
        band_rows_syn = rows_in(rows_syn, (y0, y1))
        if columns_source == "profile":
            columns = profile_columns(merchant, canonical)
            source = "profile"
            if not columns:
                columns = derive_columns_bootstrap(band_rows_real, W)
                source = "bootstrap(no-profile-data)"
        else:
            columns = derive_columns_bootstrap(band_rows_real, W)
            source = "bootstrap"
        if not columns:
            continue
        result = metric_columns(
            real_gray,
            syn_gray,
            band_rows_real,
            band_rows_syn,
            columns,
            cell_w,
        )
        result["source"] = source
        per_band_columns[name] = result
        if result["verdict"] == "FAIL":
            columns_verdict = "FAIL"
        elif result["verdict"] == "PASS" and columns_verdict != "FAIL":
            columns_verdict = "PASS"

    classes_real = classify_rows(rows_real, slug)
    classes_syn = classify_rows(rows_syn, slug)
    style = metric_style(
        real_gray, syn_gray, rows_real, rows_syn, classes_real, classes_syn
    )
    tokens = metric_tokens(
        words_real, words_syn, syn_gray, composed=composed
    )
    separators = metric_separators(real_gray, syn_gray, rows_real, rows_syn)
    graphics = metric_graphics(real_png, syn_png)
    logo = metric_logo(
        real_gray,
        syn_gray,
        storefront_band,
        expects_logo=bool(profile_block(merchant).get("logo")),
    )
    arithmetic = arithmetic_check(words_syn)

    checks = {
        "columns": {"verdict": columns_verdict, "bands": per_band_columns},
        "style": style,
        "tokens": tokens,
        "separators": separators,
        "graphics": graphics,
        "logo": logo,
        "arithmetic": arithmetic,
    }
    verdicts = [m["verdict"] for m in checks.values()]
    checks["overall"] = "FAIL" if "FAIL" in verdicts else "PASS"
    return checks


def _write_report(out_stem: str, doc: dict) -> None:
    with open(out_stem + ".checks.json", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    stamp = doc["stamp"]
    checks = doc["checks"]
    lines = [
        f"# full_fidelity_eval -- {doc['slug']}",
        "",
        f"- merchant: {stamp['merchant']}",
        f"- receipt: {doc['image_id']}#{doc['receipt_id']}"
        + (
            f" vs {doc['image_id_b']}#{doc['receipt_id_b']}"
            if doc.get("image_id_b")
            else ""
        ),
        f"- git: `{stamp['git_sha'][:12]}`"
        + (" (DIRTY)" if stamp["dirty"] else ""),
        f"- profile: `{stamp['profile_hash']}` atlas: `{stamp['atlas_hash']}`",
        "",
        f"## OVERALL: {checks['overall']}",
        "",
        "| metric | verdict |",
        "|---|---|",
    ]
    for name in (
        "columns",
        "style",
        "tokens",
        "separators",
        "graphics",
        "logo",
        "arithmetic",
    ):
        lines.append(f"| {name} | {checks[name]['verdict']} |")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(checks, indent=1, sort_keys=True))
    lines.append("```")
    with open(out_stem + ".report.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _write_sheet(out_stem: str, real_img, syn_img, doc: dict) -> None:
    from PIL import Image, ImageDraw, ImageFont

    stamp = doc["stamp"]
    W, H = syn_img.size
    pad, top = 14, 56
    cv = Image.new("RGB", (W * 2 + pad * 3, H + top + pad), (238, 238, 238))
    cv.paste(real_img, (pad, top))
    cv.paste(syn_img, (W + pad * 2, top))
    dd = ImageDraw.Draw(cv)
    try:
        f = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Arial.ttf", 14
        )
    except OSError:
        f = ImageFont.load_default()
    text = (
        f"{doc['slug']}  {doc['checks']['overall']}  "
        f"git {stamp['git_sha'][:12]}{'+DIRTY' if stamp['dirty'] else ''}  "
        f"profile {stamp['profile_hash']}  atlas {stamp['atlas_hash']}"
    )
    dd.text((pad, 8), text, fill=(180, 0, 0), font=f)
    dd.text((pad, 30), "REAL", fill=(0, 0, 0), font=f)
    dd.text((W + pad * 2, 30), "SYNTH", fill=(0, 0, 0), font=f)
    cv.save(out_stem + ".sheet.png")


def run(args) -> int:
    import section_compare

    stamp = build_stamp(args.merchant, allow_dirty=args.allow_dirty)
    out_dir = os.path.join(args.out_root, f"{args.slug}_eval")
    os.makedirs(out_dir, exist_ok=True)
    real, syn, _n_bar, words = section_compare.render_pair(
        args.merchant, args.image_id, args.receipt_id
    )
    if real is None:
        raise SystemExit("real image unavailable; cannot evaluate fidelity")
    block = profile_block(args.merchant)
    composed = bool(block.get("compose"))
    words_syn = words
    if composed and block.get("compose") == "dollartree":
        from compose_dollartree import canonical_words

        words_syn = canonical_words(words)
    stem = os.path.join(out_dir, args.slug)
    real_png = stem + ".real.png"
    syn_png = stem + ".syn.png"
    real.save(real_png)
    syn.save(syn_png)
    checks = evaluate_pair(
        real,
        syn,
        words,
        words_syn,
        slug=args.slug,
        merchant=args.merchant,
        real_png=real_png,
        syn_png=syn_png,
        composed=composed,
        columns_source=args.columns_source,
    )
    doc = {
        "mode": "real-vs-synth",
        "slug": args.slug,
        "image_id": args.image_id,
        "receipt_id": args.receipt_id,
        "stamp": stamp,
        "checks": checks,
    }
    _write_report(stem, doc)
    _write_sheet(stem, real, syn, doc)
    print(
        f"OVERALL {checks['overall']} -> {stem}.checks.json "
        f"{stem}.report.md {stem}.sheet.png"
    )
    for name in (
        "columns",
        "style",
        "tokens",
        "separators",
        "graphics",
        "logo",
        "arithmetic",
    ):
        print(f"  {name:11s} {checks[name]['verdict']}")
    return 0 if checks["overall"] == "PASS" else 1


def run_real_real(args) -> int:
    stamp = build_stamp(args.merchant, allow_dirty=args.allow_dirty)
    out_dir = os.path.join(args.out_root, f"{args.slug}_eval")
    os.makedirs(out_dir, exist_ok=True)
    real_a, words_a, _rec_a = _load_real(
        args.merchant, args.image_id_a, args.receipt_id_a
    )
    real_b, words_b, _rec_b = _load_real(
        args.merchant, args.image_id_b, args.receipt_id_b
    )
    if real_a is None or real_b is None:
        raise SystemExit("real image unavailable")
    wt = 760
    ht = int(round(wt * real_a.height / real_a.width))
    real_a = real_a.resize((wt, ht))
    # receipt B keeps ITS aspect; both sides are measured independently
    real_b = real_b.resize((wt, int(round(wt * real_b.height / real_b.width))))
    stem = os.path.join(out_dir, args.slug)
    png_a = stem + ".a.png"
    png_b = stem + ".b.png"
    real_a.save(png_a)
    real_b.save(png_b)

    # Structural metrics that are meaningful across DIFFERENT receipts of one
    # merchant: columns (each side vs its own derived lanes), style, graphics
    # count-by-kind. Content metrics (tokens/arithmetic/separator y-order) are
    # receipt-specific and skipped.
    gray_a = np.asarray(real_a.convert("L"), dtype=np.uint8)
    gray_b = np.asarray(real_b.convert("L"), dtype=np.uint8)
    rows_a = group_visual_rows(words_to_px(words_a, *real_a.size))
    rows_b = group_visual_rows(words_to_px(words_b, *real_b.size))
    cell_a = ocr_cell_width(rows_a)
    cell_b = ocr_cell_width(rows_b)

    def side_columns(gray, rows, W, cell):
        cols = derive_columns_bootstrap(rows, W)
        return [
            {"column": c, "measured": measure_column(gray, rows, c, cell)}
            for c in cols
        ]

    side_a = side_columns(gray_a, rows_a, real_a.size[0], cell_a)
    side_b = side_columns(gray_b, rows_b, real_b.size[0], cell_b)

    # PASS iff each side's own lanes are tight: a real receipt's residual
    # wobble (around its own Theil-Sen lane fit, so scan tilt cancels) must
    # stay under half a cell + margin. That is the null test: if real
    # receipts tripped this, the wobble gate would measure noise.
    verdict = "PASS"
    details = []
    for label, side, cell in (("a", side_a, cell_a), ("b", side_b, cell_b)):
        for entry in side:
            m = entry["measured"]
            if m.get("n_rows", 0) < COLUMN_MIN_ROWS:
                continue
            limit = COLUMN_WOBBLE_MARGIN_PX + 0.5 * cell
            ok = m["wobble_iqr_px"] <= limit
            details.append(
                {
                    "side": label,
                    "column": entry["column"],
                    "measured": m,
                    "wobble_limit_px": round(limit, 2),
                    "verdict": "PASS" if ok else "FAIL",
                }
            )
            if not ok:
                verdict = "FAIL"

    style = metric_style(
        gray_a,
        gray_b,
        rows_a,
        rows_b,
        classify_rows(rows_a, args.slug),
        classify_rows(rows_b, args.slug),
    )
    graphics = metric_graphics(png_a, png_b)
    checks = {
        "columns": {"verdict": verdict, "sides": details},
        "style": style,
        "graphics": graphics,
    }
    verdicts = [m["verdict"] for m in checks.values() if m["verdict"] != "SKIPPED"]
    checks["overall"] = "FAIL" if "FAIL" in verdicts else "PASS"
    doc = {
        "mode": "real-vs-real",
        "slug": args.slug,
        "image_id": args.image_id_a,
        "receipt_id": args.receipt_id_a,
        "image_id_b": args.image_id_b,
        "receipt_id_b": args.receipt_id_b,
        "stamp": stamp,
        "checks": checks,
    }
    with open(stem + ".realreal.checks.json", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"REAL-REAL OVERALL {checks['overall']} -> {stem}.realreal.checks.json")
    for name in ("columns", "style", "graphics"):
        print(f"  {name:11s} {checks[name]['verdict']}")
    return 0 if checks["overall"] == "PASS" else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("merchant")
    p_run.add_argument("image_id")
    p_run.add_argument("receipt_id", type=int)
    p_run.add_argument("slug")
    p_run.add_argument("--out-root", default=".out")
    p_run.add_argument("--allow-dirty", action="store_true")
    p_run.add_argument(
        "--columns-source",
        choices=("bootstrap", "profile"),
        default="bootstrap",
    )
    p_rr = sub.add_parser("real-real")
    p_rr.add_argument("merchant")
    p_rr.add_argument("image_id_a")
    p_rr.add_argument("receipt_id_a", type=int)
    p_rr.add_argument("image_id_b")
    p_rr.add_argument("receipt_id_b", type=int)
    p_rr.add_argument("slug")
    p_rr.add_argument("--out-root", default=".out")
    p_rr.add_argument("--allow-dirty", action="store_true")
    args = ap.parse_args(argv)
    if args.mode == "run":
        return run(args)
    return run_real_real(args)


if __name__ == "__main__":
    sys.exit(main())

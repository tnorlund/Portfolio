"""Per-line typography: font attribution, typeface discovery, style runs.

Pilot layer over the M2 shape toolchain (``family_cluster``): given per-letter
crops of a receipt line, does the line's lettering match the merchant's known
body atlas? Lines that match poorly are candidates for a *different* typeface
within the same receipt (Wild Fork mixes faces in its body; Sprouts prints a
serif display wordmark above a mono body). Poorly-attributed lines are then
clustered in the same normalized shape space to *discover* the merchant's
typeface set T1..Tk, and contiguous lines sharing (typeface, tier,
underline, reverse-video) are grouped into STYLE RUNS — the orthogonal
per-line typography layer that semantic sections correlate with but do not
determine.

Everything here is pure numpy on masks/records; network + pixels live in the
CLI (``typography_runs.py``). Shape normalization and IoU come from
``family_cluster`` (aspect-preserving, the M2 convention).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Optional, Sequence

import numpy as np

from glyphstudio.family_cluster import glyph_iou, normalize_glyph

__all__ = [
    "clean_letter_mask",
    "shifted_iou",
    "attribution_ious",
    "line_attribution",
    "per_char_medians",
    "calibrated_deviation",
    "estimate_slant",
    "line_slant",
    "DIAGONAL_CHARS",
    "intra_line_overlap",
    "LineShape",
    "cluster_line_shapes",
    "exemplar_glyphs",
    "assign_tiers",
    "build_style_runs",
    "section_run_crosstab",
]


# --- per-crop cleaning -----------------------------------------------------


def connected_components(mask: np.ndarray) -> list[np.ndarray]:
    """4-connected components of a boolean mask, as boolean masks."""
    from collections import deque

    m = np.asarray(mask, dtype=bool)
    h, w = m.shape
    lbl = np.zeros((h, w), np.int32)
    comps: list[np.ndarray] = []
    for y in range(h):
        for x in range(w):
            if m[y, x] and not lbl[y, x]:
                q = deque([(y, x)])
                lbl[y, x] = len(comps) + 1
                pix = [(y, x)]
                while q:
                    cy, cx = q.popleft()
                    for ny, nx in (
                        (cy - 1, cx),
                        (cy + 1, cx),
                        (cy, cx - 1),
                        (cy, cx + 1),
                    ):
                        if (
                            0 <= ny < h
                            and 0 <= nx < w
                            and m[ny, nx]
                            and not lbl[ny, nx]
                        ):
                            lbl[ny, nx] = len(comps) + 1
                            q.append((ny, nx))
                            pix.append((ny, nx))
                cm = np.zeros((h, w), bool)
                ys, xs = zip(*pix)
                cm[list(ys), list(xs)] = True
                comps.append(cm)
    return comps


def clean_letter_mask(mask: np.ndarray) -> np.ndarray:
    """Strip neighbor bleed, specks, and rule fragments from a letter crop.

    OCR letter boxes span the full line height and routinely clip fragments of
    the adjacent letters; ``normalize_glyph`` crops to the FULL ink bbox, so a
    3-px neighbor fragment at the crop edge shreds the normalized shape (IoU
    vs the atlas drops from ~0.55 to ~0.1 — measured on Wild Fork line 6).
    Median-vote atlas builds absorb this across hundreds of samples; per-line
    attribution cannot, so clean each crop:

    - drop components with x-centroid in the outer 12% of the crop (neighbor
      bleed hugs the left/right edges),
    - drop specks (< max(3 px, 2% of ink)),
    - drop flat full-width components (<=2 px tall, >=85% of crop width:
      underline / separator-rule fragments).

    Vertically stacked parts (i/j dots, colons) survive because their
    x-centroid is central. If EVERY component is disqualified (a lone dash
    filling the crop, a single letter hugging the edge of a tight box) the
    largest is kept — an empty mask helps nobody — but a rule fragment that
    happens to out-weigh the letter no longer gets a free pass.
    """
    m = np.asarray(mask, dtype=bool)
    if not m.any():
        return m
    h, w = m.shape
    comps = connected_components(m)
    total = int(m.sum())
    keep = np.zeros_like(m)
    for c in comps:
        n = int(c.sum())
        ys, xs = np.where(c)
        cx = float(xs.mean()) / max(1, w - 1)
        ch = ys.max() - ys.min() + 1
        cw = xs.max() - xs.min() + 1
        if n < max(3, 0.02 * total):
            continue
        if cx < 0.12 or cx > 0.88:
            continue
        if ch <= 2 and cw >= 0.85 * w:
            continue
        keep |= c
    if not keep.any():
        keep = max(comps, key=lambda c: int(c.sum()))
    return keep


# --- attribution -----------------------------------------------------------


def _shift(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Translate a boolean mask, zero-filling exposed pixels (no wrap:
    normalized glyphs touch the canvas edges, so ``np.roll`` would smuggle
    edge ink onto the opposite side and fake intersections)."""
    out = np.zeros_like(mask)
    h, w = mask.shape
    ys0, ys1 = max(0, dy), min(h, h + dy)
    xs0, xs1 = max(0, dx), min(w, w + dx)
    out[ys0:ys1, xs0:xs1] = mask[
        max(0, -dy) : min(h, h - dy), max(0, -dx) : min(w, w - dx)
    ]
    return out


def shifted_iou(a: np.ndarray, b: np.ndarray, max_shift: int = 2) -> float:
    """Max IoU of ``a`` over ``b`` across integer shifts of +-``max_shift``.

    Segmentation jitter moves ink a pixel or two inside the normalized grid;
    plain IoU punishes that as shape difference. Small-shift tolerance keeps
    the metric about letterform, not box placement.
    """
    best = 0.0
    bb = np.asarray(b, dtype=bool)
    aa = np.asarray(a, dtype=bool)
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            best = max(best, glyph_iou(_shift(aa, dy, dx), bb))
    return best


def attribution_ious(
    letters: Sequence[tuple[str, np.ndarray]],
    atlas: dict[str, np.ndarray],
    max_shift: int = 2,
) -> list[tuple[str, float]]:
    """Per-letter (char, shifted IoU vs atlas) for a line; skips chars the
    atlas lacks."""
    return [
        (ch, shifted_iou(g, atlas[ch], max_shift))
        for ch, g in letters
        if ch in atlas
    ]


def line_attribution(
    letters: Sequence[tuple[str, np.ndarray]],
    atlas: dict[str, np.ndarray],
    min_letters: int = 4,
    max_shift: int = 2,
) -> tuple[Optional[float], int]:
    """Median per-letter shifted IoU of a line's normalized glyphs vs an atlas.

    ``letters`` is (char, normalized 2D bool mask) pairs; chars missing from
    the atlas are skipped. Returns (score, n_used); score None when fewer
    than ``min_letters`` usable letters (distributions, not n=1).
    """
    vals = [v for _, v in attribution_ious(letters, atlas, max_shift)]
    if len(vals) < min_letters:
        return None, len(vals)
    return float(median(vals)), len(vals)


def per_char_medians(
    ious: Sequence[tuple[str, float]], min_samples: int = 10
) -> dict[str, float]:
    """Median atlas-IoU per character over a merchant's whole vetted corpus.

    The body face dominates every receipt, so this is each char's *body*
    matching level — including the atlas's own per-char weaknesses (a traced
    'e' that only ever hits 0.41, a '.' at 0.21). Raw line medians confuse
    those weaknesses with typeface changes: 'Tender:' is built almost
    entirely from weak chars and false-flagged as a second face until each
    letter is judged against its own char's norm.
    """
    by_char: dict[str, list[float]] = {}
    for ch, v in ious:
        by_char.setdefault(ch, []).append(v)
    return {
        ch: float(median(vs))
        for ch, vs in by_char.items()
        if len(vs) >= min_samples
    }


def calibrated_deviation(
    ious: Sequence[tuple[str, float]],
    char_medians: dict[str, float],
    min_letters: int = 4,
) -> Optional[float]:
    """Median per-letter deviation from each char's own corpus norm.

    ~0 for body lines regardless of char mix; strongly negative when the
    letterforms genuinely differ from the atlas. Chars without a corpus norm
    are skipped. None when fewer than ``min_letters`` usable letters.
    """
    devs = [v - char_medians[ch] for ch, v in ious if ch in char_medians]
    if len(devs) < min_letters:
        return None
    return float(median(devs))


# --- slant / italic probe ---------------------------------------------------


def estimate_slant(
    mask: np.ndarray, max_deg: float = 30.0, step: float = 1.0
) -> float:
    """Dominant stroke slant of one glyph, in degrees (positive = italic
    rightward lean), by shear search.

    Shear the ink by candidate angles and score the sharpness of the column
    projection (sum of squared column counts); vertical strokes concentrate
    ink into few columns exactly when the shear cancels their lean. Upright
    text peaks at 0; true italics peak at +8..+20.
    """
    m = np.asarray(mask, dtype=bool)
    ys, xs = np.where(m)
    if ys.size < 8:
        return 0.0
    h = ys.max() - ys.min() + 1
    if h < 4:
        return 0.0
    yc = (ys.max() + ys.min()) / 2.0
    best_deg, best_score = 0.0, -1.0
    # candidates ordered by |deg|: on a tie (thin strokes quantize identically
    # across small angles) the most-upright interpretation wins.
    degs = sorted(np.arange(-max_deg, max_deg + 1e-6, step), key=abs)
    for deg in degs:
        t = np.tan(np.radians(deg))
        sheared = np.round(xs + t * (ys - yc)).astype(int)
        _, counts = np.unique(sheared, return_counts=True)
        score = float((counts.astype(float) ** 2).sum())
        if score > best_score:
            best_score, best_deg = score, float(deg)
    # Image y grows DOWNWARD, so a rightward (italic) lean has larger x at
    # smaller y; the shear that straightens it (x + tan(deg)*(y - yc)) then
    # has deg > 0. Positive result = rightward lean, as typographers expect.
    return best_deg


# Letterforms whose strokes are legitimately diagonal: a line of card-mask
# X's "leans" ~14deg to the shear probe without any italic intent. Excluded
# from line-level slant so the italic finding measures the typeface, not the
# letter inventory.
DIAGONAL_CHARS = set("AKMNRVWXYZkvwxyz/\\%*<>")


def line_slant(
    slants: Sequence[tuple[str, float]], min_glyphs: int = 3
) -> Optional[float]:
    """Median per-glyph slant over a line, from (char, slant_deg) pairs.

    Skips ``DIAGONAL_CHARS`` and NaNs (glyphs too short to lean)."""
    vals = [
        s
        for ch, s in slants
        if ch not in DIAGONAL_CHARS and s == s  # NaN-safe
    ]
    if len(vals) < min_glyphs:
        return None
    return float(median(vals))


# --- contamination (overlap-stamped OCR, e.g. Sprouts wordmark) -------------


def intra_line_overlap(boxes: Sequence[tuple[float, float, float, float]]) -> float:
    """Fraction of a line's letter boxes whose x-span overlaps ANY other
    box on the line by >30% (of the smaller box).

    Overlap-contaminated regions (the Sprouts wordmark double-strike) stamp
    letter boxes on top of each other; their crops are unusable for shape
    attribution and must be flagged EXPLICITLY, not silently misattributed.
    Boxes are (x0, y0, x1, y1). All pairs are checked (double-strike passes
    are not adjacent after sorting), and both members of a bad pair count.
    """
    if len(boxes) < 2:
        return 0.0
    spans = [(min(b[0], b[2]), max(b[0], b[2])) for b in boxes]
    bad = [False] * len(spans)
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            a0, a1 = spans[i]
            b0, b1 = spans[j]
            inter = min(a1, b1) - max(a0, b0)
            smaller = min(a1 - a0, b1 - b0)
            if smaller > 0 and inter > 0.3 * smaller:
                bad[i] = bad[j] = True
    return sum(bad) / len(spans)


# --- within-merchant typeface discovery -------------------------------------


@dataclass
class LineShape:
    """A line's letterforms in normalized shape space, for clustering."""

    key: str  # e.g. "<image8>#<rid>:<visual line idx>"
    glyphs: dict[str, np.ndarray]  # char -> normalized bool mask (median-voted)
    text: str = ""


def _median_vote(stack: list[np.ndarray]) -> np.ndarray:
    return np.mean(np.stack(stack).astype(float), axis=0) >= 0.5


def line_shape(
    key: str, letters: Sequence[tuple[str, np.ndarray]], text: str = ""
) -> LineShape:
    """Collapse a line's (char, normalized mask) pairs into per-char votes."""
    by_char: dict[str, list[np.ndarray]] = {}
    for ch, g in letters:
        by_char.setdefault(ch, []).append(np.asarray(g, dtype=bool))
    return LineShape(
        key=key,
        glyphs={ch: _median_vote(gs) for ch, gs in by_char.items()},
        text=text,
    )


def line_pair_iou(
    a: LineShape, b: LineShape, max_shift: int = 2
) -> tuple[float, int]:
    """Mean shifted IoU over the chars two lines share."""
    shared = sorted(set(a.glyphs) & set(b.glyphs))
    if not shared:
        return 0.0, 0
    vals = [shifted_iou(a.glyphs[ch], b.glyphs[ch], max_shift) for ch in shared]
    return float(np.mean(vals)), len(shared)


def cluster_line_shapes(
    lines: Sequence[LineShape],
    threshold: float = 0.45,
    min_shared: int = 3,
    max_shift: int = 2,
) -> list[list[int]]:
    """Single-linkage clustering of lines by shared-letterform IoU.

    Union-find over pairs with mean IoU >= ``threshold`` on >= ``min_shared``
    shared chars (evidence gate, same rationale as M2's family clustering: a
    single coincidental glyph must not merge typefaces). Returns clusters as
    lists of indices into ``lines``, largest first; singletons included.
    """
    n = len(lines)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            iou, shared = line_pair_iou(lines[i], lines[j], max_shift)
            if shared >= min_shared and iou >= threshold:
                parent[find(i)] = find(j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=len, reverse=True)


def exemplar_glyphs(
    members: Sequence[LineShape], min_votes: int = 2
) -> dict[str, np.ndarray]:
    """Per-char median-voted exemplar over a cluster's lines.

    Chars seen on fewer than ``min_votes`` member lines are skipped for
    clusters of >=2 lines (a one-line vote is just that line back again).
    """
    by_char: dict[str, list[np.ndarray]] = {}
    for ls in members:
        for ch, g in ls.glyphs.items():
            by_char.setdefault(ch, []).append(g)
    need = min_votes if len(members) >= 2 else 1
    return {
        ch: _median_vote(gs) for ch, gs in by_char.items() if len(gs) >= need
    }


# --- tiers (receipt-relative, ported convention from stylescan) --------------


def assign_tiers(
    lines: Sequence[dict],
    cap_key: str = "cap_px",
    stroke_key: str = "stroke_med",
    large_ratio: float = 1.45,
    bold_ratio: float = 1.30,
) -> tuple[Optional[float], Optional[float]]:
    """Set ``line["tier"]`` in place: normal / bold / large.

    stylescan's convention (receipt-relative so scanner exposure cancels),
    with one pilot deviation: the body reference is the median over ALL
    measured lines instead of section-classified body lines — this layer is
    deliberately section-blind. Returns (body_cap, body_stroke).
    """
    caps = [l[cap_key] for l in lines if l.get(cap_key)]
    strokes = [l[stroke_key] for l in lines if l.get(stroke_key)]
    body_cap = median(caps) if caps else None
    body_stroke = median(strokes) if strokes else None
    for l in lines:
        tier = "normal"
        if body_cap and l.get(cap_key) and l[cap_key] >= large_ratio * body_cap:
            tier = "large"
        elif (
            body_stroke
            and l.get(stroke_key)
            and l[stroke_key] >= bold_ratio * body_stroke
        ):
            tier = "bold"
        l["tier"] = tier
    return body_cap, body_stroke


# --- style runs --------------------------------------------------------------


@dataclass
class StyleRun:
    """A maximal contiguous block of lines sharing one typographic style."""

    run_id: int
    typeface: str
    tier: str
    underline: bool
    reverse_video: bool
    line_indices: list[int] = field(default_factory=list)
    line_ids: list[int] = field(default_factory=list)

    def style_key(self) -> tuple:
        return (self.typeface, self.tier, self.underline, self.reverse_video)


def build_style_runs(lines: Sequence[dict]) -> list[StyleRun]:
    """Group contiguous measured lines sharing (typeface, tier, underline,
    reverse_video) into runs.

    ``lines`` are visual-line records in top-to-bottom order with keys
    ``typeface``, ``tier``, ``underline``, ``reverse_video``, ``line_ids``.
    Lines with typeface None (too few letters to attribute) or ``"X"``
    (overlap-contaminated — excluded, never attributed) are transparent:
    they don't break a run and belong to no run, so they can't fabricate
    multi-style sections in the crosstab.
    """
    runs: list[StyleRun] = []
    for idx, l in enumerate(lines):
        if l.get("typeface") in (None, "X"):
            continue
        key = (
            l["typeface"],
            l.get("tier", "normal"),
            bool(l.get("underline")),
            bool(l.get("reverse_video")),
        )
        if runs and runs[-1].style_key() == key:
            runs[-1].line_indices.append(idx)
            runs[-1].line_ids.extend(l.get("line_ids", []))
        else:
            runs.append(
                StyleRun(
                    run_id=len(runs),
                    typeface=key[0],
                    tier=key[1],
                    underline=key[2],
                    reverse_video=key[3],
                    line_indices=[idx],
                    line_ids=list(l.get("line_ids", [])),
                )
            )
    return runs


def section_run_crosstab(
    sections: Sequence[dict], runs: Sequence[StyleRun]
) -> list[dict]:
    """Cross-tab semantic sections vs style runs.

    ``sections`` are dicts with ``section_type`` and ``line_ids`` (the QA'd
    ReceiptSection rows). For each section: which runs its lines fall into,
    over the lines that were typographically measured at all. A section
    spanning >1 run is the quantified evidence that semantic sections do not
    determine typography.
    """
    line_to_run: dict[int, int] = {}
    for r in runs:
        for lid in r.line_ids:
            line_to_run[lid] = r.run_id
    out = []
    for s in sections:
        run_ids = sorted(
            {line_to_run[lid] for lid in s["line_ids"] if lid in line_to_run}
        )
        measured = [lid for lid in s["line_ids"] if lid in line_to_run]
        styles = {runs[rid].style_key() for rid in run_ids}
        out.append(
            {
                "section_type": s["section_type"],
                "n_lines": len(s["line_ids"]),
                "n_measured": len(measured),
                "run_ids": run_ids,
                "n_runs": len(run_ids),
                "typefaces": sorted(
                    {runs[rid].typeface for rid in run_ids}
                ),
                # multi_run: split across runs (includes being interrupted by
                # a different-styled block). multi_style: the section itself
                # contains >1 distinct (typeface, tier, underline, reverse)
                # style — the direct "sections don't determine typography"
                # evidence. multi_typeface: strongest form, >1 letterform set.
                "multi_run": len(run_ids) > 1,
                "n_styles": len(styles),
                "multi_style": len(styles) > 1,
                "multi_typeface": len({runs[rid].typeface for rid in run_ids})
                > 1,
            }
        )
    return out

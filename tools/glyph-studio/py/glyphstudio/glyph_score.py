"""GlyphScore: letterform fidelity as a percentile of real-print variation.

The bulk realism gates (density, cap height, pitch) measure *how much* ink a
render puts down, not *what shapes* it draws — a render whose fonts a human
instantly rejects can pass every bulk gate (the Smith's cold-start blind
spot). GlyphScore closes that gap: a rendered glyph is scored against a
REFERENCE DISTRIBUTION of how the merchant's real printing varies among
itself, so "does this 'e' look like their 'e'?" becomes "where does this 'e'
sit inside the spread of real 'e's?".

Two calibration modes, one scoring rule:

- ``self`` mode (reference = a merchant's cleaned real letter crops): the
  crops' median-vote EXEMPLAR is the anchor; each crop's similarity to the
  leave-one-out exemplar forms the distribution, and a candidate glyph's
  similarity to the exemplar is placed inside it. A candidate that varies
  from the merchant's central letterform no more than real prints do scores
  mid-distribution; a wrong letterform scores below every real crop.
- ``anchor`` mode (reference = a single designed glyph, e.g. the bitMatrix-C2
  chart, calibrated by the crops): the distribution is how well REAL crops
  match the anchor (the printer-distortion spread); the candidate's IoU vs
  the anchor is placed inside that.

Calibration is COHORT-MATCHED (revision 2): a single noisy stamp (render
cell, letter crop) is placed inside the single-print spread; a median-voted
atlas glyph is placed inside the split-half-exemplar spread. Judging clean
candidates against noisy distributions lets cleanliness masquerade as
fidelity.

Scores are percentiles in [0, 1] (midrank, tie-tolerant); the roll-up is
usage-frequency weighted, so a broken 'E' hurts a merchant that prints many
E's more than a broken '@'. Everything here is pure numpy on normalized
masks; network, pixels and caching live in the CLI (``glyph_score_cli.py``).

Shape space and primitives are the established ones: ``normalize_glyph``
(aspect-preserving 32x32, the M2 convention — cap heights are matched because
every glyph is compared at its own normalized ink box) and PR #1106's
``shifted_iou`` (small-shift tolerance keeps the metric about letterform,
not box placement) and ``clean_letter_mask`` (crop hygiene, applied by the
CLI's extractors).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np

from glyphstudio.family_cluster import normalize_glyph
from glyphstudio.typography import _shift, connected_components

__all__ = [
    "segment_word_mask",
    "CharReference",
    "GlyphResult",
    "shifted_iou_stack",
    "crop_exemplar",
    "self_similarity",
    "anchor_similarity",
    "exemplar_split_similarity",
    "exemplar_split_anchor_similarity",
    "N_SPLITS",
    "percentile_of",
    "build_self_references",
    "build_anchor_references",
    "score_glyph",
    "score_instances",
    "per_char_table",
    "group_rollup",
    "weighted_glyphscore",
    "save_refpack",
    "load_refpack",
    "MIN_REF",
    "MAX_REF",
    "DEFAULT_MAX_SHIFT",
]

MIN_REF = 8  # distributions, not n=1: chars with fewer crops are not scored
MAX_REF = 150  # cost cap; deterministic even subsample beyond this
DEFAULT_MAX_SHIFT = 2  # the PR #1106 jitter tolerance


# --- vectorized shifted IoU ---------------------------------------------------


def shifted_iou_stack(
    mask: np.ndarray, stack: np.ndarray, max_shift: int = DEFAULT_MAX_SHIFT
) -> np.ndarray:
    """Shifted IoU of one normalized mask vs a (n, H, W) stack.

    Equivalent to ``[typography.shifted_iou(mask, s) for s in stack]`` (the
    CANDIDATE side is shifted, zero-filled — same convention), vectorized so
    a receipt's hundreds of letter instances stay affordable.
    """
    a = np.asarray(mask, dtype=bool)
    st = np.asarray(stack, dtype=bool)
    if st.ndim != 3:
        raise ValueError(f"stack must be (n, H, W), got {st.shape}")
    n = st.shape[0]
    if n == 0:
        return np.zeros(0, dtype=float)
    shifts = [
        _shift(a, dy, dx)
        for dy in range(-max_shift, max_shift + 1)
        for dx in range(-max_shift, max_shift + 1)
    ]
    sh = np.stack(shifts)  # (k, H, W)
    inter = np.einsum("khw,nhw->kn", sh.astype(np.int32), st.astype(np.int32))
    sums_sh = sh.sum(axis=(1, 2)).astype(np.int32)[:, None]  # (k, 1)
    sums_st = st.sum(axis=(1, 2)).astype(np.int32)[None, :]  # (1, n)
    union = sums_sh + sums_st - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        iou = np.where(union > 0, inter / union, 0.0)
    return iou.max(axis=0)


def crop_exemplar(stack: np.ndarray) -> np.ndarray:
    """Median-vote exemplar of a crop stack: pixel on where >= half the
    crops ink it. This is the same vote atlas builds use — it cancels the
    per-print noise and KEEPS the letterform.
    """
    st = np.asarray(stack, dtype=bool)
    return st.mean(axis=0) >= 0.5


def self_similarity(
    stack: np.ndarray, max_shift: int = DEFAULT_MAX_SHIFT
) -> np.ndarray:
    """Leave-one-out exemplar similarity for each member of a crop stack:
    crop_i vs the median-vote exemplar of the OTHERS.

    This is the reference's OWN spread: how far real prints land from the
    merchant's central letterform. It is the yardstick every candidate is
    read against in ``self`` mode.

    Why exemplar-anchored, not crop-cloud-anchored (metric revision 1): a
    candidate's median IoU against the raw crop CLOUD rewards sharing the
    cloud's blur signature more than sharing its letterform — a data-built
    atlas of a DIFFERENT typeface outscored the merchant's own designed
    font on that statistic (Vons 56 vs bitMatrix-C2 chart 41 against Costco
    crops; the M3 pooling lesson again). Median-voting collapses the blur
    and restores letterform discrimination.
    """
    st = np.asarray(stack, dtype=bool)
    n = st.shape[0]
    votes = st.sum(axis=0).astype(np.int32)
    out = np.zeros(n, dtype=float)
    for i in range(n):
        # exemplar of the others, via the vote-sum trick (no re-stacking)
        ex_i = (votes - st[i].astype(np.int32)) >= 0.5 * (n - 1)
        out[i] = shifted_iou_stack(st[i], ex_i[None], max_shift)[0]
    return out


def anchor_similarity(
    stack: np.ndarray,
    anchor: np.ndarray,
    max_shift: int = DEFAULT_MAX_SHIFT,
) -> np.ndarray:
    """Shifted IoU of each crop vs a single designed glyph (the anchor).

    The distribution of these values IS the printer-distortion spread: how
    far real printing lands from the designed letterform.
    """
    st = np.asarray(stack, dtype=bool)
    return np.array(
        [
            shifted_iou_stack(s, np.asarray(anchor, bool)[None], max_shift)[0]
            for s in st
        ]
    )


N_SPLITS = 15  # deterministic split-half repetitions for exemplar cohorts


def _split_halves(stack: np.ndarray, n_splits: int = N_SPLITS):
    """Deterministic split-half pairs (seeded permutations: reruns must
    reproduce)."""
    st = np.asarray(stack, dtype=bool)
    n = st.shape[0]
    for k in range(n_splits):
        perm = np.random.default_rng(k).permutation(n)
        yield st[perm[: n // 2]], st[perm[n // 2 :]]


def exemplar_split_similarity(
    stack: np.ndarray,
    n_splits: int = N_SPLITS,
    max_shift: int = DEFAULT_MAX_SHIFT,
) -> np.ndarray:
    """Split-half exemplar agreement: exemplars of two independent halves of
    the crops, compared to each other, over ``n_splits`` deterministic
    splits.

    This is the CLEAN-cohort yardstick (metric revision 2): how well two
    independent noise-free estimates of the merchant's letterform agree
    (Costco: ~0.87 median, tight). An atlas glyph — itself a clean estimate —
    is judged against THIS, not against single noisy prints.
    """
    vals = [
        shifted_iou_stack(crop_exemplar(a), crop_exemplar(b)[None], max_shift)[0]
        for a, b in _split_halves(stack, n_splits)
    ]
    return np.array(vals)


def exemplar_split_anchor_similarity(
    stack: np.ndarray,
    anchor: np.ndarray,
    n_splits: int = N_SPLITS,
    max_shift: int = DEFAULT_MAX_SHIFT,
) -> np.ndarray:
    """Split-half exemplars vs a designed glyph: the clean-cohort version of
    ``anchor_similarity`` (two values per split)."""
    anc = np.asarray(anchor, bool)[None]
    vals: list[float] = []
    for a, b in _split_halves(stack, n_splits):
        vals.append(float(shifted_iou_stack(crop_exemplar(a), anc, max_shift)[0]))
        vals.append(float(shifted_iou_stack(crop_exemplar(b), anc, max_shift)[0]))
    return np.array(vals)


def percentile_of(dist: np.ndarray, value: float) -> float:
    """Midrank percentile of ``value`` inside ``dist``, in [0, 1].

    Midrank ((#less + (#equal + 1)/2) / (n + 1)) is tie-tolerant (IoU values
    quantize) and never returns exactly 0 or 1 for a value inside the
    distribution's range.
    """
    d = np.asarray(dist, dtype=float)
    if d.size == 0:
        return float("nan")
    less = int((d < value).sum())
    equal = int((d == value).sum())
    return (less + (equal + 1) / 2.0) / (d.size + 1)


# --- references ----------------------------------------------------------------


@dataclass
class CharReference:
    """Everything needed to score one character: the anchor letterform, the
    calibration distribution, and the reference crops behind them.

    Both modes score a candidate the same way — shifted IoU vs the anchor —
    and differ only in WHAT anchors: the crops' own median-vote exemplar
    (``self``) or a designed glyph (``anchor``), and in which spread
    calibrates the percentile.
    """

    char: str
    stack: np.ndarray  # (n, H, W) bool reference crops
    dist: np.ndarray  # calibration distribution
    mode: str  # "self" | "anchor"
    count: int  # usage count in the reference corpus (the weight)
    anchor: Optional[np.ndarray] = None

    def stat(self, mask: np.ndarray, max_shift: int = DEFAULT_MAX_SHIFT) -> float:
        return float(
            shifted_iou_stack(mask, self.anchor[None], max_shift)[0]
        )


def _cap_stack(stack: np.ndarray, max_ref: int) -> np.ndarray:
    if stack.shape[0] <= max_ref:
        return stack
    # deterministic even subsample (no RNG: reruns must reproduce)
    idx = np.linspace(0, stack.shape[0] - 1, max_ref).round().astype(int)
    return stack[np.unique(idx)]


def build_self_references(
    refpack: Mapping[str, np.ndarray],
    min_ref: int = MIN_REF,
    max_ref: int = MAX_REF,
    max_shift: int = DEFAULT_MAX_SHIFT,
    cohort: str = "single",
) -> dict[str, CharReference]:
    """``self`` mode references from a char -> (n, H, W) crop-stack mapping:
    anchor = the crops' median-vote exemplar; distribution = the calibration
    cohort MATCHED to what will be scored (metric revision 2):

    - ``cohort="single"`` (render cells, letter crops — one noisy stamp
      each): leave-one-out single-crop-vs-exemplar spread.
    - ``cohort="exemplar"`` (atlas glyphs — median-voted, noise-free):
      split-half exemplar agreement. Judging a clean candidate against the
      single-print spread lets CLEANLINESS masquerade as FIDELITY (a
      wrong-typeface atlas outscored the true designed font that way).
    """
    refs: dict[str, CharReference] = {}
    for ch, stack in refpack.items():
        st = np.asarray(stack, dtype=bool)
        if st.shape[0] < min_ref:
            continue
        capped = _cap_stack(st, max_ref)
        dist = (
            exemplar_split_similarity(capped, max_shift=max_shift)
            if cohort == "exemplar"
            else self_similarity(capped, max_shift)
        )
        refs[ch] = CharReference(
            char=ch,
            stack=capped,
            dist=dist,
            mode="self",
            count=int(st.shape[0]),
            anchor=crop_exemplar(capped),
        )
    return refs


def build_anchor_references(
    refpack: Mapping[str, np.ndarray],
    anchors: Mapping[str, np.ndarray],
    min_ref: int = MIN_REF,
    max_ref: int = MAX_REF,
    max_shift: int = DEFAULT_MAX_SHIFT,
    cohort: str = "single",
) -> dict[str, CharReference]:
    """``anchor`` mode: designed glyph per char, calibrated by the real
    crops at the candidate's aggregation level (see
    ``build_self_references`` for the cohort rationale):

    - ``cohort="single"``: crops-vs-anchor (the printer-distortion spread).
    - ``cohort="exemplar"``: split-half exemplars vs anchor.

    Chars missing from either side are skipped — an anchor without crops has
    no distortion spread to place candidates in.
    """
    refs: dict[str, CharReference] = {}
    for ch, stack in refpack.items():
        if ch not in anchors:
            continue
        st = np.asarray(stack, dtype=bool)
        if st.shape[0] < min_ref:
            continue
        anchor = normalize_glyph(np.asarray(anchors[ch]))
        capped = _cap_stack(st, max_ref)
        dist = (
            exemplar_split_anchor_similarity(capped, anchor, max_shift=max_shift)
            if cohort == "exemplar"
            else anchor_similarity(capped, anchor, max_shift)
        )
        refs[ch] = CharReference(
            char=ch,
            stack=capped,
            dist=dist,
            mode="anchor",
            count=int(st.shape[0]),
            anchor=anchor,
        )
    return refs


# --- scoring --------------------------------------------------------------------


@dataclass
class GlyphResult:
    char: str
    raw: float  # the candidate's similarity statistic
    pct: float  # percentile inside the reference distribution
    group: Optional[str] = None  # e.g. a line key, for roll-ups


def score_glyph(
    ref: CharReference,
    mask: np.ndarray,
    max_shift: int = DEFAULT_MAX_SHIFT,
) -> GlyphResult:
    raw = ref.stat(np.asarray(mask, dtype=bool), max_shift)
    return GlyphResult(
        char=ref.char, raw=raw, pct=percentile_of(ref.dist, raw)
    )


def score_instances(
    refs: Mapping[str, CharReference],
    instances: Iterable[tuple],
    max_shift: int = DEFAULT_MAX_SHIFT,
) -> list[GlyphResult]:
    """Score (char, normalized mask[, group]) tuples; chars without a
    reference are skipped (they cannot be judged, so they must not vote)."""
    out: list[GlyphResult] = []
    for inst in instances:
        ch, mask = inst[0], inst[1]
        group = inst[2] if len(inst) > 2 else None
        ref = refs.get(ch)
        if ref is None:
            continue
        r = score_glyph(ref, mask, max_shift)
        r.group = group
        out.append(r)
    return out


# --- aggregation -----------------------------------------------------------------


def per_char_table(results: Sequence[GlyphResult]) -> dict[str, dict]:
    """The localization property: WHICH chars drag the score."""
    by: dict[str, list[GlyphResult]] = {}
    for r in results:
        by.setdefault(r.char, []).append(r)
    return {
        ch: {
            "n": len(rs),
            "raw_med": float(np.median([r.raw for r in rs])),
            "pct_med": float(np.median([r.pct for r in rs])),
            "pct_mean": float(np.mean([r.pct for r in rs])),
        }
        for ch, rs in sorted(by.items())
    }


def group_rollup(results: Sequence[GlyphResult]) -> dict[str, dict]:
    """Mean percentile per group (per-line when groups are line keys).

    Instance pooling IS usage-frequency weighting: a char occurring five
    times on a line casts five votes.
    """
    by: dict[str, list[GlyphResult]] = {}
    for r in results:
        by.setdefault(r.group or "", []).append(r)
    return {
        g: {
            "n": len(rs),
            "pct_mean": float(np.mean([r.pct for r in rs])),
            "raw_med": float(np.median([r.raw for r in rs])),
        }
        for g, rs in sorted(by.items())
    }


def weighted_glyphscore(
    results: Sequence[GlyphResult],
    refs: Optional[Mapping[str, CharReference]] = None,
) -> float:
    """The GlyphScore roll-up, 0..100 (higher = closer to real printing).

    With one result per instance (renders, receipts), the plain mean is
    already usage-weighted. With one result per CHAR (scoring an atlas),
    pass ``refs`` so each char is weighted by its usage count in the
    reference corpus.
    """
    if not results:
        return float("nan")
    if refs is None:
        return 100.0 * float(np.mean([r.pct for r in results]))
    wsum = vsum = 0.0
    for r in results:
        w = float(refs[r.char].count) if r.char in refs else 0.0
        wsum += w
        vsum += w * r.pct
    return 100.0 * (vsum / wsum) if wsum > 0 else float("nan")


# --- render word segmentation (pure mask logic; binarization is the caller's) ----


def segment_word_mask(
    mask: np.ndarray,
    chars: str,
    min_ink: int = 4,
    snap_frac: float = 0.3,
) -> Optional[list[np.ndarray]]:
    """Split one rendered word's ink mask into per-character cell masks.

    Grid renders are MONOSPACE, but connected components are useless for
    them: dot-matrix glyphs shatter into many components while condensed
    neighbors touch into one. So segment by pitch instead: divide the word's
    ink extent into ``len(chars)`` equal cells, snapping each cut to the
    lowest-ink column within +-``snap_frac`` of a pitch (the valley between
    letters). Cells get SPECK-ONLY cleaning (drop components under
    max(3 px, 2% of cell ink) — cut slivers and texture noise). The OCR-box
    cleaner's edge-centroid rule is deliberately NOT applied here: a pitch
    cell is tight around the glyph, so an 'L' whose left bar touches the
    cell edge is the letter, not neighbor bleed.

    Returns per-char cropped masks left-to-right, or None when the word
    cannot be segmented (empty cell, sub-2px pitch): an unsegmentable word
    must be skipped and counted, never guessed.
    """
    want = [c for c in chars if not c.isspace()]
    m = np.asarray(mask, dtype=bool)
    n = len(want)
    if not n or not m.any():
        return None
    proj = m.sum(axis=0)
    xs = np.where(proj > 0)[0]
    x0, x1 = int(xs[0]), int(xs[-1])
    pitch = (x1 - x0 + 1) / n
    if n > 1 and pitch < 2.0:
        return None
    bounds = [x0]
    for i in range(1, n):
        nominal = x0 + i * pitch
        w = max(1, int(round(pitch * snap_frac)))
        lo = max(bounds[-1] + 1, int(round(nominal)) - w)
        hi = min(x1, int(round(nominal)) + w + 1)
        if lo >= hi:
            cut = min(x1, max(bounds[-1] + 1, int(round(nominal))))
        else:
            cut = lo + int(np.argmin(proj[lo:hi]))
        bounds.append(cut)
    bounds.append(x1 + 1)
    out: list[np.ndarray] = []
    for a, b in zip(bounds, bounds[1:]):
        cell = m[:, a:b]
        if cell.size == 0 or int(cell.sum()) < min_ink:
            return None
        cleaned = _despeck(cell)
        if int(cleaned.sum()) < min_ink:
            return None
        out.append(cleaned)
    return out


def _despeck(cell: np.ndarray) -> np.ndarray:
    """Drop components smaller than max(3 px, 2% of ink) — the speck rule
    from ``clean_letter_mask``, without its OCR-box edge heuristics."""
    total = int(cell.sum())
    floor = max(3, 0.02 * total)
    comps = connected_components(cell)
    keep = np.zeros_like(cell)
    for c in comps:
        if int(c.sum()) >= floor:
            keep |= c
    return keep if keep.any() else cell


# --- refpack I/O -------------------------------------------------------------------


def save_refpack(path: str, refpack: Mapping[str, np.ndarray]) -> None:
    """char -> (n, H, W) bool stacks as an npz (keys ``r<ord>``)."""
    arrays = {
        f"r{ord(ch)}": np.asarray(stack, dtype=bool)
        for ch, stack in refpack.items()
    }
    np.savez_compressed(path, **arrays)


def load_refpack(path: str) -> dict[str, np.ndarray]:
    data = np.load(path)
    return {
        chr(int(k[1:])): data[k].astype(bool)
        for k in data.files
        if k.startswith("r")
    }

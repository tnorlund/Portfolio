"""GlyphScore: letterform fidelity as a percentile of real-print variation.

The bulk realism gates (density, cap height, pitch) measure *how much* ink a
render puts down, not *what shapes* it draws — a render whose fonts a human
instantly rejects can pass every bulk gate (the Smith's cold-start blind
spot). GlyphScore closes that gap: a rendered glyph is scored against a
REFERENCE DISTRIBUTION of how the merchant's real printing varies among
itself, so "does this 'e' look like their 'e'?" becomes "where does this 'e'
sit inside the spread of real 'e's?".

Two calibration modes, one scoring rule:

- ``self`` mode (reference = a merchant's cleaned real letter crops): each
  crop's similarity to its peers (leave-one-out median shifted IoU) forms the
  distribution; a candidate glyph's similarity-to-the-crops is placed inside
  it. A candidate that varies from real prints no more than real prints vary
  from each other scores mid-distribution; a wrong letterform scores below
  every real crop.
- ``anchor`` mode (reference = a single designed glyph, e.g. the bitMatrix-C2
  chart, calibrated by the crops): the distribution is how well REAL crops
  match the anchor (the printer-distortion spread); the candidate's IoU vs
  the anchor is placed inside that.

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
from glyphstudio.typography import _shift

__all__ = [
    "CharReference",
    "GlyphResult",
    "shifted_iou_stack",
    "crop_similarity",
    "self_similarity",
    "anchor_similarity",
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


def crop_similarity(
    mask: np.ndarray, stack: np.ndarray, max_shift: int = DEFAULT_MAX_SHIFT
) -> float:
    """One glyph's similarity to a reference crop stack: the MEDIAN shifted
    IoU vs each member.

    Median, not max: matching one outlier crop must not count as matching the
    merchant's letterform; matching the central mass does.
    """
    vals = shifted_iou_stack(mask, stack, max_shift)
    return float(np.median(vals)) if vals.size else 0.0


def self_similarity(
    stack: np.ndarray, max_shift: int = DEFAULT_MAX_SHIFT
) -> np.ndarray:
    """Leave-one-out ``crop_similarity`` for each member of a crop stack.

    This is the reference's OWN spread: how much real prints of this char
    vary among themselves. It is the yardstick every candidate is read
    against in ``self`` mode.
    """
    st = np.asarray(stack, dtype=bool)
    n = st.shape[0]
    out = np.zeros(n, dtype=float)
    idx = np.arange(n)
    for i in range(n):
        rest = st[idx != i]
        out[i] = crop_similarity(st[i], rest, max_shift)
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
    """Everything needed to score one character: the reference crops, the
    calibration distribution, and (in anchor mode) the designed glyph."""

    char: str
    stack: np.ndarray  # (n, H, W) bool reference crops
    dist: np.ndarray  # calibration distribution
    mode: str  # "self" | "anchor"
    count: int  # usage count in the reference corpus (the weight)
    anchor: Optional[np.ndarray] = None

    def stat(self, mask: np.ndarray, max_shift: int = DEFAULT_MAX_SHIFT) -> float:
        if self.mode == "anchor":
            return float(
                shifted_iou_stack(mask, self.anchor[None], max_shift)[0]
            )
        return crop_similarity(mask, self.stack, max_shift)


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
) -> dict[str, CharReference]:
    """``self`` mode references from a char -> (n, H, W) crop-stack mapping."""
    refs: dict[str, CharReference] = {}
    for ch, stack in refpack.items():
        st = np.asarray(stack, dtype=bool)
        if st.shape[0] < min_ref:
            continue
        capped = _cap_stack(st, max_ref)
        refs[ch] = CharReference(
            char=ch,
            stack=capped,
            dist=self_similarity(capped, max_shift),
            mode="self",
            count=int(st.shape[0]),
        )
    return refs


def build_anchor_references(
    refpack: Mapping[str, np.ndarray],
    anchors: Mapping[str, np.ndarray],
    min_ref: int = MIN_REF,
    max_ref: int = MAX_REF,
    max_shift: int = DEFAULT_MAX_SHIFT,
) -> dict[str, CharReference]:
    """``anchor`` mode: chart glyph per char, calibrated by the real crops.

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
        refs[ch] = CharReference(
            char=ch,
            stack=capped,
            dist=anchor_similarity(capped, anchor, max_shift),
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

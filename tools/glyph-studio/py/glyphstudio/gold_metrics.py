"""Analytic metric library for the Costco gold-standard benchmark harness.

Pure numpy + scipy implementations of the L0-L4 measurement-ladder metrics
described in ``GOLD_STANDARD.md`` (Part 1 / Part 3). Nothing here needs a
trained model: every function is a deterministic pixel/geometry/texture
statistic so the ladder is reproducible offline.

Grouped by rung:

- L0 bulk gates: :func:`ink_ratio`, :func:`normalized_fill`,
  :func:`cap_height_px`, :func:`line_pitch_px`.
- L2 geometry: :func:`chamfer_distance`, :func:`boundary_iou`,
  :func:`advance_width_px`, :func:`right_edge_std`, :func:`caption_height_ratio`.
- L3 texture/degradation: :func:`stroke_core_stats`,
  :func:`edge_transition_frac`, :func:`paper_clip_frac`, :func:`tone_emd`,
  :func:`kanungo_pattern_hist`, :func:`kanungo_two_sample`.
- L4 perceptual/task: :func:`ssim_map`, :func:`ms_ssim`, :func:`ocr_error_sets`,
  :func:`glcm_contrast`, :func:`spacing_jitter`.
- Alignment: :func:`ocr_anchors`, :func:`piecewise_row_warp`.

Conventions: images are 2-D float64 luminance arrays (0=black, 255=white);
"ink" is darkness ``255 - L``; binary masks are bool ink==True. Physical
units for chamfer are pixels on the array the masks live in (the CLI reports
"dots" on the aspect-normalized 32x32 glyph grid).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable, Optional, Sequence

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy import ndimage

# ---------------------------------------------------------------------------
# L0 -- bulk ink & geometry gates
# ---------------------------------------------------------------------------


def ink_ratio(real: np.ndarray, render: np.ndarray) -> float:
    """Total-ink ratio Sigma(255-L)_render / Sigma(255-L)_real.

    Single scalar for "too heavy/dark". Equivalent to the ratio of mean
    darkness when both images share a canvas size.
    """
    real = np.asarray(real, dtype=np.float64)
    render = np.asarray(render, dtype=np.float64)
    denom = float((255.0 - real).sum())
    if denom <= 0:
        return float("nan")
    return float((255.0 - render).sum()) / denom


def normalized_fill(masks: Iterable[np.ndarray]) -> float:
    """Median normalized fill fraction (ink px / box) over a set of masks.

    Masks are the aspect-normalized glyph masks GlyphScore produces, so this
    separates "more ink per glyph" (dot-gain) from "bigger glyphs" (scale).
    """
    fills = [float(np.asarray(m, bool).mean()) for m in masks if np.asarray(m).size]
    if not fills:
        return float("nan")
    return float(np.median(fills))


def _row_ink_bands(
    gray: np.ndarray, y0: int, y1: int, ink_thresh: float = 0.06
) -> list[tuple[int, int]]:
    """Row bands (contiguous inked-row runs) inside [y0, y1).

    A row is "inked" when its mean darkness exceeds ``ink_thresh`` of the
    band's peak. Bands separated by >=2 blank rows are split.
    """
    band = np.asarray(gray, np.float64)[y0:y1]
    prof = (255.0 - band).mean(axis=1)
    if prof.max() <= 0:
        return []
    inked = prof > (prof.max() * ink_thresh)
    rows = np.where(inked)[0]
    if rows.size == 0:
        return []
    out: list[list[int]] = [[int(rows[0]), int(rows[0])]]
    for r in rows[1:]:
        if r - out[-1][1] - 1 < 2:
            out[-1][1] = int(r)
        else:
            out.append([int(r), int(r)])
    return [(a + y0, b + y0) for a, b in out]


def cap_height_px(
    gray: np.ndarray, y0: int, y1: int, ink_thresh: float = 0.06
) -> float:
    """Band-snapped cap height: median height of inked row-bands in a region.

    The gap analysts' trustworthy figure (``gap_geometry.md`` Headline): band
    heights snap to the true text line extent, avoiding the leaked-neighbour
    error of a fixed window. Returns median band height in pixels.
    """
    bands = _row_ink_bands(gray, y0, y1, ink_thresh)
    heights = [b - a + 1 for a, b in bands if (b - a + 1) >= 3]
    if not heights:
        return float("nan")
    return float(np.median(heights))


def line_pitch_px(
    gray: np.ndarray, y0: int, y1: int, ink_thresh: float = 0.06
) -> float:
    """Median row-center spacing (line pitch) between inked bands in a region."""
    bands = _row_ink_bands(gray, y0, y1, ink_thresh)
    centers = [0.5 * (a + b) for a, b in bands if (b - a + 1) >= 3]
    if len(centers) < 2:
        return float("nan")
    diffs = np.diff(sorted(centers))
    # keep only plausible single-line steps (reject big gaps between blocks)
    med = float(np.median(diffs))
    keep = diffs[(diffs > 0.5 * med) & (diffs < 1.8 * med)]
    return float(np.median(keep)) if keep.size else med


def _autocorr_peak(sig: np.ndarray, lo: int, hi: int, min_rel: float = 0.2) -> float:
    """First strong autocorrelation peak of ``sig`` in lag range [lo, hi).

    Robust to a crammed layout (unlike band-splitting) because it reads the
    dominant *period* of the ink profile. Returns nan if no lag clears
    ``min_rel`` of the zero-lag energy.
    """
    x = np.asarray(sig, np.float64)
    x = x - x.mean()
    ac = np.correlate(x, x, "full")[len(x) - 1:]
    if ac[0] <= 0:
        return float("nan")
    ac = ac / ac[0]
    hi = min(hi, ac.size)
    if hi <= lo:
        return float("nan")
    seg = ac[lo:hi]
    if seg.size == 0 or seg.max() < min_rel:
        return float("nan")
    k = lo + int(np.argmax(seg))
    # parabolic (sub-pixel) refinement around the peak -- a 1-px integer lag
    # would wrongly fail a tight pitch band (real 44.8 vs render 45.0).
    if 0 < k < ac.size - 1:
        ym1, y0v, yp1 = ac[k - 1], ac[k], ac[k + 1]
        denom = ym1 - 2 * y0v + yp1
        if denom != 0:
            offset = 0.5 * (ym1 - yp1) / denom
            if -1.0 < offset < 1.0:
                return float(k + offset)
    return float(k)


def line_pitch_autocorr(
    gray: np.ndarray, y0: int, y1: int, lo: int = 20, hi: int = 80
) -> float:
    """Line pitch (px) = dominant period of the region's row-ink profile.

    Reproduces the true line pitch on both a crammed render and an airy scan,
    where band-splitting fails (adjacent crammed lines merge into one band).
    """
    prof = (255.0 - np.asarray(gray, np.float64)[y0:y1]).mean(axis=1)
    return _autocorr_peak(prof, lo, hi)


def interline_duty(
    gray: np.ndarray, y0: int, y1: int, frac: float = 0.35
) -> float:
    """Duty cycle = fraction of region rows inked above ``frac`` of the p99
    row-darkness -- the "leading crammed" tell (render ~0.80, real ~0.67).

    cap_height ~= duty * pitch, so the ratio of duties (at equal pitch) is the
    cap-height ratio the gap report names as the dominant vertical tell.
    """
    prof = (255.0 - np.asarray(gray, np.float64)[y0:y1]).mean(axis=1)
    if prof.size == 0:
        return float("nan")
    peak = np.percentile(prof, 99)
    if peak <= 0:
        return float("nan")
    return float((prof > frac * peak).mean())


def char_advance_autocorr(
    gray: np.ndarray, y0: int, y1: int, lo: int = 12, hi: int = 34,
    min_ink: int = 8,
) -> float:
    """Character advance width (px) = median per-row horizontal ink period.

    The horizontal analogue of :func:`line_pitch_autocorr`: for a monospace
    receipt each inked row's autocorrelation peaks at the character pitch.
    Measured identically on real and render, so the ratio is method-fair.
    """
    band = 255.0 - np.asarray(gray, np.float64)[y0:y1]
    peaks = []
    for row in band:
        if int((row > 40).sum()) < min_ink:
            continue
        p = _autocorr_peak(row, lo, hi)
        if np.isfinite(p):
            peaks.append(p)
    return float(np.median(peaks)) if peaks else float("nan")


# ---------------------------------------------------------------------------
# L2 -- geometry / pitch parity
# ---------------------------------------------------------------------------


def chamfer_distance(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    """Symmetric mean chamfer distance (pixels) between two binary masks.

    Mean over A's ink of the distance to the nearest B ink, symmetrized with
    B->A. Robust to uniform printer thickening (edges stay close even when
    area-IoU collapses), which is why the similarity research prefers it over
    raw IoU for thin strokes. Empty masks -> nan.
    """
    a = np.asarray(mask_a, bool)
    b = np.asarray(mask_b, bool)
    if not a.any() or not b.any():
        return float("nan")
    # distance_transform_edt gives distance to nearest False; invert so we get
    # distance to nearest ink.
    dt_b = ndimage.distance_transform_edt(~b)
    dt_a = ndimage.distance_transform_edt(~a)
    ab = float(dt_b[a].mean())
    ba = float(dt_a[b].mean())
    return 0.5 * (ab + ba)


def _boundary(mask: np.ndarray, band: int) -> np.ndarray:
    """Pixels within ``band`` of the mask contour (Boundary-IoU band)."""
    m = np.asarray(mask, bool)
    if not m.any():
        return np.zeros_like(m)
    eroded = ndimage.binary_erosion(m, iterations=band, border_value=0)
    return m & ~eroded


def boundary_iou(mask_a: np.ndarray, mask_b: np.ndarray, band: int = 2) -> float:
    """Boundary IoU (Cheng et al. CVPR21): IoU restricted to a contour band.

    More sensitive to boundary/shape error than mask IoU and not biased by
    uniform thickening of a filled interior. band ~ stroke width.
    """
    a = np.asarray(mask_a, bool)
    b = np.asarray(mask_b, bool)
    if not a.any() or not b.any():
        return float("nan")
    ba = _boundary(a, band)
    bb = _boundary(b, band)
    inter = float((ba & bb).sum())
    union = float((ba | bb).sum())
    return inter / union if union > 0 else float("nan")


def advance_width_px(boxes: Sequence[tuple[float, str]]) -> float:
    """Median advance width (px/char) over (width_px, text) tokens.

    Only multi-char alphanumeric tokens (>=4 chars) count, matching the gap
    report's definition; punctuation/short tokens are noisy.
    """
    vals = []
    for width, text in boxes:
        n = len([c for c in text if c.isalnum()])
        if n >= 4 and width > 0:
            vals.append(width / n)
    return float(np.median(vals)) if vals else float("nan")


def right_edge_std(x_rights: Sequence[float]) -> float:
    """Std (px) of a set of right-edge x-coordinates (price-column raggedness)."""
    xs = np.asarray(list(x_rights), float)
    if xs.size < 2:
        return float("nan")
    return float(xs.std())


def caption_height_ratio(render_h: float, real_h: float) -> float:
    """Ratio render/real of a caption line height (barcode caption gate)."""
    if not real_h:
        return float("nan")
    return float(render_h) / float(real_h)


# ---------------------------------------------------------------------------
# L3 -- texture / degradation parity
# ---------------------------------------------------------------------------


def stroke_core_stats(gray: np.ndarray, ink_mask: np.ndarray, erode: int = 1) -> dict:
    """Median luminance, std and CoV of stroke *cores* (eroded ink interior).

    Real thermal strokes are light and mottled (median L ~69, CoV ~0.35);
    a too-clean render sits dark and uniform (median L ~12, CoV ~0.06).
    """
    g = np.asarray(gray, np.float64)
    m = np.asarray(ink_mask, bool)
    if erode > 0:
        m = ndimage.binary_erosion(m, iterations=erode, border_value=0)
    vals = g[m]
    if vals.size < 8:
        return {"median_L": float("nan"), "std": float("nan"), "cov": float("nan"),
                "n": int(vals.size)}
    med = float(np.median(vals))
    std = float(vals.std())
    ink = 255.0 - vals  # CoV on ink (darkness), the physically meaningful signal
    cov = float(ink.std() / ink.mean()) if ink.mean() > 0 else float("nan")
    return {"median_L": med, "std": std, "cov": cov, "n": int(vals.size)}


def edge_transition_frac(
    gray: np.ndarray, region: Optional[np.ndarray] = None,
    lo: float = 64.0, hi: float = 192.0,
) -> float:
    """Fraction of (region) pixels in the mid-tone transition band [lo, hi].

    Soft thermal/scan edges spread ink across many intermediate grays
    (frac ~0.16); crisp vector edges jump black->white (frac ~0.04). A
    scale-free proxy for edge softness.
    """
    g = np.asarray(gray, np.float64)
    if region is not None:
        g = g[np.asarray(region, bool)]
    if g.size == 0:
        return float("nan")
    return float(((g >= lo) & (g <= hi)).mean())


def paper_clip_frac(gray: np.ndarray, thresh: float = 254.5) -> float:
    """Fraction of pixels clipped to white (>= thresh) -- the paper model.

    A scanner auto-level crushes paper grain to featureless 255 (real ~0.55);
    a textured render leaves it unclipped (render ~0.025).
    """
    g = np.asarray(gray, np.float64)
    return float((g >= thresh).mean())


def tone_emd(real: np.ndarray, render: np.ndarray, bins: int = 64) -> float:
    """1-D Wasserstein (EMD) between the two luminance histograms, gray levels."""
    r = np.asarray(real, np.float64).ravel()
    f = np.asarray(render, np.float64).ravel()
    hr, edges = np.histogram(r, bins=bins, range=(0, 255), density=True)
    hf, _ = np.histogram(f, bins=bins, range=(0, 255), density=True)
    cr = np.cumsum(hr) / hr.sum()
    cf = np.cumsum(hf) / hf.sum()
    return float(np.sum(np.abs(cr - cf)) * (255.0 / bins))


def kanungo_pattern_hist(binary: np.ndarray) -> np.ndarray:
    """Normalized 3x3 neighbourhood-pattern histogram (512 bins).

    Each interior pixel maps its 3x3 binary neighbourhood to one of 512
    codes; the histogram is the print-forensics texture signature Kanungo &
    Zheng match to fit degradation params (``research_printer.md`` s1/s7).
    """
    b = np.asarray(binary, bool).astype(np.int32)
    if b.shape[0] < 3 or b.shape[1] < 3:
        return np.zeros(512, np.float64)
    windows = sliding_window_view(b, (3, 3))  # (H-2, W-2, 3, 3)
    weights = (1 << np.arange(9)).reshape(3, 3)
    codes = np.tensordot(windows, weights, axes=([2, 3], [0, 1])).ravel()
    hist = np.bincount(codes, minlength=512).astype(np.float64)
    total = hist.sum()
    return hist / total if total > 0 else hist


def _pattern_hist_from_crops(crops: Sequence[np.ndarray]) -> np.ndarray:
    """Pooled, normalized 3x3 pattern histogram over a stack of binary crops."""
    acc = np.zeros(512, np.float64)
    for c in crops:
        b = np.asarray(c, bool).astype(np.int32)
        if b.shape[0] < 3 or b.shape[1] < 3:
            continue
        windows = sliding_window_view(b, (3, 3))
        weights = (1 << np.arange(9)).reshape(3, 3)
        codes = np.tensordot(windows, weights, axes=([2, 3], [0, 1])).ravel()
        acc += np.bincount(codes, minlength=512)
    total = acc.sum()
    return acc / total if total > 0 else acc


def kanungo_two_sample(
    crops_a: Sequence[np.ndarray],
    crops_b: Sequence[np.ndarray],
    n_perm: int = 200,
    seed: int = 0,
) -> dict:
    """Two-sample test on 3x3 pattern histograms (render crops vs real crops).

    Returns the L1 distance between the pooled pattern histograms and a
    permutation p-value (fraction of label-shuffled splits whose distance is
    >= the observed). p above the acceptance threshold => "fail to reject":
    the two crop sets are texturally indistinguishable (the L3 accept gate,
    analogous to GlyphScore). Small/degenerate inputs -> nan p.
    """
    a = [np.asarray(c, bool) for c in crops_a if np.asarray(c).size]
    b = [np.asarray(c, bool) for c in crops_b if np.asarray(c).size]
    if len(a) < 2 or len(b) < 2:
        return {"distance": float("nan"), "p_value": float("nan"),
                "n_a": len(a), "n_b": len(b)}
    ha = _pattern_hist_from_crops(a)
    hb = _pattern_hist_from_crops(b)
    observed = float(np.abs(ha - hb).sum())
    # permutation: pool, reshuffle labels, recompute distance
    pooled = a + b
    na = len(a)
    rng = np.random.default_rng(seed)
    ge = 0
    for _ in range(n_perm):
        idx = rng.permutation(len(pooled))
        pa = [pooled[i] for i in idx[:na]]
        pb = [pooled[i] for i in idx[na:]]
        d = float(np.abs(_pattern_hist_from_crops(pa) - _pattern_hist_from_crops(pb)).sum())
        if d >= observed:
            ge += 1
    p = (ge + 1) / (n_perm + 1)
    return {"distance": observed, "p_value": float(p), "n_a": na, "n_b": len(b)}


# ---------------------------------------------------------------------------
# L4 -- perceptual + task parity
# ---------------------------------------------------------------------------


def _gauss_kernel(sigma: float = 1.5, radius: int = 5) -> np.ndarray:
    x = np.arange(-radius, radius + 1)
    k = np.exp(-x * x / (2 * sigma * sigma))
    return k / k.sum()


def _blur(im: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    r = (len(kernel) - 1) // 2
    p = np.pad(im, ((r, r), (0, 0)), mode="reflect")
    w = sliding_window_view(p, len(kernel), axis=0)
    im = np.tensordot(w, kernel, axes=([2], [0]))
    p = np.pad(im, ((0, 0), (r, r)), mode="reflect")
    w = sliding_window_view(p, len(kernel), axis=1)
    return np.tensordot(w, kernel, axes=([2], [0]))


def ssim_map(a: np.ndarray, b: np.ndarray, L: float = 255.0) -> np.ndarray:
    """Per-pixel SSIM map (Gaussian 11-tap, sigma 1.5) -- the metrics.py port."""
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    k = _gauss_kernel()
    C1 = (0.01 * L) ** 2
    C2 = (0.03 * L) ** 2
    mu_a = _blur(a, k)
    mu_b = _blur(b, k)
    aa = _blur(a * a, k) - mu_a ** 2
    bb = _blur(b * b, k) - mu_b ** 2
    ab = _blur(a * b, k) - mu_a * mu_b
    return ((2 * mu_a * mu_b + C1) * (2 * ab + C2)) / (
        (mu_a ** 2 + mu_b ** 2 + C1) * (aa + bb + C2)
    )


def _downsample2(im: np.ndarray) -> np.ndarray:
    h = im.shape[0] // 2 * 2
    w = im.shape[1] // 2 * 2
    return im[:h, :w].reshape(h // 2, 2, w // 2, 2).mean((1, 3))


def ms_ssim(a: np.ndarray, b: np.ndarray, levels: int = 5) -> float:
    """Multi-scale SSIM, 5-scale standard weights.

    NOTE: this is the ``metrics.py`` port used to produce the committed Costco
    reference (raw MS-SSIM 0.32328...), so it is kept bit-faithful to that
    implementation on purpose -- it uses the full SSIM-map mean at each scale
    as the contrast-structure proxy rather than the textbook CS-only term.
    Changing the formula would break the required reproduction of the gap
    report's numbers. All powered components are clamped non-negative so a
    negative SSIM cannot produce NaN via a fractional power.
    """
    weights = np.array([0.0448, 0.2856, 0.3001, 0.2363, 0.1333])
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    mcs = []
    lssim = 1.0
    for i in range(levels):
        s = ssim_map(a, b)
        if i < levels - 1:
            mcs.append(max(s.mean(), 1e-6))
            a = _downsample2(a)
            b = _downsample2(b)
        else:
            lssim = max(s.mean(), 1e-6)  # clamp: no NaN from a negative base
    mcs = np.array(mcs)
    val = np.prod(mcs ** weights[: levels - 1]) * (lssim ** weights[levels - 1])
    return float(val)


def _levenshtein(a: str, b: str) -> int:
    """Edit distance (ins/del/sub) between two strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def char_error_rate(ref: str, hyp: str) -> float:
    """True character error rate: Levenshtein(ref, hyp) / len(ref)."""
    if not ref:
        return float("nan")
    return _levenshtein(ref, hyp) / len(ref)


def _tokens(lines: Sequence[dict], row_q: float = 25.0) -> list[str]:
    """Reading-order token stream from OCR line dicts ({text,x,y})."""
    ordered = sorted(lines, key=lambda l: (round(l["y"] / row_q), l["x"]))
    joined = " ".join(l["text"] for l in ordered)
    return re.findall(r"\S+", joined)


def _align_subs(ref: Sequence[str], hyp: Sequence[str]):
    sm = SequenceMatcher(None, list(ref), list(hyp))
    nok = 0
    subs: dict[str, str] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            nok += i2 - i1
        elif tag == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                r = ref[i1 + k] if i1 + k < i2 else ""
                h = hyp[j1 + k] if j1 + k < j2 else ""
                if r and r != h:
                    subs.setdefault(r, h)
        elif tag == "delete":
            for x in range(i1, i2):
                subs.setdefault(ref[x], "")
    return nok, subs


def ocr_error_sets(gt: Sequence[str], real_lines, render_lines) -> dict:
    """OCR error-set parity: Jaccard of the mis-read GT-token sets, plus
    per-image token recall and char-error-rate vs ground truth.

    A realistic render makes the *same* OCR errors as the real scan
    (Jaccard->1); a too-clean/artefact-laden render adds its own error class
    (a superset). (``research_metrics.md`` axis-2 / ``gap_perceptual.md`` s6.)
    """
    gt = list(gt)
    rt = _tokens(real_lines)
    ft = _tokens(render_lines)
    rok, rsubs = _align_subs(gt, rt)
    fok, fsubs = _align_subs(gt, ft)

    def cer(ref: Sequence[str], hyp: Sequence[str]) -> float:
        return char_error_rate(" ".join(ref), " ".join(hyp))

    shared = set(rsubs) & set(fsubs)
    either = set(rsubs) | set(fsubs)
    jacc = len(shared) / len(either) if either else float("nan")
    return {
        "jaccard": float(jacc),
        "real_recall": rok / len(gt) if gt else float("nan"),
        "render_recall": fok / len(gt) if gt else float("nan"),
        "real_cer": cer(gt, rt),
        "render_cer": cer(gt, ft),
        "shared": sorted(shared),
        "render_only": sorted(set(fsubs) - set(rsubs)),
        "real_only": sorted(set(rsubs) - set(fsubs)),
    }


def glcm_contrast(gray: np.ndarray, levels: int = 16, dx: int = 1, dy: int = 0) -> float:
    """GLCM contrast at one offset -- a scalar texture descriptor.

    A cheap, model-free stand-in for the printer-source texture cluster
    (``research_forgery.md`` 3.4): quantize to ``levels`` grays, build the
    co-occurrence matrix at offset (dy,dx), return sum p*(i-j)^2. Compared
    render-vs-real it flags "too smooth" (low contrast) fills.
    """
    g = np.asarray(gray, np.float64)
    q = np.clip((g / 256.0 * levels).astype(int), 0, levels - 1)
    a = q[: q.shape[0] - dy if dy else q.shape[0], : q.shape[1] - dx if dx else q.shape[1]]
    b = q[dy:, dx:]
    a = a[: b.shape[0], : b.shape[1]]
    idx = a.ravel() * levels + b.ravel()
    glcm = np.bincount(idx, minlength=levels * levels).astype(np.float64).reshape(levels, levels)
    glcm = glcm + glcm.T
    total = glcm.sum()
    if total <= 0:
        return float("nan")
    glcm /= total
    i = np.arange(levels)[:, None]
    j = np.arange(levels)[None, :]
    return float((glcm * (i - j) ** 2).sum())


def spacing_jitter(centers_by_line: Sequence[Sequence[float]]) -> float:
    """Sub-cell spacing-jitter statistic across text lines.

    For each line, the std of successive glyph-center gaps normalized by the
    line's median gap; averaged over lines. Mechanically-perfect grid spacing
    -> ~0 (a named forensic tell, ``research_forgery.md`` 2.8); real print
    carries micro-variation. Reported so a render can be checked for having
    *some* jitter without inventing artificial drift.
    """
    per_line = []
    for centers in centers_by_line:
        c = np.asarray(sorted(centers), float)
        if c.size < 3:
            continue
        gaps = np.diff(c)
        med = np.median(gaps)
        if med <= 0:
            continue
        per_line.append(float(gaps.std() / med))
    if not per_line:
        return float("nan")
    return float(np.mean(per_line))


# ---------------------------------------------------------------------------
# Alignment (OCR-anchored piecewise-linear vertical warp)
# ---------------------------------------------------------------------------


def _clean_text(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()


def ocr_anchors(
    real_lines: Sequence[dict],
    render_lines: Sequence[dict],
    min_sim: float = 0.85,
    min_len: int = 4,
) -> list[tuple[float, float, float]]:
    """OCR line-box correspondences (real_cy, render_cy, similarity).

    Matches real OCR lines to render OCR lines by cleaned-text similarity
    (>= ``min_sim``), 1:1 greedily, then slope-outlier rejects to a monotone
    anchor set (the ``match2.py`` recipe). Coordinates are line-center y.
    """
    def prep(lines):
        out = []
        for l in lines:
            out.append({"cy": l["y"] + l["h"] / 2.0, "text": l["text"]})
        out.sort(key=lambda x: x["cy"])
        return out

    R = prep(real_lines)
    F = prep(render_lines)
    used = [False] * len(F)
    anchors: list[tuple[float, float, float]] = []
    for r in R:
        rc = _clean_text(r["text"])
        if len(rc) < min_len:
            continue
        best = (-1.0, -1)
        for j, f in enumerate(F):
            if used[j]:
                continue
            fc = _clean_text(f["text"])
            if len(fc) < min_len:
                continue
            s = SequenceMatcher(None, rc, fc).ratio()
            if s > best[0]:
                best = (s, j)
        if best[1] >= 0 and best[0] >= min_sim:
            used[best[1]] = True
            anchors.append((r["cy"], F[best[1]]["cy"], best[0]))
    anchors.sort()

    # slope-outlier rejection (successive slope must be plausible)
    def bad_indices(a):
        bad = []
        for i in range(1, len(a)):
            dy_r = a[i][0] - a[i - 1][0]
            dy_f = a[i][1] - a[i - 1][1]
            if dy_r <= 0:
                continue
            s = dy_f / dy_r
            if s < 0.55 or s > 1.8:
                bad.append(i)
        return bad

    a = anchors[:]
    for _ in range(200):
        bad = bad_indices(a)
        if not bad:
            break
        from collections import Counter

        c: Counter = Counter()
        for i in bad:
            c[i] += 1
            c[i - 1] += 1
        worst = max(c, key=lambda k: c[k])
        a.pop(worst)
    return a


def piecewise_row_warp(
    render_gray: np.ndarray, anchors: Sequence[tuple[float, float, float]], H: int
):
    """Warp render into the real frame by vertical piecewise-linear map.

    ``anchors`` are (real_cy, render_cy, sim). Returns (warped_render,
    residual_std). residual_std is the std of the linear-fit residual of the
    anchors -- the "geometry not matched" flag driver.
    """
    render_gray = np.asarray(render_gray, np.float64)
    if len(anchors) < 2:
        return render_gray.copy(), float("inf")
    Hr = render_gray.shape[0]  # clip source rows against the RENDER's height
    yr = np.array([a[0] for a in anchors], float)
    yf = np.array([a[1] for a in anchors], float)
    # linear-fit residual std (reported)
    A = np.vstack([yr, np.ones_like(yr)]).T
    coef, *_ = np.linalg.lstsq(A, yf, rcond=None)
    resid = yf - A @ coef
    resid_std = float(resid.std())
    # extend anchors to the edges, then interpolate a source-row map
    yr_e = np.concatenate([[0.0], yr, [H - 1.0]])
    yf_e = np.concatenate(
        [[max(0.0, yf[0] - yr[0])], yf, [min(Hr - 1.0, yf[-1] + (Hr - 1 - yr[-1]))]]
    )
    rows = np.arange(H)
    srcf = np.interp(rows, yr_e, yf_e)
    f0 = np.clip(np.floor(srcf).astype(int), 0, Hr - 1)
    f1 = np.clip(f0 + 1, 0, Hr - 1)
    w1 = (srcf - f0)[:, None]
    warped = render_gray[f0] * (1 - w1) + render_gray[f1] * w1
    return warped, resid_std

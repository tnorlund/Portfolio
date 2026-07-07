"""Inlier-refine a letterform corpus before tracing.

Pooled corpora (photos, multi-size faces, loose OCR boxes) stack outlier
samples into the consensus: neighbor letters ghost into wide crops, a second
font face doubles every stroke, and skewed photos smear caps. The tracer then
faithfully vectorizes the mush. This pass keeps, per char, only the largest
mutually-agreeing sample cluster so consensus is built from one letterform.

Method (pure numpy): binarize each sample, compute IoU of every sample
against the stack's soft median shape, take the best-agreeing half as a seed,
rebuild the seed's consensus, then keep every sample whose IoU against that
consensus clears an adaptive floor. Neighbor-letter ink lives in the crop
margins, so IoU is computed on the central band around the ink's mass column.

Usage:
  python -m glyphstudio.refine <in.samples.npz> <out.samples.npz> [--min-keep 5]
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

# A sample must overlap the seed consensus at least this much to survive.
# Thermal jitter keeps same-letterform IoU around 0.5-0.7; a different
# letterform, size, or neighbor-polluted crop falls well under 0.35.
IOU_FLOOR = 0.35
# Never refine below this many samples; a tiny agreeing core beats none.
MIN_KEEP_DEFAULT = 5


def _binary(stack: np.ndarray) -> np.ndarray:
    return stack.astype(bool)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter) / float(union) if union else 0.0


def _central_band(stack: np.ndarray) -> tuple[int, int]:
    """Column window around the median sample's ink mass (neighbor ink lives
    in the margins of wide crops)."""
    soft = stack.mean(0)
    cols = soft.sum(0)
    total = cols.sum()
    if total <= 0:
        return 0, stack.shape[2]
    cum = np.cumsum(cols)
    lo = int(np.searchsorted(cum, 0.02 * total))
    hi = int(np.searchsorted(cum, 0.98 * total)) + 1
    pad = max(2, (hi - lo) // 8)
    return max(0, lo - pad), min(stack.shape[2], hi + pad)


def refine_char(stack: np.ndarray, min_keep: int = MIN_KEEP_DEFAULT
                ) -> tuple[np.ndarray, dict]:
    n = stack.shape[0]
    if n <= min_keep:
        return stack, {"kept": n, "total": n, "floor": None}
    x0, x1 = _central_band(stack)
    band = stack[:, :, x0:x1]
    soft = band.mean(0)
    ref = soft >= 0.5
    if not ref.any():
        # sparse/blurry consensus: match total ink area instead
        thr = float(np.quantile(soft[soft > 0], 0.75)) if (soft > 0).any() else 0.5
        ref = soft >= thr
    scores = np.array([_iou(band[i], ref) for i in range(n)])
    # seed = best-agreeing half, rebuilt into a cleaner reference
    order = np.argsort(-scores)
    seed = band[order[: max(min_keep, n // 2)]]
    ref2 = seed.mean(0) >= 0.5
    if ref2.any():
        scores = np.array([_iou(band[i], ref2) for i in range(n)])
    keep = scores >= IOU_FLOOR
    if keep.sum() < min_keep:
        keep = np.zeros(n, bool)
        keep[np.argsort(-scores)[:min_keep]] = True
    return stack[keep], {
        "kept": int(keep.sum()),
        "total": n,
        "floor": IOU_FLOOR,
        "median_iou": round(float(np.median(scores)), 3),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--min-keep", type=int, default=MIN_KEEP_DEFAULT)
    args = ap.parse_args(argv)

    out: dict[str, np.ndarray] = {}
    report = []
    with np.load(args.src, allow_pickle=False) as data:
        for key in data.files:
            stack = data[key].astype(bool)
            kept, info = refine_char(stack, args.min_keep)
            out[key] = kept
            if info["kept"] < info["total"]:
                report.append((chr(int(key)), info))
    np.savez_compressed(args.dst, **out)
    report.sort(key=lambda t: t[1]["kept"] / t[1]["total"])
    for ch, info in report:
        print(f"  {ch!r}: kept {info['kept']}/{info['total']}"
              f" (median IoU {info.get('median_iou')})")
    print(f"wrote {args.dst}: {len(out)} chars,"
          f" {sum(1 for _, i in report if i['kept'] < i['total'])} refined")
    return 0


if __name__ == "__main__":
    sys.exit(main())

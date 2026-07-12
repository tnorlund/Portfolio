"""Fitted physical print+scan degradation model (GOLD_STANDARD.md I1).

Transforms a clean deterministic render into a printed-then-scanned-looking
image through the ordered physical chain of ``research_printer.md``:

    ink = 1 - L/255
      -> (1) GEOMETRY   per-row sub-pixel jitter (head/transport wobble)
      -> (2) DOT GAIN   anisotropic Gaussian heat/ink spread + gain
      -> (3) TRC        logistic tone-reproduction curve (paper activation)
      -> (4) NOISE      multiplicative correlated mottle (thermal density
                        variation) + Kanungo boundary flips (edge dropout /
                        bleed specks, distance-decayed)
      -> (5) SCAN       optical+scanner Gaussian PSF + sensor noise
      -> (6) LEVELS     scanner auto-level white clip (paper -> 255)
      -> observed grayscale

Every stage is analytic (numpy/scipy) and cheap; the whole chain runs in
well under a second on a full 760x2999 receipt. **No parameter here is
hand-tuned**: values are FITTED against real per-merchant crops and scan
texture statistics by ``fit_degrade.py`` (CMA-ES over the 17-vector, staged
for identifiability) and shipped as a params JSON next to the merchant's
other profile artifacts. The stochastic stages draw from a seeded
``np.random.default_rng``, so a (params, seed) pair is deterministic.
"""

from __future__ import annotations

import json
from typing import Mapping

import numpy as np
from scipy import ndimage

# The fitted parameter vector, in canonical order. Bounds are the physically
# plausible ranges from research_printer.md s7 (identifiability guardrails);
# the FIT chooses the values, the bounds only keep it on physical ground.
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    # (1) geometry: per-row horizontal jitter, px std / row-correlation px
    "jitter_sigma": (0.0, 1.5),
    "jitter_corr": (0.5, 12.0),
    # (2) mechanical dot gain: anisotropic spread (px) + gain amplification
    "gain_sx": (0.05, 2.5),
    "gain_sy": (0.05, 2.5),
    "gain_gamma": (0.8, 3.0),
    # (3) logistic TRC: activation midpoint / steepness, max ink density
    "trc_mid": (0.05, 0.9),
    "trc_slope": (2.0, 40.0),
    "d_max": (0.4, 1.0),
    # (4) spatial noise: mottle amplitude / correlation, Kanungo flips
    "mottle_sigma": (0.0, 0.8),
    "mottle_scale": (0.3, 6.0),
    "k_a0": (0.0, 1.0),
    "k_decay": (0.05, 5.0),
    "k_b0": (0.0, 0.5),
    "k_eta": (0.0, 0.05),
    # (5) scan: optical/scanner PSF sigma (px), sensor noise (gray levels)
    "psf_sigma": (0.2, 2.5),
    "sensor_sigma": (0.0, 12.0),
    # (6) auto-level white point (gray level mapped to 255)
    "white_point": (235.0, 255.0),
}

PARAM_ORDER = list(PARAM_BOUNDS)


def _validate(params: Mapping[str, float]) -> dict[str, float]:
    missing = [k for k in PARAM_ORDER if k not in params]
    if missing:
        raise ValueError(f"degrade params missing {missing}")
    out = {}
    for k in PARAM_ORDER:
        lo, hi = PARAM_BOUNDS[k]
        out[k] = float(np.clip(float(params[k]), lo, hi))
    return out


def degrade(gray: np.ndarray, params: Mapping[str, float], seed: int = 0) -> np.ndarray:
    """Apply the fitted print+scan chain to a grayscale image (0..255).

    Returns float64 in [0, 255]. Deterministic for a (params, seed) pair.
    """
    p = _validate(params)
    g = np.asarray(gray, np.float64)
    H, W = g.shape
    rng = np.random.default_rng(seed)
    ink = np.clip((255.0 - g) / 255.0, 0.0, 1.0)

    # (1) per-row sub-pixel horizontal jitter, correlated down the page
    if p["jitter_sigma"] > 1e-3:
        shifts = rng.standard_normal(H)
        shifts = ndimage.gaussian_filter1d(shifts, p["jitter_corr"])
        sd = shifts.std()
        if sd > 1e-9:
            shifts = shifts / sd * p["jitter_sigma"]
            rows = np.repeat(np.arange(H, dtype=np.float64)[:, None], W, axis=1)
            cols = np.arange(W, dtype=np.float64)[None, :] - shifts[:, None]
            ink = ndimage.map_coordinates(
                ink, [rows, cols], order=1, mode="nearest"
            )

    # (2) mechanical dot gain: heat spreads beyond the addressed dot
    spread = ndimage.gaussian_filter(ink, (p["gain_sy"], p["gain_sx"]))
    a2 = np.clip(p["gain_gamma"] * spread, 0.0, 1.0)

    # (3) logistic activation TRC, normalized to [0, d_max]
    mid, slope = p["trc_mid"], p["trc_slope"]
    d = 1.0 / (1.0 + np.exp(-slope * (a2 - mid)))
    d0 = 1.0 / (1.0 + np.exp(slope * mid))
    d1 = 1.0 / (1.0 + np.exp(-slope * (1.0 - mid)))
    d = np.clip((d - d0) / max(d1 - d0, 1e-9), 0.0, 1.0) * p["d_max"]

    # (4a) intra-stroke mottle: multiplicative correlated density variation
    if p["mottle_sigma"] > 1e-3:
        field = ndimage.gaussian_filter(
            rng.standard_normal((H, W)), p["mottle_scale"]
        )
        sd = field.std()
        if sd > 1e-9:
            d = np.clip(d * (1.0 + p["mottle_sigma"] * field / sd), 0.0, p["d_max"])

    # (4b) Kanungo boundary flips: dropout inside ink edges, specks outside.
    # Grayscale application of the binary flip (density -> 0 / -> d_max); the
    # scan PSF below correlates and softens the flipped pixels.
    m = d >= 0.5 * p["d_max"]
    if m.any() and (p["k_a0"] > 0 or p["k_b0"] > 0 or p["k_eta"] > 0):
        dt_in = ndimage.distance_transform_edt(m)
        dt_out = ndimage.distance_transform_edt(~m)
        p_drop = p["k_a0"] * np.exp(-p["k_decay"] * dt_in**2) + p["k_eta"]
        p_add = p["k_b0"] * np.exp(-p["k_decay"] * dt_out**2) + p["k_eta"]
        u = rng.random((H, W))
        drop = m & (u < p_drop)
        add = (~m) & (u < p_add)
        d = np.where(drop, 0.0, d)
        d = np.where(add, p["d_max"], d)

    # (5) optical + scanner PSF, then sensor noise
    d = ndimage.gaussian_filter(d, p["psf_sigma"])
    L = 255.0 * (1.0 - d)
    if p["sensor_sigma"] > 1e-3:
        L = L + rng.standard_normal((H, W)) * p["sensor_sigma"]

    # (6) scanner auto-level: white_point -> 255 (clips paper grain to white)
    L = np.clip(L * (255.0 / p["white_point"]), 0.0, 255.0)
    return L


def load_params(path: str) -> dict[str, float]:
    """Load a fitted params JSON ({"params": {...}} or a flat dict)."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return _validate(doc.get("params", doc))


def degrade_image_file(
    src: str, dst: str, params_path: str, seed: int = 0
) -> str:
    """CLI/pipeline entry: degrade an image file with a fitted params JSON."""
    from PIL import Image

    params = load_params(params_path)
    img = Image.open(src)
    gray = np.asarray(img.convert("L"), np.float64)
    out = degrade(gray, params, seed=seed)
    res = Image.fromarray(np.round(out).astype(np.uint8), "L").convert("RGB")
    # lossless for webp: the scorecard must measure the model, not encoder
    # artifacts stacked on top of it.
    kw = {"lossless": True} if dst.lower().endswith(".webp") else {}
    res.save(dst, **kw)
    return dst


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--params", required=True)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    degrade_image_file(args.src, args.dst, args.params, seed=args.seed)
    print(args.dst)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

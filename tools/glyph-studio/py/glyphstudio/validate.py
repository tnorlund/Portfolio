"""Render-free validation of the calibrate measurer's assumptions on real atlases.

M1/M2 (``glyphstudio.calibrate``) rest on three structural claims about how a
real atlas responds to the render knobs. This harness checks them on any real
``.glyphs.npz`` WITHOUT a receipt render -- so the cheap measurer's premises can
be confirmed on the shipped merchant fonts, and a merchant whose response
violates them (e.g. an atlas too heavy to erode into range) is flagged before it
is calibrated:

1. **Erosion saturates.** Density stops falling by ~thin 0.4-0.5 because
   ``thin_ink_mask``'s drop-period floors at 2. The render-time bisection probes
   up to 0.6 blindly; if the cheap curve is already flat there, those probes are
   wasted -- and a merchant pinned AT the ceiling (Wild Fork 0.6) is railed, not
   optimal.
2. **Monotonicity.** Density is non-increasing in ``thin`` (erosion) and
   non-decreasing in ``weight_iters`` (dilation) -- the property the joint solver
   relies on to pick a weight then fine-tune thin.
3. **Cap-height linearity.** Rendered cap height is ~linear in ``cap_px`` (the
   M2 two-point-fit premise): doubling cap_px ~doubles the cap glyph height.

This is the M1/M2 validation milestone's render-free half. The other half --
absolute agreement between a cheap solve and a real render -- necessarily needs
the render path and is measured against the scorecard (see calibrate's "Scope"
note). Run: ``python -m glyphstudio.validate <atlas.npz> [--cap-px N]``.
"""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from . import calibrate


def atlas_corpus_text(font) -> str:
    """A merchant-representative corpus: each atlas glyph once.

    Real receipt words aren't always on hand at validation time, and the claims
    checked here (saturation, monotonicity, linearity) are about the density
    *shape*, which is robust to the exact glyph mix. Using every atlas glyph
    once gives a stable, coverage-complete corpus. Spaces are skipped.
    """
    return "".join(c for c in sorted(font.glyphs) if not c.isspace())


def saturation_thin(
    curve: Sequence[tuple[float, float]], tol: float = 1e-3
) -> float:
    """Smallest thin at/after which density is flat within ``tol`` to the end.

    ``curve`` is ``[(thin, density)]`` sorted by thin. Returns the thin where
    erosion stops mattering (the plateau the render-time bisection should stop
    at); the last thin if it never flattens.
    """
    pts = sorted(curve)
    for i in range(len(pts)):
        base = pts[i][1]
        if all(abs(pts[j][1] - base) <= tol for j in range(i, len(pts))):
            return pts[i][0]
    return pts[-1][0] if pts else 0.0


def validate_atlas(
    font,
    cap_px: int = 40,
    *,
    text: str | None = None,
    thins: Sequence[float] = tuple(round(0.05 * k, 2) for k in range(13)),
    max_weight_iters: int = 4,
    linear_tol: float = 0.06,
    sat_tol: float = 1e-3,
) -> dict:
    """Check the three M1/M2 structural claims on ``font``; return a report dict.

    ``thins`` sweeps 0.0..0.6 by default (the render-time bisection's range).
    ``linear_tol`` is the allowed relative error on the cap-height doubling test.
    The report's ``ok`` is True iff all three claims hold.
    """
    text = text if text is not None else atlas_corpus_text(font)
    coverage = calibrate.text_glyph_coverage(font, text)

    curve = calibrate.thin_response_curve(font, text, cap_px, thins=thins)
    dens = [d for _, d in curve]
    monotone_thin = all(b <= a + 1e-9 for a, b in zip(dens, dens[1:]))
    eroded = bool(dens) and dens[-1] < dens[0] - 1e-9
    sat = saturation_thin(curve, tol=sat_tol)

    wdens = []
    for k in range(max_weight_iters + 1):
        d = calibrate.text_ink_density(font, text, cap_px, 0.0, weight_iters=k)
        if d is not None:
            wdens.append(d)
    monotone_weight = all(b >= a - 1e-9 for a, b in zip(wdens, wdens[1:]))
    thickened = bool(wdens) and wdens[-1] > wdens[0] + 1e-9

    h1 = calibrate.cap_glyph_height(font, cap_px, 0.0)
    h2 = calibrate.cap_glyph_height(font, 2 * cap_px, 0.0)
    cap_linear = (
        h1 is not None
        and h2 is not None
        and h1 > 0
        and abs(h2 - 2 * h1) <= linear_tol * (2 * h1)
    )

    ok = bool(
        monotone_thin
        and monotone_weight
        and eroded
        and thickened
        and cap_linear
    )
    return {
        "cap_px": cap_px,
        "coverage": coverage,
        "glyph_count": len(font.glyphs),
        "density_at_0": dens[0] if dens else None,
        "density_at_max_thin": dens[-1] if dens else None,
        "saturation_thin": sat,
        "monotone_thin": monotone_thin,
        "eroded": eroded,
        "monotone_weight": monotone_weight,
        "thickened": thickened,
        "cap_height_px": h1,
        "cap_height_2x": h2,
        "cap_linear": cap_linear,
        "thin_curve": curve,
        "ok": ok,
    }


def _load_font(npz_path: str):
    # calibrate._renderer inserts the sibling paths; reuse it, then import
    # BitmapFont the same way so validation needs no extra PYTHONPATH.
    calibrate._renderer()
    from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
        BitmapFont,
    )

    return BitmapFont(npz_path)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("atlas", help="path to a {name}.glyphs.npz atlas")
    ap.add_argument("--cap-px", type=int, default=40)
    ap.add_argument(
        "--json", action="store_true", help="emit the full report as JSON"
    )
    args = ap.parse_args(argv)

    font = _load_font(args.atlas)
    report = validate_atlas(font, args.cap_px)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"atlas: {args.atlas}  ({report['glyph_count']} glyphs)")
        print(f"  coverage           {report['coverage']:.3f}")
        print(
            f"  density 0 -> max    {report['density_at_0']:.3f} -> "
            f"{report['density_at_max_thin']:.3f}"
        )
        print(
            f"  saturation thin     {report['saturation_thin']:.2f}  "
            f"(erosion flat beyond)"
        )
        print(f"  monotone (thin)     {report['monotone_thin']}")
        print(f"  monotone (weight)   {report['monotone_weight']}")
        print(
            f"  cap linear          {report['cap_linear']}  "
            f"({report['cap_height_px']} -> {report['cap_height_2x']} px)"
        )
        print(f"  OK                  {report['ok']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

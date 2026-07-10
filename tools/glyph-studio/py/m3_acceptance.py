#!/usr/bin/env python3
"""M3 acceptance test: does a candidate atlas un-rail a density-railed merchant?

For each merchant: measure real-side scalars from its own dev scans (the
scorecard's `_ink_metrics` path), then run v1's render-free
`calibrate_merchant` with (a) the shipped solo atlas and (b) the candidate
atlas. Same corpus, same targets — only the font changes. A PASS is a derived
`bitmap_thin` in the interior (off the 0.0 floor / 0.40 saturation ceiling)
with projected density ≈ target.

Findings from the first run are in ../M3_FINDINGS.md (pooled family atlas
FAILED — denser than solo). Kept as the standing harness for future mint
candidates (e.g. density-calibrated mints).

Usage:
  python m3_acceptance.py CANDIDATE.glyphs.npz \
      [--merchant "Wild Fork:wildfork"] [--merchant "Costco Wholesale:costco"]
  (default merchants: Wild Fork + Costco Wholesale; each solo baseline is
   compiled fresh from fonts/<slug> into a temp dir)
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _p in (
    _HERE,
    os.path.join(_ROOT, "receipt_dynamo"),
    os.path.join(_ROOT, "synthesis_loop"),
    os.path.join(_ROOT, "receipt_agent"),
    os.path.join(_ROOT, "receipt_upload"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SATURATION = 0.40  # erosion saturates here on all real atlases (VALIDATION.md)


def ocr_overlap_score(words) -> int:
    """Count same-row word pairs whose x-intervals overlap >30%.

    Pathological OCR (doubled passes, fragmented tokens) stamps words on top
    of each other; the renderer reproduces that faithfully, inflating measured
    density. Same TEXT repeating on a row is legitimate (price + total
    columns) — only geometric x-overlap flags. Receipt 058b662d (Wild Fork)
    scores 44 on this metric and single-handedly created the 'WF rails ~48%
    too dense' artifact; clean receipts score 0-2.
    """
    rows: dict[float, list[tuple[float, float]]] = {}
    for w in words:
        bb = w.get("bbox") or ()
        if len(bb) != 4:
            continue
        y_center = round((bb[1] + bb[3]) / 2000.0, 2)
        x0, x1 = sorted((bb[0], bb[2]))
        rows.setdefault(y_center, []).append((x0, x1))
    bad = 0
    for spans in rows.values():
        spans.sort()
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            inter = min(a1, b1) - max(a0, b0)
            if inter > 0.3 * min(a1 - a0, b1 - b0):
                bad += 1
    return bad


def gather(client, merchant_name: str, n_receipts: int, max_overlaps: int = 2) -> dict:
    """Real-side scalars + scorecard corpus from the merchant's own scans.

    Receipts whose OCR is pathological (``ocr_overlap_score`` >
    ``max_overlaps``) are excluded — unvetted first-N sampling is how the
    Wild Fork density artifact contaminated both v1's and this epic's
    measurements.
    """
    from glyph_review import _ink_metrics
    from receipt_line_scorecard import _load_words_and_real

    places, _ = client.get_receipt_places_by_merchant(
        merchant_name=merchant_name, limit=50
    )
    receipts, h_meds, d_meds, word_h_px = [], [], [], []
    for p in places:
        if len(receipts) >= n_receipts:
            break
        try:
            real, words = _load_words_and_real(merchant_name, p.image_id, p.receipt_id)
        except Exception:  # noqa: BLE001 - missing image/receipt: skip
            continue
        overlaps = ocr_overlap_score(words)
        if overlaps > max_overlaps:
            print(
                f"  [vet] skipping {p.image_id[:8]}#{p.receipt_id}: "
                f"{overlaps} x-overlapping OCR pairs",
                file=sys.stderr,
            )
            continue
        m = _ink_metrics(real, words)
        if not m:
            continue
        h_meds.append(m["h_med"])
        d_meds.append(m["density_med"])
        height = real.size[1]
        for w in words:
            bb = w.get("bbox") or ()
            if len(bb) == 4:
                word_h_px.append(abs(bb[3] - bb[1]) / 1000.0 * height)
        receipts.append({"words": [{"text": w.get("text", "")} for w in words]})
    if not receipts:
        raise RuntimeError(f"no usable receipts for {merchant_name}")
    return {
        "receipts": receipts,
        "real_cap_height_px": float(np.median(h_meds)),
        "target_density": float(np.median(d_meds)),
        "median_ocr_word_height_px": float(np.median(word_h_px)),
        "n": len(receipts),
    }


def solve(font_path: str, g: dict):
    from glyphstudio.calibrate import calibrate_merchant

    from receipt_agent.agents.label_evaluator.rendering.bitmap_font import BitmapFont

    result = calibrate_merchant(
        BitmapFont(font_path),
        g["receipts"],
        real_cap_height_px=g["real_cap_height_px"],
        median_ocr_word_height_px=g["median_ocr_word_height_px"],
        target_density=g["target_density"],
    )
    thin = result.get("bitmap_thin")
    if thin is None:
        railed = "no-solve"
    elif thin >= SATURATION - 1e-6:
        railed = "CEILING-RAILED"
    elif thin <= 1e-6:
        railed = "FLOOR-RAILED"
    else:
        railed = "interior"
    return thin, result.get("projected") or {}, result.get("coverage"), railed


def _compile_solo(slug: str) -> str:
    """Compile fonts/<slug> to a temp npz (the shipped v1-refined baseline)."""
    from glyphstudio.compile import main as compile_main

    out = os.path.join(tempfile.gettempdir(), f"m3_acceptance_{slug}.glyphs.npz")
    compile_main([os.path.join(_ROOT, "tools", "glyph-studio", "fonts", slug), out])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("candidate", help="candidate .glyphs.npz to test")
    ap.add_argument(
        "--merchant",
        action="append",
        default=None,
        help='"Merchant Name:slug" pair; repeatable '
        "(default: 'Wild Fork:wildfork' and 'Costco Wholesale:costco')",
    )
    ap.add_argument("--receipts", type=int, default=4)
    ap.add_argument(
        "--min-coverage",
        type=float,
        default=0.90,
        help="PASS also requires candidate scorecard glyph coverage >= this",
    )
    ap.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"),
    )
    args = ap.parse_args(argv)
    os.environ["DYNAMODB_TABLE_NAME"] = args.table

    pairs = args.merchant or ["Wild Fork:wildfork", "Costco Wholesale:costco"]

    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(args.table)
    verdicts: dict[str, str] = {}
    for pair in pairs:
        name, _, slug = pair.partition(":")
        solo = _compile_solo(slug or name.lower().replace(" ", ""))
        print(f"\n===== {name} =====")
        g = gather(client, name, args.receipts)
        print(
            f"  real-side (n={g['n']}): cap={g['real_cap_height_px']:.1f}px "
            f"word_h={g['median_ocr_word_height_px']:.1f}px "
            f"target_density={g['target_density']:.4f}"
        )
        for label, path in (("SOLO shipped", solo), ("CANDIDATE", args.candidate)):
            thin, proj, cov, railed = solve(path, g)
            proj_s = (
                {k: round(v, 3) for k, v in proj.items()}
                if isinstance(proj, dict)
                else proj
            )
            print(
                f"  {label:13s} thin={thin} [{railed}] "
                f"projected={proj_s} coverage={cov and round(cov, 3)}"
            )
            if label == "CANDIDATE":
                verdicts[name] = (railed, cov or 0.0)
    # The epic's exit criterion is ALL railed merchants off the rails, not
    # any — and a candidate can't buy density by dropping glyphs, so PASS also
    # requires scorecard coverage >= --min-coverage per merchant.
    all_pass = bool(verdicts) and all(
        v == "interior" and c >= args.min_coverage for v, c in verdicts.values()
    )
    summary = ", ".join(f"{n}={v}(cov={c:.2f})" for n, (v, c) in verdicts.items())
    print(
        f"\nacceptance: {'PASS (all merchants un-railed)' if all_pass else 'FAIL'}"
        f" [{summary}]"
    )
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())

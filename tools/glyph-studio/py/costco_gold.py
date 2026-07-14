#!/usr/bin/env python3
"""costco_gold.py -- frozen Costco entry point for the gold-standard ladder.

This is a thin shim over the merchant-generalized ``merchant_gold.py``. It
exists so the committed Costco baseline record
(``baselines/costco_gold.baseline.*.json``) stays reproducible by a stable
command with a stable ``tool`` tag, even as ``merchant_gold.py`` grows to score
new merchants. Costco always supplies its specimen chart (``--chart``), so this
shim exercises the exact same code path the original monolithic
``costco_gold.py`` did; the output is byte-identical (see
``tests/test_costco_shim.py``).

Usage (identical to the original):
    costco_gold.py --render final.webp --real real.webp \\
        --labels final.labels.json --refpack costco.refpack.npz \\
        --chart bitMatrix-C2-chart.glyphs.npz --config costco.thresholds.json \\
        --out costco_gold.scorecard.json
"""

from __future__ import annotations

import argparse
import os
import sys

import merchant_gold

TOOL = "costco_gold.py"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--render", required=True)
    ap.add_argument("--real", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--refpack", required=True)
    ap.add_argument("--chart", required=True,
                    help="Costco specimen chart (bitMatrix-C2 glyphs.npz)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--out")
    ap.add_argument("--glyphscore-root",
                    default=os.environ.get("GLYPHSCORE_ROOT",
                                           "/private/tmp/glyphscore"))
    ap.add_argument("--cache-dir",
                    default=os.path.join(__import__("tempfile").gettempdir(),
                                         "costco_gold_cache"))
    ap.add_argument("--baseline",
                    help="prior scorecard JSON; flag metrics that regressed "
                         "(CI gate). Exit 2 on regression.")
    args = ap.parse_args(argv)
    # Freeze the Costco identity: merchant tag + tool tag the baseline expects.
    args.merchant = "costco"
    args.tool = TOOL
    return merchant_gold.run(args)


if __name__ == "__main__":
    sys.exit(main())

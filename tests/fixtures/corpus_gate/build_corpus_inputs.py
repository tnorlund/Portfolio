#!/usr/bin/env python3.12
"""Regenerate the committed corpus-gate eval-input bundles (offline).

Each bundle is the vendored, AWS-free stand-in for one receipt that
``corpus_regression_gate.py`` feeds to ``full_fidelity_eval.evaluate_pair``:
an OCR word manifest (``manifest_words`` -- what the "real" side prints) and
the synth word list (``syn_words`` -- what the render drew). The gate renders
a deterministic image pair from these words, so no real receipt / S3 image is
ever needed.

Words use full_fidelity_eval's renderer format: ``bbox`` is ``[x0, y0, x1,
y1]`` on a 0-1000 grid with a y-UP origin (y=1000 is the top of the receipt).
Rows are placed so that, for slugs with declared SECTION_BANDS (costco), each
line falls in its intended band.

Deterministic: re-running writes byte-identical JSON. Run from the repo root:

    python3.12 tests/fixtures/corpus_gate/build_corpus_inputs.py
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _word(text, x, y, width, labels=None, height=26):
    """A word box: left x, bottom y (renderer y-up), width -- all /1000."""
    return {
        "text": text,
        "line_id": 0,
        "word_id": 0,
        "bbox": [x, y, x + width, y + height],
        "labels": list(labels or []),
    }


def _row(y, cells):
    """A row at renderer-y ``y``; cells = list of (text, x, width, labels)."""
    return [_word(t, x, y, w, lbl) for (t, x, w, lbl) in cells]


def costco_words() -> list[dict]:
    """A small Costco-shaped receipt landing lines in their SECTION_BANDS.

    STOREFRONT ~910-1000, ITEMS ~450-910, SUMMARY ~340-450,
    PAYMENT ~180-340, FOOTER ~0-180 (renderer y-up).
    """
    rows = []
    # STOREFRONT
    rows += _row(950, [("COSTCO", 380, 240, [])])
    rows += _row(920, [("WHOLESALE", 360, 280, [])])
    # ITEMS (description left, line total right; a couple carry LINE_TOTAL)
    rows += _row(
        860, [("BANANAS", 120, 260, []), ("1.99", 720, 130, ["LINE_TOTAL"])]
    )
    rows += _row(
        820, [("MILK", 120, 170, []), ("3.49", 720, 130, ["LINE_TOTAL"])]
    )
    rows += _row(
        780, [("EGGS", 120, 160, []), ("4.29", 720, 130, ["LINE_TOTAL"])]
    )
    rows += _row(
        740, [("BREAD", 120, 200, []), ("2.50", 720, 130, ["LINE_TOTAL"])]
    )
    rows += _row(
        700, [("COFFEE", 120, 230, []), ("9.99", 720, 130, ["LINE_TOTAL"])]
    )
    # SUMMARY
    rows += _row(420, [("SUBTOTAL", 120, 300, []), ("22.26", 700, 150, [])])
    rows += _row(390, [("TAX", 120, 120, []), ("0.00", 720, 130, [])])
    rows += _row(360, [("TOTAL", 120, 200, []), ("22.26", 700, 150, [])])
    # PAYMENT
    rows += _row(300, [("VISA", 120, 160, []), ("22.26", 700, 150, [])])
    rows += _row(260, [("CHANGE", 120, 230, []), ("0.00", 720, 130, [])])
    # FOOTER
    rows += _row(120, [("THANK", 250, 200, []), ("YOU", 470, 150, [])])
    rows += _row(80, [("ITEMS", 250, 200, []), ("SOLD", 470, 180, [])])
    return rows


def fixture_mart_words() -> list[dict]:
    """A generic single-band receipt for the pure-fixture merchant."""
    rows = []
    rows += _row(950, [("FIXTURE", 340, 240, []), ("MART", 600, 170, [])])
    rows += _row(
        860, [("APPLES", 120, 230, []), ("3.99", 720, 130, ["LINE_TOTAL"])]
    )
    rows += _row(
        820, [("BREAD", 120, 200, []), ("2.50", 720, 130, ["LINE_TOTAL"])]
    )
    rows += _row(
        780, [("CHEESE", 120, 240, []), ("5.75", 720, 130, ["LINE_TOTAL"])]
    )
    rows += _row(
        740, [("JUICE", 120, 190, []), ("4.25", 720, 130, ["LINE_TOTAL"])]
    )
    rows += _row(600, [("SUBTOTAL", 120, 300, []), ("16.49", 700, 150, [])])
    rows += _row(560, [("TAX", 120, 120, []), ("0.00", 720, 130, [])])
    rows += _row(520, [("TOTAL", 120, 200, []), ("16.49", 700, 150, [])])
    rows += _row(400, [("CASH", 120, 160, []), ("16.49", 700, 150, [])])
    rows += _row(120, [("THANK", 300, 200, []), ("YOU", 520, 150, [])])
    return rows


BUNDLES = {
    "costco_wholesale_v1.inputs.json": {
        "slug": "costco",
        "composed": False,
        "section_sequence": [
            "STOREFRONT",
            "ITEMS",
            "SUMMARY",
            "PAYMENT",
            "FOOTER",
        ],
        "canvas": {"w": 460, "h": 900},
        "words": costco_words,
    },
    "fixture_mart_v1.inputs.json": {
        "slug": "fixture_mart",
        "composed": False,
        "section_sequence": [],
        "canvas": {"w": 420, "h": 700},
        "words": fixture_mart_words,
    },
}


def build_one(spec: dict) -> dict:
    words = spec["words"]()
    return {
        "slug": spec["slug"],
        "composed": spec["composed"],
        "section_sequence": spec["section_sequence"],
        "canvas": spec["canvas"],
        # clean baseline: the synth drew exactly the manifest (real) content
        "manifest_words": words,
        "syn_words": words,
    }


def main() -> int:
    for filename, spec in BUNDLES.items():
        out = os.path.join(HERE, filename)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(build_one(spec), fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

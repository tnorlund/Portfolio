"""Freeze stylescan's section classification over every committed fixture.

Collects line texts from the committed receipts and records, for every
stylescan merchant slug, the raw section ``stylescan._classify`` returns.
The goldens let a refactor of where the rules live (code vs
``fonts/<slug>/stylemap.json``) prove it changed no classification:

* ``section_classification_golden.json`` -- one row per unique ``(text,
  has_price)`` line with the raw section for each slug;
* ``section_rules_pin.json`` -- per slug, the ordered ``(section, pattern,
  flags)`` list ``stylescan.rules_for_merchant`` serves.

Line sources:

* ``fixtures/source_snapshots/*.json`` words grouped by ``line_id`` and,
  separately, into visual rows by bbox overlap;
* ``portfolio/public/synthetic-receipts/pipeline/*/final.labels.json``
  tokens grouped into visual rows by bbox overlap;
* every single word of both, and the literal texts the stylescan tests pass
  to ``_classify``.

Usage (from ``tools/glyph-studio/py``):
  python section_classification_golden.py [--check]
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import sys
from statistics import median

from glyphstudio import stylescan

PY_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(PY_DIR)
REPO_DIR = os.path.dirname(os.path.dirname(STUDIO_DIR))
SNAPSHOT_GLOB = os.path.join(
    STUDIO_DIR, "fixtures", "source_snapshots", "*.json"
)
PIPELINE_GLOB = os.path.join(
    REPO_DIR,
    "portfolio",
    "public",
    "synthetic-receipts",
    "pipeline",
    "*",
    "final.labels.json",
)
TESTS_DIR = os.path.join(PY_DIR, "tests")
FIXTURE_DIR = os.path.join(TESTS_DIR, "fixtures")
GOLDEN_PATH = os.path.join(FIXTURE_DIR, "section_classification_golden.json")
RULES_PIN_PATH = os.path.join(FIXTURE_DIR, "section_rules_pin.json")

# Every slug with merchant-specific rules as of the S2 rules move (the
# in-code ``_MERCHANT_RULES`` keys plus the stylemap-declared merchants),
# and one unknown slug to pin the Sprouts fallback.
SLUGS = (
    "sprouts",
    "gelsons",
    "costco",
    "vons",
    "traderjoes",
    "cvs",
    "innout",
    "target",
    "wildfork",
    "homedepot",
    "speedway",
    "wholefoods",
    "unknown-merchant",
)

_LINE_OVERLAP = 0.5
_FLAG_CHARS = ((re.IGNORECASE, "i"), (re.MULTILINE, "m"), (re.VERBOSE, "x"))


def _y_span(bbox) -> tuple[float, float]:
    y0, y1 = float(bbox[1]), float(bbox[3])
    return min(y0, y1), max(y0, y1)


def _overlap_frac(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    shorter = min(a[1] - a[0], b[1] - b[0])
    if shorter <= 0:
        return 1.0 if inter >= 0 else 0.0
    return max(0.0, inter) / shorter


def group_words_by_overlap(words: list[dict]) -> list[list[dict]]:
    """Words with a ``bbox`` -> visual rows by vertical bbox overlap.

    Same grouping as ``glyphstudio.label_role_audit``: a word joins the row
    whose median y-band it overlaps most (at least half the shorter
    height); rows keep input order, words sort left to right.
    """
    lines: list[list[dict]] = []
    bands: list[tuple[float, float]] = []
    for word in words:
        span = _y_span(word["bbox"])
        best, best_frac = None, _LINE_OVERLAP
        for li, band in enumerate(bands):
            frac = _overlap_frac(span, band)
            if frac >= best_frac:
                best, best_frac = li, frac
        if best is None:
            lines.append([word])
            bands.append(span)
            continue
        lines[best].append(word)
        spans = [_y_span(w["bbox"]) for w in lines[best]]
        bands[best] = (
            median(s[0] for s in spans),
            median(s[1] for s in spans),
        )
    for line in lines:
        line.sort(key=lambda w: float(min(w["bbox"][0], w["bbox"][2])))
    return lines


def snapshot_lines(snapshot: dict) -> list[list[dict]]:
    by_line: dict[int, list[dict]] = {}
    for word in snapshot.get("words", []):
        by_line.setdefault(int(word["line_id"]), []).append(word)
    return [
        sorted(by_line[k], key=lambda w: int(w.get("word_id", 0)))
        for k in sorted(by_line)
    ]


def _line_texts(lines: list[list[dict]]) -> list[list[str]]:
    return [[str(w.get("text") or "") for w in line] for line in lines]


def _test_literals() -> list[str]:
    """String literals the stylescan tests pass as ``_classify``'s text."""
    out: list[str] = []
    for path in sorted(glob.glob(os.path.join(TESTS_DIR, "test_*.py"))):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else None
            if isinstance(fn, ast.Name):
                name = fn.id
            arg = node.args[0]
            if name == "_classify" and isinstance(arg, ast.Constant):
                if isinstance(arg.value, str):
                    out.append(arg.value)
    return out


def collect_lines() -> list[tuple[str, bool]]:
    """Unique ``(text, has_price)`` pairs over every fixture, sorted."""
    word_lists: list[list[str]] = []
    for path in sorted(glob.glob(SNAPSHOT_GLOB)):
        with open(path, encoding="utf-8") as fh:
            snapshot = json.load(fh)
        by_id = snapshot_lines(snapshot)
        word_lists += _line_texts(by_id)
        word_lists += _line_texts(
            group_words_by_overlap([w for line in by_id for w in line])
        )
    for path in sorted(glob.glob(PIPELINE_GLOB)):
        with open(path, encoding="utf-8") as fh:
            labels = json.load(fh)
        words = [
            {"text": text, "bbox": list(bbox)}
            for text, bbox in zip(labels["tokens"], labels["bboxes"])
        ]
        word_lists += _line_texts(group_words_by_overlap(words))
    singles = [[w] for texts in word_lists for w in texts]
    literals = [[text] for text in _test_literals()]
    pairs: set[tuple[str, bool]] = set()
    for texts in word_lists + singles:
        pairs.add((" ".join(texts), stylescan.line_has_price(texts)))
    for (text,) in literals:
        pairs.add((text, False))
        pairs.add((text, True))
    return sorted(pairs)


def classify_row(text: str, has_price: bool) -> list[str]:
    """Raw ``_classify`` section of one line for every slug in ``SLUGS``."""
    return [stylescan._classify(text, has_price, slug) for slug in SLUGS]


def build_golden() -> dict:
    """``rows`` are ``[text, has_price, [section per slug in SLUGS]]``."""
    return {
        "generator": "tools/glyph-studio/py/section_classification_golden.py",
        "slugs": list(SLUGS),
        "rows": [
            [text, has_price, classify_row(text, has_price)]
            for text, has_price in collect_lines()
        ],
    }


def flag_chars(rx: re.Pattern) -> str:
    """``stylerules`` flag string for a compiled pattern's flags.

    Raises on any flag the stylemap schema cannot express (``re.UNICODE``
    is implicit for str patterns and ignored).
    """
    rest = rx.flags & ~re.UNICODE
    chars = ""
    for bit, ch in _FLAG_CHARS:
        if rest & bit:
            chars += ch
            rest &= ~bit
    if rest:
        raise ValueError(f"inexpressible flags {rest:#x} on {rx.pattern!r}")
    return chars


def build_rules_pin() -> dict:
    return {
        slug: [
            [section, rx.pattern, flag_chars(rx)]
            for section, rx in stylescan.rules_for_merchant(slug)
        ]
        for slug in SLUGS
    }


def _dump(obj: dict, path: str) -> None:
    """JSON with one top-level list element per line (diffable)."""
    parts = []
    for key, value in obj.items():
        head = json.dumps(key, ensure_ascii=False)
        if isinstance(value, dict):
            value = [[k, v] for k, v in value.items()]
            head += ": {"
            items = [
                f"  {json.dumps(k, ensure_ascii=False)}: "
                + json.dumps(v, ensure_ascii=False)
                for k, v in value
            ]
            parts.append(f" {head}\n" + ",\n".join(items) + "\n }")
        elif isinstance(value, list) and value:
            items = [f"  {json.dumps(v, ensure_ascii=False)}" for v in value]
            parts.append(f" {head}: [\n" + ",\n".join(items) + "\n ]")
        else:
            parts.append(f" {head}: {json.dumps(value, ensure_ascii=False)}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{\n" + ",\n".join(parts) + "\n}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="compare against the committed goldens instead of writing",
    )
    args = ap.parse_args(argv)
    golden, pin = build_golden(), build_rules_pin()
    if args.check:
        ok = True
        for obj, path in ((golden, GOLDEN_PATH), (pin, RULES_PIN_PATH)):
            with open(path, encoding="utf-8") as fh:
                if json.load(fh) != obj:
                    print(f"MISMATCH {path}")
                    ok = False
        return 0 if ok else 1
    _dump(golden, GOLDEN_PATH)
    _dump(pin, RULES_PIN_PATH)
    print(f"{len(golden['rows'])} lines x {len(SLUGS)} slugs -> {GOLDEN_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""layout_template v1: measured per-merchant layout data (#1188 P2).

Consumes a directory of stylescan outputs (which carry the columnscan
token-edge rider) and writes a ``layout_template`` block into the merchant's
entry in ``scripts/merchant_profiles.json``:

* ``columns``     -- pooled per-section column lanes
  ``{role, anchor, x, spread, support}`` (styleagg's pooling: per-receipt
  greedy 1-D clusters at 0.04 paper-width, cross-receipt nearest-x 0.03,
  support >= max(2, receipts/2)).
* ``sections``    -- the merchant's canonical section sequence, ordered by
  median first-appearance position across receipts.
* ``separators``  -- measured rule-row inventory: dominant char, the
  canonical section each rule follows, and receipt support.

This is SCHEMA + MEASURED DATA ONLY: nothing in the render path consumes it
yet (that is P3). The eval's column metric can read it via
``--columns-source profile``.

Usage:
  python -m glyphstudio.layout_template <profile_key> <scan_dir> [--dry-run]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from statistics import median

try:
    from .sections import normalize_stylescan_section
    from .styleagg import pool_columns, receipt_section_columns
except ImportError:  # bare-script invocation
    from sections import normalize_stylescan_section
    from styleagg import pool_columns, receipt_section_columns

_REPO = os.path.abspath(
    os.path.join(os.path.dirname(__file__), *[os.pardir] * 4)
)
PROFILES_PATH = os.path.join(_REPO, "scripts", "merchant_profiles.json")

# a separator/sequence observation must recur in >= max(2, receipts/2)
# receipts to be committed as merchant layout data.
SEPARATOR_CHARS = "*-=_~"


def _load_scans(scan_dir: str) -> list[dict]:
    scans = []
    for f in sorted(glob.glob(os.path.join(scan_dir, "*.json"))):
        if f.endswith(("receipts.json", "stylemap.json")):
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if d.get("lines"):
            scans.append(d)
    return scans


def _support_need(n_scans: int) -> int:
    """At least half the receipts, CEILING (3 of 5), floored at 2."""
    return max(2, -(-n_scans // 2))


def _line_y_frac(line: dict, scan: dict, i: int, n_lines: int) -> float:
    """Paper-position fraction of a line: pixel bbox center over image
    height when the scan carries it, else the line-index fraction."""
    bbox = line.get("bbox")
    size = scan.get("image_size")
    if bbox and size and len(bbox) == 4 and size[1]:
        return ((bbox[1] + bbox[3]) / 2.0) / float(size[1])
    return i / n_lines


def measure_section_sequence(scans: list[dict]) -> list[str]:
    """Canonical sections ordered by median first-appearance position."""
    firsts: dict[str, list[float]] = defaultdict(list)
    need = _support_need(len(scans))
    for scan in scans:
        lines = scan["lines"]
        n = max(1, len(lines))
        seen: set[str] = set()
        for i, line in enumerate(lines):
            sec = line.get("section_canonical") or normalize_stylescan_section(
                line.get("section")
            )
            if sec and sec not in seen:
                seen.add(sec)
                firsts[sec].append(_line_y_frac(line, scan, i, n))
    ordered = [
        (median(pos), sec) for sec, pos in firsts.items() if len(pos) >= need
    ]
    return [sec for _, sec in sorted(ordered)]


def _separator_char(text: str) -> str | None:
    counts = Counter(ch for ch in text if ch in SEPARATOR_CHARS)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def measure_separators(scans: list[dict]) -> list[dict]:
    """Rule-row inventory: (char, after_section, ordinal) observations with
    receipt support, ordered by their median position on the paper.

    ``ordinal`` distinguishes REPEATED rules after one section (two ``*``
    rows following the summary are two entries, ordinal 0 and 1), so the
    template can represent the real inventory, and support still counts
    receipts (each receipt contributes one observation per ordinal).
    """
    obs: dict[tuple[str, str | None, int], list[float]] = defaultdict(list)
    need = _support_need(len(scans))
    for scan in scans:
        lines = scan["lines"]
        n = max(1, len(lines))
        ordinals: Counter = Counter()
        prev_section: str | None = None
        for i, line in enumerate(lines):
            sec_raw = line.get("section")
            canonical = line.get(
                "section_canonical"
            ) or normalize_stylescan_section(sec_raw)
            # A rule row is identified by its TEXT, not its classified
            # section: merchant rules routinely swallow rule rows (gelsons'
            # ``^\*{5,}$`` footer rule beats the generic separator fallback).
            compact = str(line.get("text") or "").replace(" ", "")
            if sec_raw == "separator" or (
                len(compact) >= 6
                and all(ch in SEPARATOR_CHARS for ch in compact)
            ):
                char = _separator_char(str(line.get("text") or ""))
                if char:
                    base = (char, prev_section)
                    key = (char, prev_section, ordinals[base])
                    ordinals[base] += 1
                    obs[key].append(_line_y_frac(line, scan, i, n))
                continue
            if canonical:
                prev_section = canonical
        # a separator observed before any classified section anchors to None
    out = []
    for (char, after, ordinal), positions in obs.items():
        if len(positions) < need:
            continue
        out.append(
            {
                "char": char,
                "after_section": after,
                "ordinal": ordinal,
                "support": len(positions),
                "pos_frac_med": round(median(positions), 3),
            }
        )
    out.sort(key=lambda s: s["pos_frac_med"])
    return out


def build_layout_template(scans: list[dict]) -> dict:
    per_receipt = [receipt_section_columns(scan["lines"]) for scan in scans]
    sha = "unknown"
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()[:12]
    except Exception:  # noqa: BLE001
        pass
    return {
        "version": 1,
        "_comment": (
            "Measured layout data (#1188 P2): columns/sections/separators "
            "from real receipts via glyphstudio.layout_template. NOT yet "
            "consumed by the renderer (P3); full_fidelity_eval reads "
            "columns via --columns-source profile."
        ),
        "measured": {
            "receipts": len(scans),
            "tool_git_sha": sha,
        },
        "columns": pool_columns(per_receipt),
        "sections": measure_section_sequence(scans),
        "separators": measure_separators(scans),
    }


def write_layout_template(profile_key: str, template: dict) -> None:
    with open(PROFILES_PATH, encoding="utf-8") as fh:
        doc = json.load(fh)
    if profile_key not in doc["profiles"]:
        raise SystemExit(f"unknown merchant profile key {profile_key!r}")
    doc["profiles"][profile_key]["layout_template"] = template
    # ensure_ascii stays True: the existing file escapes non-ASCII (®),
    # so the rewrite diff touches only the layout_template block.
    with open(PROFILES_PATH, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile_key")
    ap.add_argument("scan_dir")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    scans = _load_scans(args.scan_dir)
    if len(scans) < 2:
        raise SystemExit(
            f"need >= 2 usable scans in {args.scan_dir}, found {len(scans)}"
        )
    template = build_layout_template(scans)
    print(json.dumps(template, indent=1))
    if not args.dry_run:
        write_layout_template(args.profile_key, template)
        print(f"written -> profiles[{args.profile_key!r}].layout_template")
    return 0


if __name__ == "__main__":
    sys.exit(main())

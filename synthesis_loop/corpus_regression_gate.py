#!/usr/bin/env python3.12
"""Standing eval-regression corpus gate (offline, zero AWS, PR-runner-safe).

Companion to ``render_regression_guard.py``. Where that guard pins the
production RENDERER's pixels (re-rendering shipped receipts through
``glyph_review`` -- which needs the dev table + S3 + creds), THIS gate pins
the EVAL LOGIC: it freezes ``full_fidelity_eval``'s seven metric verdicts and
their load-bearing numeric values over a corpus of committed offline fixtures,
so a change to a metric threshold, the merchant-truth loader, or the stylemap
classifier that silently flips a corpus receipt's verdict fails loudly in CI
-- with NO AWS credentials.

Why this cannot just shell out to ``full_fidelity_eval run --truth-fixture``:
that subcommand's ``--truth-fixture`` flag only makes the merchant-TRUTH
resolution offline. The receipt it evaluates is still fetched live --
``section_compare.render_pair`` reads the font profile, glyph atlas, receipt
words and the real S3 image from the dev table for EVERY run. Truth-fixture
mode is therefore NOT zero-AWS for ``run``, and one corpus merchant
(``fixture_mart``) is a pure fixture with no receipt in any table at all. So
the gate drives the eval's genuinely-offline pure core directly:
``resolve_truth(fixture_path=...)`` + ``evaluate_pair(...)``, feeding it
vendored receipt inputs (OCR word manifests) and deterministically-rendered
image pairs. No boto3, no DynamoDB, no S3. The default ``--write-gate-record
False`` is not even reachable here (the CLI ``run`` path is not used), so no
gate record is ever written.

The corpus:
  - ``corpus_manifest.json`` (beside this script) lists entries; each names a
    committed merchant-truth fixture (``tests/fixtures/merchant_truth/*.json``)
    plus a committed eval-input bundle
    (``tests/fixtures/corpus_gate/*.inputs.json`` -- the OCR word manifest and
    the synth word list). Adding a future merchant = add a truth fixture, add
    an inputs bundle, add a manifest entry, re-``capture``.
  - ``corpus_baseline.json`` (beside this script) is the committed baseline:
    per-entry metric verdicts, the relevant metric values, and render hashes.

Verbs (mirroring render_regression_guard's capture/compare/check):
  corpus_regression_gate.py capture [out_path]
      Run the offline eval over every manifest entry and WRITE the baseline
      (default: the committed ``corpus_baseline.json``). Re-capture and commit
      only for an intended, reviewed eval change.
  corpus_regression_gate.py compare <baseline_path> [--json]
      Fresh-capture and diff against ``baseline_path``; print findings.
  corpus_regression_gate.py check [--json]
      Fresh-capture and diff against the COMMITTED baseline. Exit nonzero on
      any regression. ``--json`` emits the findings as a machine-parseable
      document (``{"ok": bool, "findings": [...]}``) on stdout -- H7's CI
      comment poster consumes this directly.

Each finding names the receipt, the metric, the field, the baseline and
current values, and (for numerics) the delta -- e.g.
``{"receipt": "costco_wholesale_v1", "metric": "tokens", "field": "verdict",
"baseline": "PASS", "current": "FAIL"}``.

Intended CI invocation (wired in H7, NOT here):
    python3.12 synthesis_loop/corpus_regression_gate.py check --json
On a self-hosted PR runner with NO AWS credentials. Exit 0 = corpus stable;
exit 1 = a metric verdict/value/render-hash regressed (findings on stdout for
the comment poster); exit 2 = usage error.

Determinism: the eval core is pure over its inputs (median/numpy, no RNG). The
ONE nondeterminism source is metric 5 (graphics), which shells out to the
Swift ``receipt-ocr`` barcode detector whose availability varies by machine.
The gate PINS it OFF (``RECEIPT_OCR_BIN`` -> a guaranteed-missing path) so
graphics is deterministically ``SKIPPED`` on every runner and two same-commit
captures are byte-identical. The fixtures carry no barcodes, so nothing is
lost by pinning it off.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (HERE, os.path.join(REPO, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MANIFEST_PATH = os.path.join(HERE, "corpus_manifest.json")
COMMITTED_BASELINE = os.path.join(HERE, "corpus_baseline.json")

# The one pinned nondeterminism source (see module docstring): force the
# barcode detector missing so metric_graphics is deterministically SKIPPED.
# Set BEFORE full_fidelity_eval is imported (it reads the env at import time).
_MISSING_BARCODE_BIN = os.path.join(HERE, "__corpus_gate_no_barcode_bin__")
os.environ["RECEIPT_OCR_BIN"] = _MISSING_BARCODE_BIN

# Numeric fields equal within this are "unchanged" (rounding stability).
_EPS = 1e-6

_METRIC_ORDER = (
    "columns",
    "style",
    "tokens",
    "separators",
    "graphics",
    "logo",
    "arithmetic",
)


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _repo_path(rel: str) -> str:
    """Resolve a manifest-relative path against the repo root."""
    return rel if os.path.isabs(rel) else os.path.join(REPO, rel)


# ---------------------------------------------------------------------------
# deterministic offline render: OCR-format words -> a stable pixel image.
# This is NOT the production renderer (that needs AWS and is render_regression
# _guard's jurisdiction); it is a fixed, reproducible stand-in so evaluate_pair
# has real pixels to measure. The renderer-format bbox convention (0-1000,
# y-up) matches full_fidelity_eval.words_to_px.
# ---------------------------------------------------------------------------
def render_fixture_image(words: list[dict], w: int, h: int):
    import numpy as np
    from PIL import Image

    arr = np.full((h, w), 245, dtype=np.uint8)
    for word in words:
        bb = word.get("bbox")
        if not bb:
            continue
        x0, y0, x1, y1 = (float(v) for v in bb[:4])
        left = int(min(x0, x1) / 1000.0 * w)
        right = int(max(x0, x1) / 1000.0 * w)
        top = int((1 - max(y0, y1) / 1000.0) * h)
        bottom = int((1 - min(y0, y1) / 1000.0) * h)
        left = max(0, min(w, left))
        right = max(0, min(w, right))
        top = max(0, min(h, top))
        bottom = max(0, min(h, bottom))
        if right - left < 1 or bottom - top < 1:
            continue
        # Ink the glyph run, leaving a 1px inter-character comb so the column
        # and token ink-scans see structured strokes, not a solid bar.
        block = arr[top:bottom, left:right]
        block[:, ::2] = 30
    return Image.fromarray(arr, "L").convert("RGB"), arr


def _pixel_hash(arr) -> str:
    """SHA over the raw pixel buffer (PIL/zlib-version independent)."""
    return hashlib.sha256(arr.tobytes()).hexdigest()


# ---------------------------------------------------------------------------
# compact per-entry summary of an evaluate_pair result. Only the load-bearing
# scalars are recorded, so a baseline diff can name a metric + a numeric delta
# without carrying the eval's large nested arrays.
# ---------------------------------------------------------------------------
def _metric_summary(checks: dict) -> dict:
    col = checks["columns"]
    style = checks["style"]
    tokens = checks["tokens"]
    seps = checks["separators"]
    logo = checks["logo"]
    arith = checks["arithmetic"]
    return {
        "overall": checks["overall"],
        "coverage_gaps": sorted(checks.get("coverage_gaps", [])),
        "columns": {
            "verdict": col["verdict"],
            "bands": {
                name: band["verdict"]
                for name, band in (col.get("bands") or {}).items()
            },
        },
        "style": {
            "verdict": style["verdict"],
            "body_stroke_rel_real": (style.get("body_stroke_rel") or {}).get(
                "real"
            ),
            "body_stroke_rel_synth": (style.get("body_stroke_rel") or {}).get(
                "synth"
            ),
        },
        "tokens": {
            "verdict": tokens["verdict"],
            "text_recall": tokens.get("text_recall"),
            "text_precision": tokens.get("text_precision"),
            "ink_recall": tokens.get("ink_recall"),
        },
        "separators": {
            "verdict": seps["verdict"],
            "real_count": seps.get("real_count"),
            "synth_count": seps.get("synth_count"),
            "kind_mismatches": seps.get("kind_mismatches"),
        },
        "graphics": {"verdict": checks["graphics"]["verdict"]},
        "logo": {
            "verdict": logo["verdict"],
            "size_ratio": logo.get("size_ratio"),
            "area_ratio": logo.get("area_ratio"),
            "center_offset_frac": logo.get("center_offset_frac"),
        },
        "arithmetic": {
            "verdict": arith["verdict"],
            **{
                k: v
                for k, v in arith.items()
                if k != "verdict"
                and (isinstance(v, (int, float, bool)) or v is None)
            },
        },
    }


def capture_entry(entry: dict) -> dict:
    """Run the offline eval for one corpus entry -> baseline record."""
    import full_fidelity_eval as ffe

    # Pin the graphics nondeterminism OFF at runtime too: if another test
    # imported full_fidelity_eval before our env export ran, its _BARCODE_BIN
    # may still point at a present Swift binary. Force it missing so
    # metric_graphics is deterministically SKIPPED on every machine.
    ffe._BARCODE_BIN = _MISSING_BARCODE_BIN

    inputs = _load_json(_repo_path(entry["eval_inputs"]))
    truth = ffe.resolve_truth(
        entry["merchant"],
        fixture_path=_repo_path(entry["truth_fixture"]),
    )
    canvas = inputs.get("canvas") or {"w": 400, "h": 600}
    w, h = int(canvas["w"]), int(canvas["h"])
    manifest_words = inputs["manifest_words"]
    syn_words = inputs["syn_words"]
    real_img, real_arr = render_fixture_image(manifest_words, w, h)
    syn_img, syn_arr = render_fixture_image(syn_words, w, h)
    checks = ffe.evaluate_pair(
        real_img,
        syn_img,
        manifest_words,
        syn_words,
        slug=inputs["slug"],
        truth=truth,
        real_png="<offline>",
        syn_png="<offline>",
        composed=bool(inputs.get("composed", False)),
        columns_source=entry.get("columns_source", "bootstrap"),
        section_sequence=inputs.get("section_sequence") or [],
    )
    return {
        "name": entry["name"],
        "merchant": entry["merchant"],
        "slug": inputs["slug"],
        "truth": {
            "slug": truth.slug,
            "version": truth.version,
            "bundle_hash": truth.bundle_hash,
        },
        "render_hashes": {
            "real": _pixel_hash(real_arr),
            "syn": _pixel_hash(syn_arr),
        },
        "metrics": _metric_summary(checks),
    }


def capture_all(manifest_path: str = MANIFEST_PATH) -> dict:
    manifest = _load_json(manifest_path)
    entries = [capture_entry(e) for e in manifest["entries"]]
    return {
        "version": manifest.get("version", 1),
        "entries": {e["name"]: e for e in entries},
    }


def capture(out_path: str | None = None) -> int:
    baseline = capture_all()
    out_path = out_path or COMMITTED_BASELINE
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(baseline, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"corpus baseline captured: {out_path}")
    print(f"  entries: {sorted(baseline['entries'])}")
    for name, rec in sorted(baseline["entries"].items()):
        print(f"  {name}: overall={rec['metrics']['overall']}")
    return 0


# ---------------------------------------------------------------------------
# diff: baseline record vs fresh record -> findings naming receipt+metric+delta
# ---------------------------------------------------------------------------
def _walk_scalars(node, prefix=""):
    """Yield (dotted_field, value) for every scalar leaf in a metric dict."""
    if isinstance(node, dict):
        for key in sorted(node):
            yield from _walk_scalars(
                node[key], f"{prefix}.{key}" if prefix else str(key)
            )
    elif isinstance(node, list):
        # lists (e.g. coverage_gaps) compare as a whole ordered value
        yield prefix, tuple(node)
    else:
        yield prefix, node


def _diff_entry(name: str, base: dict, cur: dict) -> list[dict]:
    findings: list[dict] = []

    # truth tuple + render hashes: any drift is a regression of the pinned
    # inputs, reported under a synthetic metric name.
    for field in ("slug", "version", "bundle_hash"):
        b = base.get("truth", {}).get(field)
        c = cur.get("truth", {}).get(field)
        if b != c:
            findings.append(
                {
                    "receipt": name,
                    "metric": "truth",
                    "field": field,
                    "baseline": b,
                    "current": c,
                }
            )
    for side in ("real", "syn"):
        b = base.get("render_hashes", {}).get(side)
        c = cur.get("render_hashes", {}).get(side)
        if b != c:
            findings.append(
                {
                    "receipt": name,
                    "metric": "render",
                    "field": side,
                    "baseline": b,
                    "current": c,
                }
            )

    base_m = base.get("metrics", {})
    cur_m = cur.get("metrics", {})
    base_leaves = dict(_walk_scalars(base_m))
    cur_leaves = dict(_walk_scalars(cur_m))
    for field in sorted(set(base_leaves) | set(cur_leaves)):
        b = base_leaves.get(field)
        c = cur_leaves.get(field)
        if b == c:
            continue
        # numeric drift within rounding epsilon is not a regression
        if (
            isinstance(b, (int, float))
            and isinstance(c, (int, float))
            and not isinstance(b, bool)
            and not isinstance(c, bool)
            and abs(b - c) <= _EPS
        ):
            continue
        metric = field.split(".", 1)[0]
        finding = {
            "receipt": name,
            "metric": metric,
            "field": field,
            "baseline": _jsonable(b),
            "current": _jsonable(c),
        }
        if (
            isinstance(b, (int, float))
            and isinstance(c, (int, float))
            and not isinstance(b, bool)
            and not isinstance(c, bool)
        ):
            finding["delta"] = round(c - b, 6)
        findings.append(finding)
    return findings


def _jsonable(value):
    return list(value) if isinstance(value, tuple) else value


def diff_baselines(baseline: dict, current: dict) -> list[dict]:
    findings: list[dict] = []
    base_entries = baseline.get("entries", {})
    cur_entries = current.get("entries", {})
    missing = sorted(set(base_entries) - set(cur_entries))
    extra = sorted(set(cur_entries) - set(base_entries))
    for name in missing:
        findings.append(
            {
                "receipt": name,
                "metric": "corpus",
                "field": "presence",
                "baseline": "present",
                "current": "missing",
            }
        )
    for name in extra:
        findings.append(
            {
                "receipt": name,
                "metric": "corpus",
                "field": "presence",
                "baseline": "absent",
                "current": "present",
            }
        )
    for name in sorted(set(base_entries) & set(cur_entries)):
        findings.extend(
            _diff_entry(name, base_entries[name], cur_entries[name])
        )
    return findings


def _print_findings(findings: list[dict]) -> None:
    for f in findings:
        line = (
            f"REGRESSION {f['receipt']} / {f['metric']} / {f['field']}: "
            f"{f['baseline']!r} -> {f['current']!r}"
        )
        if "delta" in f:
            line += f" (delta {f['delta']:+g})"
        print(line)


def compare(baseline_path: str, as_json: bool = False) -> int:
    baseline = _load_json(baseline_path)
    current = capture_all()
    findings = diff_baselines(baseline, current)
    if as_json:
        print(
            json.dumps(
                {"ok": not findings, "findings": findings},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        if findings:
            _print_findings(findings)
            print(f"REGRESSION: {len(findings)} finding(s) vs {baseline_path}")
        else:
            print(f"corpus stable vs {baseline_path}")
    return 1 if findings else 0


def check(as_json: bool = False) -> int:
    """Diff a fresh offline capture against the COMMITTED baseline."""
    baseline = _load_json(COMMITTED_BASELINE)
    current = capture_all()
    findings = diff_baselines(baseline, current)
    if as_json:
        print(
            json.dumps(
                {
                    "ok": not findings,
                    "baseline": os.path.relpath(COMMITTED_BASELINE, REPO),
                    "findings": findings,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        if findings:
            _print_findings(findings)
            print(
                f"REGRESSION vs committed baseline: {len(findings)} finding(s)"
            )
        else:
            print("corpus stable vs committed baseline")
    return 1 if findings else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="verb", required=True)
    p_cap = sub.add_parser("capture")
    p_cap.add_argument("out_path", nargs="?", default=None)
    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("baseline_path")
    p_cmp.add_argument("--json", action="store_true", dest="as_json")
    p_chk = sub.add_parser("check")
    p_chk.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    if args.verb == "capture":
        return capture(args.out_path)
    if args.verb == "compare":
        return compare(args.baseline_path, as_json=args.as_json)
    return check(as_json=args.as_json)


if __name__ == "__main__":
    sys.exit(main())

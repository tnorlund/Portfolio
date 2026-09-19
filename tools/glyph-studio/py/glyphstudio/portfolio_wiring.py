"""Emit the SynthesisPipeline merchant tables from ``pipeline_merchants.json``.

``tools/glyph-studio/fixtures/pipeline_merchants.json`` is the one place a
finale merchant is declared (canonical name, font dir, hero, source receipt,
card label, receipt dims, bold callout). This module renders it into
``portfolio/components/ui/Figures/SynthesisPipeline/merchants.generated.ts``
so ``pipelineData.ts`` never needs a hand edit per vendor, and the exporter
can write the measured ``dims`` back into the manifest.

    python -m glyphstudio.portfolio_wiring            # regenerate + diff check
    python -m glyphstudio.portfolio_wiring --check    # exit 1 on drift
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(os.path.dirname(_HERE))
ROOT = os.path.dirname(os.path.dirname(STUDIO))
MANIFEST = os.path.join(STUDIO, "fixtures", "pipeline_merchants.json")
GENERATED_TS = os.path.join(
    ROOT,
    "portfolio",
    "components",
    "ui",
    "Figures",
    "SynthesisPipeline",
    "merchants.generated.ts",
)
PIPELINE_PUBLIC = os.path.join(
    ROOT, "portfolio", "public", "synthetic-receipts", "pipeline"
)
FINALE_FILES = (
    "real.webp",
    "final.webp",
    "final.labels.json",
    "logo.png",
    "compose_steps.json",
)

HEADER = """\
// GENERATED FILE - do not edit by hand.
// Source: tools/glyph-studio/fixtures/pipeline_merchants.json
// Regenerate: python -m glyphstudio.portfolio_wiring
//   (tools/glyph-studio/py/new_vendor.py wire does this after an export)
"""


def load_manifest(path: str = MANIFEST) -> dict[str, dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["merchants"]


def save_manifest(
    merchants: dict[str, dict[str, Any]], path: str = MANIFEST
) -> None:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["merchants"] = merchants
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def validate(merchants: dict[str, dict[str, Any]]) -> list[str]:
    problems = []
    for slug, spec in merchants.items():
        for key in (
            "merchant",
            "font",
            "hero",
            "receipt",
            "label",
            "dims",
            "bold_callout",
        ):
            if key not in spec:
                problems.append(f"{slug}: missing {key!r}")
        dims = spec.get("dims") or {}
        if dims and (
            dims.get("w") != 760 or not isinstance(dims.get("h"), int)
        ):
            problems.append(
                f"{slug}: dims must be {{w: 760, h: <int>}}, got {dims}"
            )
    return problems


def _ts_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_ts(merchants: dict[str, dict[str, Any]]) -> str:
    slugs = list(merchants)
    lines = [HEADER]
    lines.append("export type Merchant =")
    for i, slug in enumerate(slugs):
        end = ";" if i == len(slugs) - 1 else ""
        lines.append(f"  | {_ts_string(slug)}{end}")
    lines.append("")
    lines.append(
        "/** Every merchant the finale fans out to, in card order. */"
    )
    lines.append("export const MERCHANTS: Merchant[] = [")
    lines.extend(f"  {_ts_string(s)}," for s in slugs)
    lines.append("];")
    lines.append("")
    lines.append("export const MERCHANT_LABELS: Record<Merchant, string> = {")
    lines.extend(f"  {s}: {_ts_string(merchants[s]['label'])}," for s in slugs)
    lines.append("};")
    lines.append("")
    lines.append(
        "/**\n"
        " * True pixel dimensions of each merchant's normalized receipt (real + final\n"
        " * share these). All 760px wide; heights differ - that difference is the point\n"
        " * of the finale, so the cards render at a common width and their natural\n"
        " * (different) heights, tops aligned.\n"
        " */"
    )
    lines.append(
        "export const RECEIPT_DIMS: Record<Merchant, { w: number; h: number }> = {"
    )
    for s in slugs:
        d = merchants[s]["dims"]
        lines.append(f"  {s}: {{ w: {int(d['w'])}, h: {int(d['h'])} }},")
    lines.append("};")
    lines.append("")
    lines.append(
        "/**\n"
        " * The measured-weight callout for act 4, per merchant (spec copy). Shown when\n"
        " * the slider reaches the merchant's bold weight.\n"
        " */"
    )
    lines.append(
        "export const BOLD_WEIGHT_CALLOUT: Record<Merchant, string> = {"
    )
    lines.extend(
        f"  {s}: {_ts_string(merchants[s]['bold_callout'])}," for s in slugs
    )
    lines.append("};")
    lines.append("")
    return "\n".join(lines)


def write_ts(
    merchants: dict[str, dict[str, Any]], out_path: str = GENERATED_TS
) -> str:
    text = render_ts(merchants)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def asset_dirs(public_dir: str = PIPELINE_PUBLIC) -> set[str]:
    if not os.path.isdir(public_dir):
        return set()
    return {
        name
        for name in os.listdir(public_dir)
        if os.path.isdir(os.path.join(public_dir, name))
        and all(
            os.path.exists(os.path.join(public_dir, name, f))
            for f in FINALE_FILES
        )
    }


def set_entry(
    slug: str,
    *,
    merchant: str,
    font: str,
    hero: str,
    image_id: str,
    receipt_id: int,
    label: str,
    bold_callout: str,
    dims: dict[str, int] | None = None,
    path: str = MANIFEST,
) -> None:
    merchants = load_manifest(path)
    entry = merchants.get(slug, {})
    entry.update(
        {
            "merchant": merchant,
            "font": font,
            "hero": hero,
            "receipt": {"image_id": image_id, "receipt_id": int(receipt_id)},
            "label": label,
            "bold_callout": bold_callout,
        }
    )
    if dims:
        entry["dims"] = {"w": int(dims["w"]), "h": int(dims["h"])}
    entry.setdefault("dims", {"w": 760, "h": 0})
    merchants[slug] = entry
    save_manifest(merchants, path)


def set_dims(slug: str, dims: dict[str, int], path: str = MANIFEST) -> None:
    merchants = load_manifest(path)
    merchants[slug]["dims"] = {"w": int(dims["w"]), "h": int(dims["h"])}
    save_manifest(merchants, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--out", default=GENERATED_TS)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed TS differs",
    )
    args = ap.parse_args(argv)
    merchants = load_manifest(args.manifest)
    problems = validate(merchants)
    if problems:
        for p in problems:
            print(f"manifest: {p}", file=sys.stderr)
        return 2
    text = render_ts(merchants)
    if args.check:
        current = (
            open(args.out, encoding="utf-8").read()
            if os.path.exists(args.out)
            else ""
        )
        if current != text:
            print(
                f"{args.out} is stale; run python -m glyphstudio.portfolio_wiring",
                file=sys.stderr,
            )
            return 1
        print("merchants.generated.ts up to date")
        return 0
    write_ts(merchants, args.out)
    print(f"wrote {args.out}: {len(merchants)} merchants")
    return 0


if __name__ == "__main__":
    sys.exit(main())

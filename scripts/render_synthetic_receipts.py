#!/usr/bin/env python3.12
"""Render synthetic receipt candidates (and their real base) to PNGs for QA.

Reads a bundle produced by ``verify_synthetic_replay.py local-pipeline`` plus the
real receipt export directory it was built from, then renders each accepted
synthetic training example beside the real receipt it was derived from using the
font-render renderer. This is the visual-QA artifact for milestone 2 of the
``feat/receipt-font-render`` charter — it consumes data only and touches no gate.

Usage:
    python3.12 scripts/render_synthetic_receipts.py \
        --bundle .tmp/bundle.json \
        --receipt-dir .tmp/vons_export \
        --out-dir .tmp/render \
        --merchant Vons
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (
    os.path.join(REPO_ROOT, "receipt_agent"),
    os.path.join(REPO_ROOT, "receipt_upload"),
):
    if path not in sys.path:
        sys.path.insert(0, path)

from receipt_agent.agents.label_evaluator.rendering import (  # noqa: E402
    RenderConfig,
    build_merchant_font_profile,
    extract_receipt_font_profile,
    render_real_vs_synthetic,
    save_receipt_png,
)


def _bbox_from_bounding_box(bb: dict) -> list[float] | None:
    try:
        x = float(bb["x"])
        y = float(bb["y"])
        w = float(bb["width"])
        h = float(bb["height"])
    except (KeyError, TypeError, ValueError):
        return None
    return [x, y, x + w, y + h]


def _real_receipt_dict(export: dict, receipt_id: int) -> dict:
    """Build a renderer receipt dict from an exported receipt's OCR words."""
    label_index: dict[tuple, list[str]] = {}
    for lbl in export.get("receipt_word_labels", []) or []:
        if lbl.get("receipt_id") != receipt_id:
            continue
        key = (lbl.get("line_id"), lbl.get("word_id"))
        label_index.setdefault(key, []).append(str(lbl.get("label") or ""))

    words = []
    for word in export.get("receipt_words", []) or []:
        if word.get("receipt_id") != receipt_id:
            continue
        bbox = _bbox_from_bounding_box(word.get("bounding_box") or {})
        if bbox is None:
            continue
        key = (word.get("line_id"), word.get("word_id"))
        words.append(
            {
                "text": word.get("text", ""),
                "bbox": bbox,
                "labels": label_index.get(key, []),
                "line_id": word.get("line_id"),
                "word_id": word.get("word_id"),
            }
        )
    return {"words": words}


def _resolve_tag(tag, id_to_label: dict[int, str]):
    """ner_tags may be BIO strings ('B-PRODUCT_NAME') or integer ids."""
    if isinstance(tag, str):
        return tag
    if isinstance(tag, int):
        return id_to_label.get(tag)
    return None


def _synthetic_receipt_dict(example: dict, id_to_label: dict[int, str]) -> dict:
    words = []
    tokens = example.get("tokens") or []
    bboxes = example.get("bboxes") or []
    tags = example.get("ner_tags") or []
    for index, (token, bbox) in enumerate(zip(tokens, bboxes)):
        label = _resolve_tag(tags[index], id_to_label) if index < len(tags) else None
        # Keep the raw (possibly BIO-prefixed) label; the renderer normalizes it.
        words.append(
            {
                "text": token,
                "bbox": bbox,
                "labels": [label] if label and label != "O" else [],
            }
        )
    return {"words": words}


def _profile_from_export_dir(merchant: str, receipt_dir: str):
    receipt_profiles = []
    for name in sorted(os.listdir(receipt_dir)):
        if not name.endswith(".json"):
            continue
        export = json.load(open(os.path.join(receipt_dir, name)))
        # Group words / lines / letters per receipt id.
        words_by_rid: dict[int, list] = {}
        lines_by_rid: dict[int, list] = {}
        letters_by_rid: dict[int, list] = {}
        for word in export.get("receipt_words", []) or []:
            words_by_rid.setdefault(word.get("receipt_id"), []).append(word)
        for line in export.get("receipt_lines", []) or []:
            lines_by_rid.setdefault(line.get("receipt_id"), []).append(line)
        for letter in export.get("receipt_letters", []) or []:
            letters_by_rid.setdefault(letter.get("receipt_id"), []).append(letter)
        for rid, words in words_by_rid.items():
            profile = extract_receipt_font_profile(
                words,
                lines_by_rid.get(rid),
                letters=letters_by_rid.get(rid),
            )
            if profile is not None:
                receipt_profiles.append(profile)
    return build_merchant_font_profile(merchant, receipt_profiles)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--receipt-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--merchant", default="Vons")
    parser.add_argument("--limit", type=int, default=4)
    args = parser.parse_args()

    bundle = json.load(open(args.bundle))
    examples = bundle.get("synthetic_training_examples", []) or []
    if not examples:
        print("No synthetic_training_examples in bundle.")
        return 1

    # Map ner_tag ids back to label names if the bundle records them.
    id_to_label: dict[int, str] = {}
    policy = bundle.get("synthetic_training_batch_policy") or {}
    for key in ("id_to_label", "label_list", "labels"):
        value = policy.get(key)
        if isinstance(value, dict):
            id_to_label = {int(k): v for k, v in value.items()}
            break
        if isinstance(value, list):
            id_to_label = {i: lbl for i, lbl in enumerate(value)}
            break

    # Index exported files by image id for base-receipt lookup.
    exports: dict[str, dict] = {}
    for name in os.listdir(args.receipt_dir):
        if name.endswith(".json"):
            exports[name[:-5]] = json.load(
                open(os.path.join(args.receipt_dir, name))
            )

    profile = _profile_from_export_dir(args.merchant, args.receipt_dir)
    print("Merchant profile:", json.dumps(profile.to_dict() if profile else None))

    os.makedirs(args.out_dir, exist_ok=True)
    config = RenderConfig(width=460, height=1100, color_by_label=True,
                          draw_price_column=True)

    rendered = 0
    for example in examples[: args.limit]:
        candidate_id = example.get("candidate_id", f"candidate-{rendered}")
        base_key = (example.get("metadata") or {}).get("base_receipt_key", "")
        image_id, _, suffix = base_key.partition("#")
        try:
            base_receipt_id = int(suffix)
        except ValueError:
            base_receipt_id = None

        synthetic = _synthetic_receipt_dict(example, id_to_label)
        synth_path = os.path.join(args.out_dir, f"{candidate_id}.synthetic.png")
        save_receipt_png(synthetic, synth_path, profile=profile, config=config)

        if image_id in exports and base_receipt_id is not None:
            real = _real_receipt_dict(exports[image_id], base_receipt_id)
            combined = render_real_vs_synthetic(
                real, synthetic, profile=profile, config=config,
                labels=(f"real {base_key}", f"synthetic {example.get('operation','')}")
            )
            combined_path = os.path.join(
                args.out_dir, f"{candidate_id}.real_vs_synthetic.png"
            )
            combined.save(combined_path)
            print("wrote", combined_path)
        else:
            print("wrote", synth_path, "(no base receipt found for", base_key, ")")
        rendered += 1

    print(f"Rendered {rendered} candidate(s) to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

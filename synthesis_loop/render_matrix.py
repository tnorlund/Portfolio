#!/usr/bin/env python3
"""render_matrix.py -- render ONE hybrid per (merchant x synthesis-operation) for the realism review.

For each target operation present in the bundle, picks the first candidate, renders its hybrid (cleaned
content + #4 renderer), fetches the real original once, and writes a per-op context json (operation +
the tokens that operation produced). Lets opus/codex analyze "what makes THIS specific change look fake".

Usage: render_matrix.py <bundle.json> <receipt_dir> <merchant_name> <out_dir>
"""
from __future__ import annotations
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
for p in (HERE, SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)
import render_synthetic_receipts as rsr  # noqa: E402
import thermal_variants as TV  # noqa: E402

TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
REGION = os.environ.get("AWS_REGION", "us-east-1")
W, H = 460, 1100
# operations that produce visible synthetic CONTENT (skip hard_negative -- it's deliberately fake)
TARGET_OPS = ["add_line_item", "replace_field", "compose_online_catalog",
              "compose_store_header", "remove_line_item"]


def _op(ex):
    return (ex.get("metadata") or {}).get("operation") or ex.get("operation") or "?"


def main():
    bundle_p, receipt_dir, merchant, out_dir = sys.argv[1:5]
    os.makedirs(out_dir, exist_ok=True)
    bundle = json.load(open(bundle_p))
    examples = bundle.get("synthetic_training_examples", []) or []
    picked = {}
    for i, ex in enumerate(examples):
        op = _op(ex)
        if op in TARGET_OPS and op not in picked:
            picked[op] = (i, ex)
    if not picked:
        print("no target-op candidates")
        return 0

    atlas = rsr.cached_glyph_atlas(TABLE, merchant, region=REGION, max_receipts=8)
    if atlas is None:
        print("no atlas for", merchant)
        return 1
    profile = rsr.cached_font_profile(TABLE, merchant, region=REGION, max_receipts=12)
    id_to_label = TV._id_to_label(bundle)
    exports = TV._load_exports(receipt_dir)
    import boto3
    s3 = boto3.client("s3", region_name=REGION)

    orig_done = False
    for op, (i, ex) in picked.items():
        synth = rsr._synthetic_receipt_dict(ex, id_to_label)
        hp = os.path.join(out_dir, f"{op}.hybrid.png")
        rsr._render_cached_hybrid(
            synth, atlas, profile=profile, width=W, height=H, path=hp,
            section_scale=rsr.section_scale_for_merchant(merchant),
        )
        # original once per merchant
        base_key = (ex.get("metadata") or {}).get("base_receipt_key", "")
        image_id, _, suffix = base_key.partition("#")
        try:
            rid = int(suffix)
        except ValueError:
            rid = None
        if not orig_done and image_id and rid is not None:
            rec = TV._receipt_record(exports.get(image_id, {}), image_id, rid)
            if rec and TV._fetch_original(rec, os.path.join(out_dir, "original.jpg"), s3):
                orig_done = True
        # context for the analyst
        json.dump({"merchant": merchant, "operation": op, "candidate_id": ex.get("candidate_id"),
                   "tokens": ex.get("tokens"), "ner_tags": ex.get("ner_tags")},
                  open(os.path.join(out_dir, f"{op}.context.json"), "w"))
        print(f"  {merchant} :: {op} -> {os.path.basename(hp)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

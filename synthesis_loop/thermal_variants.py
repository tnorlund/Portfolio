#!/usr/bin/env python3
"""thermal_variants.py -- render thermal-realistic variants of each bundle candidate and
fetch the ORIGINAL receipt photo, then stitch an ORIGINAL | HYBRID | GLYPH 3-up for the
opus line review.

This is DECOUPLED from the hill-climb score: the verifier scores the JSON tokens/bboxes/
labels, not pixels. The loop's bundle render (save_receipt_png, color_by_label) looks
"computer printed"; the thermal renderers (real glyph atlas + paper realism) look like an
actual thermal print. We render BOTH so we can compare, and we drop the real photographed
receipt next to them so opus has a true thermal ground truth to judge against.

Usage:  thermal_variants.py <bundle.json> <receipt_dir> <merchant_name> <out_dir> [limit]
Env:    DYNAMODB_TABLE_NAME, AWS_REGION, AWS creds (glyph atlas + S3 original fetch).
"""
from __future__ import annotations

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# Importing the render script reuses its receipt_* sys.path setup + helper functions
# (_synthetic_receipt_dict, _render_cached_hybrid) and the rendering imports. Importing is
# side-effect free: its main() only runs under __main__.
import render_synthetic_receipts as rsr  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
REGION = os.environ.get("AWS_REGION", "us-east-1")
W, H = 460, 1100


def _id_to_label(bundle: dict) -> dict[int, str]:
    policy = bundle.get("synthetic_training_batch_policy") or {}
    for key in ("id_to_label", "label_list", "labels"):
        v = policy.get(key)
        if isinstance(v, dict):
            return {int(k): val for k, val in v.items()}
        if isinstance(v, list):
            return {i: lbl for i, lbl in enumerate(v)}
    return {}


def _load_exports(receipt_dir: str) -> dict[str, dict]:
    exports: dict[str, dict] = {}
    for n in os.listdir(receipt_dir):
        if n.endswith(".json"):
            exports[n[:-5]] = json.load(open(os.path.join(receipt_dir, n)))
    return exports


def _receipt_record(export: dict, image_id: str, receipt_id):
    for r in export.get("receipts", []) or []:
        rr = r.get("receipt") if isinstance(r, dict) and "receipt" in r else r
        if not isinstance(rr, dict):
            continue
        if rr.get("image_id") == image_id and int(rr.get("receipt_id", -1)) == receipt_id:
            return rr
    return None


def _fetch_original(rec: dict, path: str, s3) -> str | None:
    # Prefer the CDN jpg (smaller, displayable); fall back to the raw png.
    bucket, key = rec.get("cdn_s3_bucket"), rec.get("cdn_s3_key")
    if not (bucket and key):
        bucket, key = rec.get("raw_s3_bucket"), rec.get("raw_s3_key")
    if not (bucket and key):
        return None
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        img = Image.open(io.BytesIO(obj["Body"].read())).convert("RGB")
        img.save(path)
        return path
    except Exception as e:  # noqa: BLE001
        print("  original fetch failed:", e)
        return None


def _caption(img: Image.Image, text: str) -> Image.Image:
    strip_h = 22
    out = Image.new("RGB", (img.width, img.height + strip_h), (20, 20, 20))
    draw = ImageDraw.Draw(out)
    draw.text((4, 5), text, fill=(255, 255, 255))
    out.paste(img, (0, strip_h))
    return out


def _threeup(items: list[tuple[str | None, str]], out_path: str) -> str | None:
    imgs = []
    for p, lbl in items:
        if not (p and os.path.exists(p)):
            continue
        im = Image.open(p).convert("RGB")
        if im.height != H:
            im = im.resize((max(1, int(im.width * H / im.height)), H))
        imgs.append(_caption(im, lbl))
    if not imgs:
        return None
    gap = 12
    total_w = sum(i.width for i in imgs) + gap * (len(imgs) - 1)
    max_h = max(i.height for i in imgs)
    canvas = Image.new("RGB", (total_w, max_h), (255, 255, 255))
    x = 0
    for im in imgs:
        canvas.paste(im, (x, 0))
        x += im.width + gap
    canvas.save(out_path)
    return out_path


def main() -> int:
    if len(sys.argv) < 5:
        print(__doc__)
        return 2
    bundle_p, receipt_dir, merchant, out_dir = sys.argv[1:5]
    limit = int(sys.argv[5]) if len(sys.argv) > 5 else 2
    os.makedirs(out_dir, exist_ok=True)

    bundle = json.load(open(bundle_p))
    examples = bundle.get("synthetic_training_examples", []) or []
    if not examples:
        print("no synthetic_training_examples in", bundle_p)
        return 1
    id_to_label = _id_to_label(bundle)
    exports = _load_exports(receipt_dir)

    atlas = rsr.build_glyph_atlas_from_dynamo(TABLE, merchant, region=REGION, max_receipts=8)
    if atlas is None:
        print("no glyph atlas for", merchant)
        return 1
    profile = rsr.build_merchant_font_profile_from_dynamo(
        TABLE, merchant, region=REGION, max_receipts=12
    )
    fallback = rsr.make_ttf_fallback(atlas)

    import boto3  # noqa: E402

    s3 = boto3.client("s3", region_name=REGION)

    for i, ex in enumerate(examples[:limit]):
        cid = ex.get("candidate_id", f"candidate-{i}")
        synth = rsr._synthetic_receipt_dict(ex, id_to_label)

        hybrid_p = os.path.join(out_dir, f"{cid}.hybrid.png")
        rsr._render_cached_hybrid(synth, atlas, profile=profile, width=W, height=H, path=hybrid_p)

        glyph_p = os.path.join(out_dir, f"{cid}.glyph.png")
        cfg = rsr.GlyphRenderConfig(width=W, height=H, seed=37 + i, body_glyph_source="atlas")
        rsr.save_receipt_glyphs(
            synth, atlas, glyph_p, profile=profile, config=cfg, coord_max=1000.0, fallback=fallback
        )

        orig_p = None
        base_key = (ex.get("metadata") or {}).get("base_receipt_key", "")
        image_id, _, suffix = base_key.partition("#")
        try:
            rid = int(suffix)
        except ValueError:
            rid = None
        rec = (
            _receipt_record(exports.get(image_id, {}), image_id, rid)
            if (image_id and rid is not None)
            else None
        )
        if rec:
            orig_p = _fetch_original(rec, os.path.join(out_dir, f"{cid}.original.jpg"), s3)

        comp = os.path.join(out_dir, f"{cid}.thermal3up.png")
        _threeup(
            [
                (orig_p, "ORIGINAL real thermal"),
                (hybrid_p, "HYBRID"),
                (glyph_p, "GLYPH atlas"),
            ],
            comp,
        )
        print(
            f"  {cid}: hybrid+glyph rendered, original="
            f"{'yes' if orig_p else 'MISSING'} -> {os.path.basename(comp)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

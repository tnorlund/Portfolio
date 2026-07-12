#!/usr/bin/env python3
"""render_costco_gold.py -- deterministic re-render of the Costco gold receipt.

Reproduces the shipped prod render
(``portfolio/public/synthetic-receipts/pipeline/costco/final.webp``) from the
same inputs the pipeline used: the gold receipt's OCR words + barcodes
(Dynamo, cached locally per receipt), the published bitMatrix-C2 fonts, and
the production render path (``scripts/render_synthetic_receipts``). With no
overrides the output is byte-identical to the shipped webp -- that SHA check
is the regression tripwire for the opt-in knobs below.

Opt-in interventions (GOLD_STANDARD.md Part 2):

- ``--vscale F``   I2: body glyph vertical scale (cap-height correction).
                   Plumbs ``bitmap_glyph_vscale`` into the render config;
                   line pitch and condense are untouched.
- ``--degrade P``  I1: apply the fitted physical print+scan degradation
                   model (``glyphstudio.degrade``) with parameter JSON ``P``
                   to the rendered image before writing.

Usage:
    python tools/glyph-studio/py/render_costco_gold.py --out /tmp/final.webp
    python ... --vscale 0.857 --out /tmp/final_i2.webp
    python ... --degrade degrade_params/costco.json --out /tmp/final_i1.webp

The receipt payload is cached in ``--cache-dir`` after the first Dynamo pull,
so subsequent renders are offline and fast.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _p in (
    _HERE,
    os.path.join(_ROOT, "receipt_agent"),
    os.path.join(_ROOT, "receipt_dynamo"),
    os.path.join(_ROOT, "receipt_upload"),
    os.path.join(_ROOT, "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GOLD_MERCHANT = "Costco Wholesale"
# The shipped gold render/scan pair is receipt 57cb7f2c...#1
# (final.labels.json: operation=re_render_real_receipt, receipt_key).
GOLD_IMAGE_ID = "57cb7f2c-7dcc-4974-9ef8-a460232f3b1d"
GOLD_RECEIPT_ID = 1
GOLD_WIDTH = 760
GOLD_HEIGHT = 2999  # the shipped canvas (matches the real scan's aspect)


def _load_receipt_payload(client, merchant, image_id, receipt_id):
    """Words + barcodes + geometry, matching glyph_review.receipt."""
    details = client.get_image_details(image_id)
    rec = next(
        (r for r in details.receipts if str(r.receipt_id) == str(receipt_id)),
        None,
    )
    if rec is None:
        raise RuntimeError(f"receipt {receipt_id} not found for {image_id}")
    lbl = {
        (l.line_id, l.word_id): l.label
        for l in details.receipt_word_labels
        if l.receipt_id == receipt_id
    }
    words = [
        {
            "text": w.text,
            "line_id": w.line_id,
            "word_id": w.word_id,
            "bbox": [
                w.top_left["x"] * 1000,
                w.top_left["y"] * 1000,
                w.bottom_right["x"] * 1000,
                w.bottom_right["y"] * 1000,
            ],
            "labels": (
                [lbl[(w.line_id, w.word_id)]]
                if lbl.get((w.line_id, w.word_id)) not in (None, "O")
                else []
            ),
        }
        for w in details.receipt_words
        if w.receipt_id == receipt_id
    ]
    barcodes = []
    if hasattr(client, "list_receipt_barcodes_from_receipt"):
        try:
            for b in client.list_receipt_barcodes_from_receipt(
                image_id, receipt_id
            ):
                barcodes.append(
                    {
                        "text": getattr(b, "text", "") or "",
                        "symbology": getattr(b, "symbology", ""),
                        "top_left": getattr(b, "top_left", None),
                        "bottom_right": getattr(b, "bottom_right", None),
                        "confidence": getattr(b, "confidence", None),
                    }
                )
        except Exception:  # noqa: BLE001
            barcodes = []
    return rec, words, barcodes


def _cached_payload(cache_dir: str, table: str, region: str) -> dict:
    """The gold receipt payload, cached to disk after the first Dynamo pull."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(
        cache_dir, f"payload_{GOLD_IMAGE_ID}_{GOLD_RECEIPT_ID}.json"
    )
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(table_name=table, region=region)
    rec, words, barcodes = _load_receipt_payload(
        client, GOLD_MERCHANT, GOLD_IMAGE_ID, GOLD_RECEIPT_ID
    )
    doc = {
        "merchant": GOLD_MERCHANT,
        "image_id": GOLD_IMAGE_ID,
        "receipt_id": GOLD_RECEIPT_ID,
        "width": rec.width,
        "height": rec.height,
        "words": words,
        "barcodes": barcodes,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    os.replace(tmp, path)
    return doc


def render_gold(
    out: str,
    *,
    table: str,
    region: str,
    cache_dir: str,
    vscale: float | None = None,
    labels_out: str | None = None,
) -> str:
    import render_synthetic_receipts as rsr

    doc = _cached_payload(cache_dir, table, region)
    merchant = doc["merchant"]
    prof = rsr.cached_font_profile(table, merchant, region=region,
                                   max_receipts=12)
    typ = rsr.merchant_typography(merchant)
    if vscale is not None:
        typ["bitmap_glyph_vscale"] = float(vscale)
    ss = rsr.section_scale_for_merchant(merchant)
    wt = GOLD_WIDTH
    ht = GOLD_HEIGHT
    payload = {
        "words": doc["words"],
        "barcodes": doc["barcodes"],
        "merchant_name": merchant,
    }
    box_sink: list | None = [] if labels_out else None
    if box_sink is not None:
        typ["box_sink"] = box_sink
    rsr._render_cached_hybrid(
        copy.deepcopy(payload),
        None,
        profile=prof,
        width=wt,
        height=ht,
        path=out,
        section_scale=ss,
        **typ,
    )
    if labels_out and box_sink is not None:
        # Render-true labels in the shipped final.labels.json format:
        # 0-1000 bottom-origin boxes over the inner (margin=10) canvas.
        margin, inner_w, inner_h = 10.0, wt - 20.0, ht - 20.0
        tokens, bboxes = [], []
        for b in box_sink:
            x0, y0, x1, y1 = (float(v) for v in b["px"])
            tokens.append(b["text"])
            bboxes.append(
                [
                    (x0 - margin) / inner_w * 1000.0,
                    (1.0 - (y1 - margin) / inner_h) * 1000.0,
                    (x1 - margin) / inner_w * 1000.0,
                    (1.0 - (y0 - margin) / inner_h) * 1000.0,
                ]
            )
        with open(labels_out, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "tokens": tokens,
                    "bboxes": bboxes,
                    "merchant_name": merchant,
                    "receipt_key": f"{GOLD_IMAGE_ID}#{GOLD_RECEIPT_ID}",
                    "metadata": {
                        "operation": "re_render_real_receipt",
                        "boxes": "render_true",
                        "render": {
                            "width": wt,
                            "height": ht,
                            "margin": 10,
                        },
                    },
                },
                fh,
            )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--vscale", type=float, default=None,
                    help="I2 body glyph vertical scale (default: off)")
    ap.add_argument("--degrade", default=None,
                    help="I1 fitted degrade parameter JSON (default: off)")
    ap.add_argument("--degrade-seed", type=int, default=0,
                    help="deterministic seed for the degrade noise stages")
    ap.add_argument("--labels-out", default=None,
                    help="write render-true labels JSON (box_sink)")
    ap.add_argument("--table",
                    default=os.environ.get("DYNAMODB_TABLE_NAME",
                                           "ReceiptsTable-dc5be22"))
    ap.add_argument("--region",
                    default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--cache-dir",
                    default=os.path.join(_ROOT, ".out", "costco_gold"))
    ap.add_argument("--expect-sha", default=None,
                    help="fail unless the output SHA-256 matches (byte-"
                         "identity tripwire for the default render)")
    args = ap.parse_args(argv)

    render_gold(
        args.out,
        table=args.table,
        region=args.region,
        cache_dir=args.cache_dir,
        vscale=args.vscale,
        labels_out=args.labels_out,
    )
    if args.degrade:
        from glyphstudio.degrade import degrade_image_file

        degrade_image_file(args.out, args.out, args.degrade,
                           seed=args.degrade_seed)
    sha = hashlib.sha256(open(args.out, "rb").read()).hexdigest()
    print(f"{args.out} sha256={sha}")
    if args.expect_sha and sha != args.expect_sha:
        print(f"SHA MISMATCH: expected {args.expect_sha}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

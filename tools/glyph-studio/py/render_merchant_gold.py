#!/usr/bin/env python3
"""render_merchant_gold.py -- deterministic re-render of a merchant's gold receipt.

Merchant-generalized sibling of ``render_costco_gold.py`` (PR #1113). Renders a
chosen gold receipt (``--image-id`` / ``--receipt-id``) at a fixed canvas from
the same inputs the production pipeline uses -- the receipt's OCR words +
barcodes (Dynamo, cached per receipt) and the merchant's published fonts /
typography -- through the production render path
(``scripts/render_synthetic_receipts``).

Opt-in interventions (GOLD_STANDARD.md Part 2 + the ladder-green work):

- ``--vscale F``    I2 body glyph vertical scale (cap-height correction).
- ``--face K=PATH`` override one bitmap_font face (repeatable). ``--face
                    heavy=.../sprouts-heavy-composite.glyphs.npz`` swaps the
                    bold face to the composite true-bold atlas (PR #1104) --
                    the "modern" variant's atlas half.
- ``--degrade P``   I1 fitted print+scan degradation (``glyphstudio.degrade``)
                    applied to the rendered image before writing.

The receipt payload is cached in ``--cache-dir`` after the first Dynamo pull,
so subsequent renders are offline and fast.

Usage:
    render_merchant_gold.py --merchant "Sprouts Farmers Market" \\
        --image-id 00ded398-... --receipt-id 2 --width 760 --height 2471 \\
        --face heavy=/path/sprouts-heavy-composite.glyphs.npz \\
        --out modern.webp --labels-out modern.labels.json
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


def _load_receipt_payload(table, region, image_id, receipt_id):
    """Words + barcodes + geometry via raw DynamoDB queries.

    Built from targeted ``begins_with(SK, RECEIPT#nnnnn...)`` queries rather
    than ``DynamoClient.get_image_details`` so a single malformed/legacy item
    in the image partition (e.g. a TYPE-less GSI-projection duplicate) cannot
    fail the whole render. Returns ``(width, height, words, barcodes)`` in the
    render_costco_gold payload contract (0-1000 coordinates, [tlx,tly,brx,bry]).
    """
    import re as _re

    import boto3

    ddb = boto3.client("dynamodb", region_name=region)
    rid = f"{int(receipt_id):05d}"

    def query(sk_prefix):
        items, kwargs = [], {}
        while True:
            r = ddb.query(
                TableName=table,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk": {"S": sk_prefix},
                },
                **kwargs,
            )
            items.extend(r["Items"])
            if "LastEvaluatedKey" not in r:
                return items
            kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    def _pt(m):
        return {"x": float(m["M"]["x"]["N"]), "y": float(m["M"]["y"]["N"])}

    rec = None
    for it in query(f"RECEIPT#{rid}"):
        if it.get("TYPE", {}).get("S") == "RECEIPT":
            rec = it
            break
    if rec is None:
        raise RuntimeError(f"receipt {receipt_id} not found for {image_id}")
    width, height = int(rec["width"]["N"]), int(rec["height"]["N"])

    sk_re = _re.compile(
        r"RECEIPT#\d+#LINE#(\d+)#WORD#(\d+)(?:#LABEL#(.+))?$"
    )
    words, labels = {}, {}
    for it in query(f"RECEIPT#{rid}#LINE"):
        t = it.get("TYPE", {}).get("S")
        m = sk_re.search(it["SK"]["S"])
        if not m:
            continue
        line_id, word_id, label = int(m.group(1)), int(m.group(2)), m.group(3)
        if t == "RECEIPT_WORD" and label is None:
            tl, br = _pt(it["top_left"]), _pt(it["bottom_right"])
            words[(line_id, word_id)] = {
                "text": it["text"]["S"],
                "line_id": line_id,
                "word_id": word_id,
                "bbox": [tl["x"] * 1000, tl["y"] * 1000,
                         br["x"] * 1000, br["y"] * 1000],
            }
        elif t == "RECEIPT_WORD_LABEL" and label not in (None, "O"):
            # first label per word wins (matches the single-label render path)
            labels.setdefault((line_id, word_id), label)
    word_list = []
    for key, w in sorted(words.items()):
        w = dict(w)
        w["labels"] = [labels[key]] if key in labels else []
        word_list.append(w)

    barcodes = []
    for it in query(f"RECEIPT#{rid}#BARCODE"):
        if it.get("TYPE", {}).get("S") != "RECEIPT_BARCODE":
            continue
        barcodes.append({
            "text": it.get("text", {}).get("S", "") or "",
            "symbology": it.get("symbology", {}).get("S", ""),
            "top_left": _pt(it["top_left"]) if "top_left" in it else None,
            "bottom_right": _pt(it["bottom_right"]) if "bottom_right" in it else None,
            "confidence": (float(it["confidence"]["N"])
                           if "confidence" in it else None),
        })
    return width, height, word_list, barcodes


def _cached_payload(cache_dir, table, region, merchant, image_id, receipt_id):
    """Gold receipt payload, cached to disk after the first Dynamo pull. The
    cache key includes the table so a run against a different environment
    cannot silently reuse another table's payload."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(
        cache_dir, f"payload_{table}_{image_id}_{receipt_id}.json"
    )
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    width, height, words, barcodes = _load_receipt_payload(
        table, region, image_id, receipt_id
    )
    doc = {
        "merchant": merchant,
        "image_id": image_id,
        "receipt_id": receipt_id,
        "width": width,
        "height": height,
        "words": words,
        "barcodes": barcodes,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    os.replace(tmp, path)
    return doc


def render_gold(
    out,
    *,
    merchant,
    image_id,
    receipt_id,
    width,
    height,
    table,
    region,
    cache_dir,
    vscale=None,
    face_overrides=None,
    labels_out=None,
):
    import render_synthetic_receipts as rsr

    doc = _cached_payload(cache_dir, table, region, merchant, image_id,
                          receipt_id)
    prof = rsr.cached_font_profile(table, merchant, region=region,
                                   max_receipts=12)
    # shallow-copy before mutating: never leak overrides into a shared/cached
    # profile dict
    typ = dict(rsr.merchant_typography(merchant))
    if vscale is not None:
        typ["bitmap_glyph_vscale"] = float(vscale)
    if face_overrides:
        bf = dict(typ.get("bitmap_font") or {})
        for k, v in face_overrides.items():
            if not os.path.exists(v):
                raise SystemExit(f"--face {k}: atlas not found: {v}")
            bf[k] = v
        typ["bitmap_font"] = bf
    ss = rsr.section_scale_for_merchant(merchant)
    payload = {
        "words": doc["words"],
        "barcodes": doc["barcodes"],
        "merchant_name": merchant,
    }
    box_sink = [] if labels_out else None
    if box_sink is not None:
        typ["box_sink"] = box_sink
    # bitmap_glyph_vscale is an I2 knob not present on every render path; drop it
    # if _render_cached_hybrid does not accept it so the driver stays portable.
    try:
        rsr._render_cached_hybrid(
            copy.deepcopy(payload), None, profile=prof, width=width,
            height=height, path=out, section_scale=ss, **typ,
        )
    except TypeError as e:
        if "bitmap_glyph_vscale" in str(e):
            typ.pop("bitmap_glyph_vscale", None)
            rsr._render_cached_hybrid(
                copy.deepcopy(payload), None, profile=prof, width=width,
                height=height, path=out, section_scale=ss, **typ,
            )
        else:
            raise
    if labels_out and box_sink is not None:
        margin, inner_w, inner_h = 10.0, width - 20.0, height - 20.0
        tokens, bboxes = [], []
        for b in box_sink:
            x0, y0, x1, y1 = (float(v) for v in b["px"])
            box = [
                (x0 - margin) / inner_w * 1000.0,
                (1.0 - (y1 - margin) / inner_h) * 1000.0,
                (x1 - margin) / inner_w * 1000.0,
                (1.0 - (y0 - margin) / inner_h) * 1000.0,
            ]
            box = [min(1000.0, max(0.0, v)) for v in box]
            if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
                continue
            tokens.append(b["text"])
            bboxes.append(box)
        with open(labels_out, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "tokens": tokens,
                    "bboxes": bboxes,
                    "merchant_name": merchant,
                    "receipt_key": f"{image_id}#{receipt_id}",
                    "metadata": {
                        "operation": "re_render_real_receipt",
                        "boxes": "render_true",
                        "render": {"width": width, "height": height,
                                   "margin": 10},
                    },
                },
                fh,
            )
    return out


def _parse_face(items):
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--face expects KEY=PATH, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = os.path.abspath(os.path.expanduser(v.strip()))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merchant", required=True)
    ap.add_argument("--image-id", required=True)
    ap.add_argument("--receipt-id", type=int, required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--vscale", type=float, default=None)
    ap.add_argument("--face", action="append", default=[],
                    help="override a bitmap_font face: KEY=PATH (repeatable)")
    ap.add_argument("--degrade", default=None,
                    help="fitted degrade params JSON applied after render")
    ap.add_argument("--degrade-seed", type=int, default=0)
    ap.add_argument("--labels-out", default=None)
    ap.add_argument("--table",
                    default=os.environ.get("DYNAMODB_TABLE_NAME",
                                           "ReceiptsTable-dc5be22"))
    ap.add_argument("--region",
                    default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--cache-dir",
                    default=os.path.join(_ROOT, ".out", "merchant_gold"))
    ap.add_argument("--expect-sha", default=None)
    args = ap.parse_args(argv)

    render_gold(
        args.out,
        merchant=args.merchant,
        image_id=args.image_id,
        receipt_id=args.receipt_id,
        width=args.width,
        height=args.height,
        table=args.table,
        region=args.region,
        cache_dir=args.cache_dir,
        vscale=args.vscale,
        face_overrides=_parse_face(args.face),
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

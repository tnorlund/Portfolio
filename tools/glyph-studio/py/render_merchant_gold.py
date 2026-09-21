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
so subsequent renders are offline and fast. Gold/export size the grid from
``fonts/<slug>/font.json`` (``pitchRatioTarget``, recorded cap) plus recorded
``bitmap_thin`` in ``vendor.json`` -- they do not rebuild a 12-receipt font
profile or solve thin live. ``--calibrate-from-corpus`` restores that
authoring path.

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

import render_synthetic_receipts as rsr  # noqa: E402
from glyphstudio.source_snapshot import resolve_pinned_payload  # noqa: E402
from glyphstudio.vendor_package import (  # noqa: E402
    GOLD_CANVAS_MARGIN,
    closed_font_height,
    resolve_gold_inputs,
)

from receipt_agent.agents.label_evaluator.rendering.font_profile import (  # noqa: E402
    MerchantFontProfile,
)

_FALLBACK_FONT_HEIGHT = 0.018
_FALLBACK_CHAR_WIDTH = 0.0125


def closed_font_profile(
    merchant,
    pins=None,
    *,
    canvas_height=None,
    canvas_width=None,
    margin=GOLD_CANVAS_MARGIN,
):
    """Deterministic ``MerchantFontProfile`` with no Dynamo 12-receipt build.

    ``font_height`` comes from the recorded cap (``pins["cap_px"]``, the
    font.json ``preview.capPx`` on the 760-wide export canvas, scaled to
    ``canvas_width``) and ``pins["ocr_cap_height_ratio"]`` on the
    ``canvas_height`` the render targets, inverting what ``build_grid_spec``
    and ``_ocr_grid_metrics`` do to the 12-receipt profile. The 0.018
    fallback applies only when no cap is recorded or the canvas is unknown.
    ``line_pitch`` stays ``None`` (no recorded pitch; the OCR row positions
    carry the real spacing).
    """
    pins = pins or {}
    font_height = closed_font_height(
        pins.get("cap_px"),
        pins.get("ocr_cap_height_ratio"),
        canvas_height,
        canvas_width=canvas_width,
        margin=margin,
    )
    if font_height is None:
        font_height = _FALLBACK_FONT_HEIGHT
    pitch = pins.get("pitch_ratio")
    char_width = (
        float(pitch) * font_height
        if pitch is not None
        else _FALLBACK_CHAR_WIDTH
    )
    return MerchantFontProfile(
        merchant_name=merchant,
        receipt_count=0,
        font_height=font_height,
        char_width=char_width,
        char_aspect=char_width / font_height,
        line_pitch=None,
        price_column_x=None,
        dominant_style_label="BODY",
        source_image_ids=(),
    )


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

    sk_re = _re.compile(r"RECEIPT#\d+#LINE#(\d+)#WORD#(\d+)(?:#LABEL#(.+))?$")
    words, labels, skipped_words = {}, {}, 0
    for it in query(f"RECEIPT#{rid}#LINE"):
        t = it.get("TYPE", {}).get("S")
        m = sk_re.search(it["SK"]["S"])
        if not m:
            # An unparsable RECEIPT_WORD SK would silently drop a real word ->
            # a plausible-but-incomplete receipt. Surface it loudly.
            if t == "RECEIPT_WORD":
                skipped_words += 1
                print(
                    f"[render_merchant_gold] WARN: unparsable RECEIPT_WORD SK "
                    f"{it['SK']['S']!r}",
                    file=sys.stderr,
                )
            continue
        line_id, word_id, label = int(m.group(1)), int(m.group(2)), m.group(3)
        if t == "RECEIPT_WORD" and label is None:
            tl, br = _pt(it["top_left"]), _pt(it["bottom_right"])
            words[(line_id, word_id)] = {
                "text": it["text"]["S"],
                "line_id": line_id,
                "word_id": word_id,
                "bbox": [
                    tl["x"] * 1000,
                    tl["y"] * 1000,
                    br["x"] * 1000,
                    br["y"] * 1000,
                ],
            }
        elif t == "RECEIPT_WORD_LABEL" and label not in (None, "O"):
            # Prefer a VALID label over PENDING/INVALID for the same word (the
            # single label the render path styles on); fall back to SK order.
            status = it.get("validation_status", {}).get("S", "")
            key = (line_id, word_id)
            prev = labels.get(key)
            if prev is None or (status == "VALID" and prev[1] != "VALID"):
                labels[key] = (label, status)
    if skipped_words:
        raise RuntimeError(
            f"{skipped_words} RECEIPT_WORD item(s) had unparsable SKs for "
            f"{image_id}#{receipt_id}; refusing to render an incomplete receipt"
        )
    word_list = []
    for key, w in sorted(words.items()):
        w = dict(w)
        w["labels"] = [labels[key][0]] if key in labels else []
        word_list.append(w)

    # Barcodes use the renderer's NORMALIZED (0-1) top_left/bottom_right point
    # contract, matching render_costco_gold.py (words are 0-1000, barcodes are
    # 0-1 dicts -- the renderer scales barcode geometry itself). The Sprouts
    # gold receipt has no barcodes, so this path is untested here but preserved
    # for chart/barcode merchants.
    barcodes = []
    for it in query(f"RECEIPT#{rid}#BARCODE"):
        if it.get("TYPE", {}).get("S") != "RECEIPT_BARCODE":
            continue
        barcodes.append(
            {
                "text": it.get("text", {}).get("S", "") or "",
                "symbology": it.get("symbology", {}).get("S", ""),
                "top_left": _pt(it["top_left"]) if "top_left" in it else None,
                "bottom_right": (
                    _pt(it["bottom_right"]) if "bottom_right" in it else None
                ),
                "confidence": (
                    float(it["confidence"]["N"])
                    if "confidence" in it
                    else None
                ),
            }
        )
    return width, height, word_list, barcodes


def _cached_payload(cache_dir, table, region, merchant, image_id, receipt_id):
    """Gold receipt payload.

    A pinned source snapshot wins over the disk cache and over Dynamo, so a
    re-render does not follow live geometry. Without a pin, the payload is
    cached after the first pull. The cache key includes the table so a run
    against a different environment cannot reuse another table's payload.
    """
    pinned = resolve_pinned_payload(image_id, int(receipt_id))
    if pinned is not None:
        return pinned
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


def closed_gold_inputs(
    merchant,
    typ,
    *,
    table,
    region,
    calibrate_from_corpus=False,
    atlas=None,
    section_scale=None,
    canvas_height=None,
    canvas_width=None,
):
    """Profile + typography for gold/export: git pins, no live 12-receipt thin.

    ``--calibrate-from-corpus`` restores the authoring path
    (``cached_font_profile(n=12)`` + ``resolve_bitmap_thin``).
    ``canvas_height`` / ``canvas_width`` size the closed profile's
    ``font_height`` from the recorded cap (see :func:`closed_font_profile`).
    """
    return resolve_gold_inputs(
        merchant,
        typ,
        table=table,
        region=region,
        rsr=rsr,
        make_profile=closed_font_profile,
        calibrate_from_corpus=calibrate_from_corpus,
        atlas=atlas,
        section_scale=section_scale,
        canvas_height=canvas_height,
        canvas_width=canvas_width,
    )


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
    calibrate_from_corpus=False,
):
    doc = _cached_payload(
        cache_dir, table, region, merchant, image_id, receipt_id
    )
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
    prof, typ = closed_gold_inputs(
        merchant,
        typ,
        table=table,
        region=region,
        calibrate_from_corpus=calibrate_from_corpus,
        section_scale=ss,
        canvas_height=height,
        canvas_width=width,
    )
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
            copy.deepcopy(payload),
            None,
            profile=prof,
            width=width,
            height=height,
            path=out,
            section_scale=ss,
            **typ,
        )
    except TypeError as e:
        if "bitmap_glyph_vscale" in str(e):
            typ.pop("bitmap_glyph_vscale", None)
            rsr._render_cached_hybrid(
                copy.deepcopy(payload),
                None,
                profile=prof,
                width=width,
                height=height,
                path=out,
                section_scale=ss,
                **typ,
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
                        "render": {
                            "width": width,
                            "height": height,
                            "margin": 10,
                        },
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
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--merchant", required=True)
    ap.add_argument("--image-id", required=True)
    ap.add_argument("--receipt-id", type=int, required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--vscale", type=float, default=None)
    ap.add_argument(
        "--face",
        action="append",
        default=[],
        help="override a bitmap_font face: KEY=PATH (repeatable)",
    )
    ap.add_argument(
        "--degrade",
        default=None,
        help="fitted degrade params JSON applied after render",
    )
    ap.add_argument("--degrade-seed", type=int, default=0)
    ap.add_argument("--labels-out", default=None)
    ap.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"),
    )
    ap.add_argument(
        "--region", default=os.environ.get("AWS_REGION", "us-east-1")
    )
    ap.add_argument(
        "--cache-dir", default=os.path.join(_ROOT, ".out", "merchant_gold")
    )
    ap.add_argument(
        "--calibrate-from-corpus",
        action="store_true",
        help="authoring: rebuild cached_font_profile(n=12) and live bitmap_thin",
    )
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
        calibrate_from_corpus=args.calibrate_from_corpus,
    )
    if args.degrade:
        from glyphstudio.degrade import degrade_image_file

        degrade_image_file(
            args.out, args.out, args.degrade, seed=args.degrade_seed
        )
    sha = hashlib.sha256(open(args.out, "rb").read()).hexdigest()
    print(f"{args.out} sha256={sha}")
    if args.expect_sha and sha != args.expect_sha:
        print(f"SHA MISMATCH: expected {args.expect_sha}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""End-to-end demo: QA'd sections -> section-conditioned heavy mint -> measured
face selection -> render.

Composes the whole pipeline on real receipts:

  * the SECTIONS found the bold evidence (the heavy pool that minted the
    candidate came from QA'd VALID ReceiptSection line_ids),
  * the MINT made the true bold glyphs (composite heavy: minted bold chars
    overlaid on the regular atlas -- see compose_heavy_npz.py's manifest),
  * MEASURED FACE SELECTION places them (M4 ``face_source="measured"``:
    per-row typography measured from the real scan routes bold rows to the
    profile's ``bitmap_font.heavy`` slot).

Per receipt this renders:
  P) PRODUCTION -- stylemap text rules + the shipped heavy slot (today a
     uniform dilation of the regular face), and
  E) END-TO-END -- measured row faces + the composite TRUE-bold heavy,
at identical canvas size, then writes a labeled REAL | PRODUCTION | E2E
side-by-side, prints which rows routed to the heavy face and WHY (measured /
box / prior ladder rung), and runs the line scorecard on both renders.

MUST run inside a worktree with the M4 measured-face renderer
(``RenderConfig.face_source``; branch feat/m4-measured-faces or a descendant)
-- it exits with a clear message otherwise.

Usage:
  python e2e_section_heavy_demo.py --heavy-npz COMPOSITE.npz --out-dir OUT \\
      --receipt IMAGE_ID:RECEIPT_ID [--receipt ...] \\
      [--merchant "Sprouts Farmers Market:sprouts"]

Env: DYNAMODB_TABLE_NAME (default dev), AWS creds, BITMATRIX_DIR.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from io import BytesIO

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _p in (
    _HERE,
    os.path.join(_ROOT, "receipt_agent"),
    os.path.join(_ROOT, "receipt_dynamo"),
    os.path.join(_ROOT, "receipt_upload"),
    os.path.join(_ROOT, "synthesis_loop"),
    os.path.join(_ROOT, "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_receipt(client, iid: str, rid: int):
    d = client.get_image_details(iid)
    rec = next(
        (r for r in d.receipts if str(r.receipt_id) == str(rid)), None
    )
    if rec is None:
        raise RuntimeError(f"receipt {rid} not found for {iid}")
    lbl = {
        (l.line_id, l.word_id): l.label
        for l in d.receipt_word_labels
        if l.receipt_id == rid
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
        for w in d.receipt_words
        if w.receipt_id == rid
    ]
    barcodes = []
    if hasattr(client, "list_receipt_barcodes_from_receipt"):
        try:
            barcodes = [
                {
                    "text": getattr(b, "text", "") or "",
                    "symbology": getattr(b, "symbology", ""),
                    "top_left": getattr(b, "top_left", None),
                    "bottom_right": getattr(b, "bottom_right", None),
                    "confidence": getattr(b, "confidence", None),
                }
                for b in client.list_receipt_barcodes_from_receipt(iid, rid)
            ]
        except Exception:  # noqa: BLE001
            barcodes = []
    return rec, words, barcodes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--heavy-npz", required=True, help="composite heavy atlas")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument(
        "--receipt",
        action="append",
        required=True,
        metavar="IMAGE_ID:RECEIPT_ID",
    )
    ap.add_argument("--merchant", default="Sprouts Farmers Market:sprouts")
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)
    merchant, _, slug = args.merchant.partition(":")
    if not merchant.strip() or not slug.strip():
        raise SystemExit(
            f"--merchant must be 'Name:slug' (got {args.merchant!r})"
        )
    if not os.path.exists(args.heavy_npz):
        raise SystemExit(f"--heavy-npz not found: {args.heavy_npz}")

    import boto3
    from PIL import Image, ImageDraw, ImageFont

    import render_synthetic_receipts as rsr
    from receipt_line_scorecard import score_receipt_images

    from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
        RenderConfig,
    )
    from receipt_dynamo.data.dynamo_client import DynamoClient

    if not hasattr(RenderConfig(), "face_source"):
        raise SystemExit(
            "this worktree's renderer has no RenderConfig.face_source -- run "
            "inside the M4 measured-faces branch (feat/m4-measured-faces)"
        )
    from glyphstudio.face_select import select_row_faces
    from glyphstudio.section_face_map import load_merchant_faces
    from glyphstudio.stylescan import measure

    table = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    region = os.environ.get("AWS_REGION", "us-east-1")
    client = DynamoClient(table_name=table, region=region)
    s3 = boto3.client("s3", region_name=region)

    prof = rsr.cached_font_profile(
        table, merchant, region=region, max_receipts=12
    )
    ss = rsr.section_scale_for_merchant(merchant)
    typ = rsr.merchant_typography(merchant)
    if "bitmap_font" not in typ:
        raise SystemExit(f"{merchant}: bitmap_font atlases not resolvable")
    priors = None
    prior_dir = os.path.join(_ROOT, "tools", "glyph-studio", "fonts", slug)
    if os.path.isdir(prior_dir):
        try:
            priors = load_merchant_faces(prior_dir)
        except Exception:  # noqa: BLE001
            priors = None

    try:
        lab = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Arial.ttf", 15
        )
    except OSError:
        lab = ImageFont.load_default()

    report = []
    for spec_arg in args.receipt:
        iid, _, rid_s = spec_arg.partition(":")
        rid = int(rid_s)
        tag = f"{slug}_{iid[:8]}_{rid}"
        rec, words, barcodes = _load_receipt(client, iid, rid)

        measurement = measure(iid, rid, merchant=slug)
        row_faces, ladder = select_row_faces(
            measurement, section_priors=priors
        )
        heavy_rows = {
            k: v["source"] for k, v in row_faces.items() if v["face"] == "heavy"
        }

        if not rec.width or not rec.height or rec.width <= 0 or rec.height <= 0:
            raise SystemExit(
                f"{iid}:{rid}: bad receipt dimensions "
                f"{rec.width}x{rec.height}"
            )
        wt = 760
        ht = int(round(wt * rec.height / rec.width))
        payload = {
            "words": words,
            "barcodes": barcodes,
            "merchant_name": merchant,
        }
        variants = {
            # P: today's pipeline exactly -- stylemap rules + shipped heavy.
            "production": {},
            # E: measured faces + the section-minted TRUE bold composite.
            "e2e": {
                "face_source": "measured",
                "row_faces": row_faces,
                "bitmap_font": {
                    "regular": typ["bitmap_font"]["regular"],
                    "heavy": args.heavy_npz,
                },
            },
        }
        paths, scores = {}, {}
        for mode, extra in variants.items():
            out = os.path.join(args.out_dir, f"{tag}.{mode}.png")
            t = dict(typ)
            t.update(extra)
            rsr._render_cached_hybrid(
                copy.deepcopy(payload),
                None,
                profile=prof,
                width=wt,
                height=ht,
                path=out,
                section_scale=ss,
                **t,
            )
            paths[mode] = out

        real = None
        for bkt, key in (
            (rec.cdn_s3_bucket, rec.cdn_s3_key),
            (rec.raw_s3_bucket, rec.raw_s3_key),
        ):
            if not bkt or not key:
                continue
            try:
                real = Image.open(
                    BytesIO(
                        s3.get_object(Bucket=bkt, Key=key)["Body"].read()
                    )
                ).convert("RGB")
                break
            except Exception:  # noqa: BLE001
                continue
        if real is not None:
            real_m = real.resize((wt, ht))
            for mode, p in paths.items():
                syn = Image.open(p).convert("RGB")
                try:
                    sm = score_receipt_images(real_m, syn, words)["summary"]
                    scores[mode] = sm["severity_counts"]
                except Exception as e:  # noqa: BLE001
                    scores[mode] = {"error": str(e)}

        # labeled side-by-side, shared canvas height
        wd = 380

        def _byw(im):
            w, h = im.size
            return im.resize((wd, int(h * wd / w)))

        panels = ([("REAL", real)] if real is not None else []) + [
            ("PRODUCTION (stylemap+dilation)", Image.open(paths["production"])),
            ("END-TO-END (measured+true bold)", Image.open(paths["e2e"])),
        ]
        panels = [(t_, _byw(im.convert("RGB"))) for t_, im in panels]
        hh = max(im.height for _, im in panels) + 40
        cv = Image.new(
            "RGB",
            (wd * len(panels) + 20 * (len(panels) + 1), hh),
            (235, 235, 235),
        )
        dd = ImageDraw.Draw(cv)
        x = 20
        for t_, im in panels:
            cv.paste(im, (x, 34))
            dd.text((x, 12), t_, fill=(200, 0, 0), font=lab)
            x += wd + 20
        side = os.path.join(args.out_dir, f"e2e_{tag}.png")
        cv.save(side)

        report.append(
            {
                "receipt": f"{iid}:{rid}",
                "side_by_side": side,
                "heavy_rows": heavy_rows,
                "ladder_stats": ladder,
                "scorecard": scores,
            }
        )
        print(json.dumps(report[-1], indent=1))

    with open(
        os.path.join(args.out_dir, "e2e_report.json"), "w", encoding="utf-8"
    ) as fh:
        json.dump(report, fh, indent=1)
    # A demo that could not score a render is not a completed comparison.
    score_errors = any(
        isinstance(v, dict) and "error" in v
        for r in report
        for v in (r.get("scorecard") or {}).values()
    )
    return 1 if score_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""M4 pilot driver: A/B stylemap text rules vs MEASURED typography (Wild Fork).

For every vetted receipt of the merchant (OCR x-overlap score <= 2, the M3
vetting rule), this:

1. measures per-line typography from the REAL scan (glyphstudio.stylescan),
2. builds the measured row->face map (glyphstudio.face_select; fallback
   ladder measurement -> M2 section prior -> renderer stylemap rules),
3. renders the receipt twice at identical canvas size -- face_source
   "stylemap" (production default) vs "measured" (opt-in path),
4. scores per-line FACE AGREEMENT of each path against the measured truth,
5. runs the production scorecard (receipt_line_scorecard) on both renders,
6. saves one labeled side-by-side per receipt: real | stylemap | measured.

Usage:
  python m4_face_pilot.py --out-dir /tmp/m4_pilot \
      [--merchant "Wild Fork:wildfork"] [--limit 10]

Env: PYTHONPATH=receipt_agent:receipt_dynamo:receipt_upload:synthesis_loop:
scripts, DYNAMODB_TABLE_NAME, AWS_REGION, PORTFOLIO_ENV=dev,
RECEIPT_PAPER_STRENGTH=0.3 (render calibration), optional BITMATRIX_DIR.
"""

from __future__ import annotations

import argparse
import copy
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
    os.path.join(_ROOT, "synthesis_loop"),
    os.path.join(_ROOT, "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MAX_OVERLAPS = 2  # M3 vetting threshold
LARGE = 1.30  # scale bucket boundary (matches face_select.LARGE_CAP)


def _truth_from_line(line, body_cap, body_stroke):
    """Measured (large, heavy, underline) truth triple, or None.

    Truth comes from the selector's LETTERS rung only (the strongest
    evidence, noise-guarded and hand-checked); lines it cannot measure are
    excluded from agreement scoring rather than judged against weak rungs.
    """
    from glyphstudio.face_select import measured_style_for_line

    style = measured_style_for_line(line, body_cap, body_stroke)
    if style is None:
        return None
    return (
        float(style["scale"]) >= LARGE,
        style["face"] == "heavy",
        bool(style["underline"]),
    )


def _pred_from_style(style):
    return (
        float(style["scale"]) >= LARGE,
        bool(style["bold"]),
        bool(style["underline"]),
    )


def _agreement(measurement, row_faces, stylemap_json):
    """Per-line agreement of both paths against the measured truth."""
    from glyphstudio.face_select import normalize_face_key

    from receipt_agent.agents.label_evaluator.rendering.receipt_stylemap import (  # noqa: E501
        measured_row_style,
        row_style,
    )

    body_cap = measurement.get("body_cap_px")
    body_stroke = measurement.get("body_stroke_px")
    rows = []
    for line in measurement.get("lines") or ():
        truth = _truth_from_line(line, body_cap, body_stroke)
        if truth is None:
            continue
        text = line.get("text") or ""
        rule = row_style(stylemap_json, text.upper())
        # The measured render's effective style: covered row -> measured
        # entry; uncovered (conflict-dropped/skipped) -> stylemap fallback,
        # exactly what the renderer does.
        key = normalize_face_key(text)
        entry = row_faces.get(key)
        meas = measured_row_style({key: entry}, text) if entry else None
        rows.append(
            {
                "text": text,
                "truth": truth,
                "stylemap": _pred_from_style(rule),
                "measured": _pred_from_style(meas if meas else rule),
                "measured_source": "measured" if meas else "fallback",
            }
        )
    return rows


def _summarize(rows):
    out = {}
    for path in ("stylemap", "measured"):
        n = len(rows)
        attr_hits = [0, 0, 0]
        full = 0
        for r in rows:
            hits = [int(a == b) for a, b in zip(r[path], r["truth"])]
            attr_hits = [x + y for x, y in zip(attr_hits, hits)]
            full += int(all(hits))
        out[path] = {
            "n": n,
            "scale_tier": attr_hits[0] / n if n else None,
            "face": attr_hits[1] / n if n else None,
            "underline": attr_hits[2] / n if n else None,
            "all_three": full / n if n else None,
        }
    return out


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
            for b in client.list_receipt_barcodes_from_receipt(image_id, receipt_id):
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


def _panelize(images, labels, out_png, panel_w=400):
    from PIL import Image, ImageDraw, ImageFont

    try:
        lab = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 16)
    except OSError:
        lab = ImageFont.load_default()
    resized = [
        im.resize((panel_w, int(im.height * panel_w / im.width))) for im in images
    ]
    hh = max(im.height for im in resized) + 40
    cv = Image.new(
        "RGB",
        (panel_w * len(resized) + 20 * (len(resized) + 1), hh),
        (235, 235, 235),
    )
    dd = ImageDraw.Draw(cv)
    x = 20
    for t, im in zip(labels, resized):
        cv.paste(im, (x, 34))
        dd.text((x, 12), t, fill=(200, 0, 0), font=lab)
        x += panel_w + 20
    cv.save(out_png)
    # header zoom (the region where WF's face differences live)
    crop = cv.crop((0, 0, cv.width, 34 + int((hh - 34) * 0.22)))
    crop = crop.resize((crop.width * 2, crop.height * 2))
    crop.save(os.path.splitext(out_png)[0] + ".zoom_header.png")


def _scorecard(real, syn, words):
    from glyph_review import _ink_metrics
    from receipt_line_scorecard import score_receipt_images

    report = score_receipt_images(real, syn, words)
    counts = report["summary"]["severity_counts"]
    rm, sm = _ink_metrics(real, words), _ink_metrics(syn, words)
    gates = {}
    if rm and sm:
        gates = {
            "h_ratio": round(sm["h_med"] / max(1.0, rm["h_med"]), 3),
            "wpc_ratio": round(sm["wpc_med"] / max(1.0, rm["wpc_med"]), 3),
            "density_ratio": round(sm["density_med"] / max(1e-6, rm["density_med"]), 3),
        }
    return {
        "blockers": counts.get("BLOCKER", 0),
        "minors": counts.get("MINOR", 0),
        **gates,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--merchant", default="Wild Fork:wildfork")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)
    merchant, _, slug = args.merchant.partition(":")

    from io import BytesIO

    import boto3
    import render_synthetic_receipts as rsr
    from glyph_review import _ink_metrics  # noqa: F401  (path check)
    from glyphstudio.face_select import select_row_faces
    from glyphstudio.section_face_map import load_merchant_faces
    from glyphstudio.stylescan import measure
    from m3_acceptance import ocr_overlap_score
    from PIL import Image
    from receipt_dynamo.data.dynamo_client import DynamoClient

    table = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    region = os.environ.get("AWS_REGION", "us-east-1")
    client = DynamoClient(table_name=table, region=region)
    s3 = boto3.client("s3", region_name=region)

    prof = rsr.cached_font_profile(table, merchant, region=region, max_receipts=12)
    ss = rsr.section_scale_for_merchant(merchant)
    typ = rsr.merchant_typography(merchant)
    priors = load_merchant_faces(
        os.path.join(_ROOT, "tools", "glyph-studio", "fonts", slug)
    )

    places, _ = client.get_receipt_places_by_merchant(merchant_name=merchant, limit=50)
    all_rows, per_receipt = [], {}
    for p in places:
        if len(per_receipt) >= args.limit:
            break
        iid, rid = p.image_id, p.receipt_id
        tag = f"{iid[:8]}_{rid}"
        try:
            rec, words, barcodes = _load_receipt_payload(client, merchant, iid, rid)
        except Exception as exc:  # noqa: BLE001
            print(f"[skip] {tag}: load failed ({exc})")
            continue
        overlaps = ocr_overlap_score(words)
        if overlaps > MAX_OVERLAPS:
            print(f"[vet] {tag}: rejected ({overlaps} x-overlap pairs)")
            continue

        measurement = measure(iid, rid, merchant=slug)
        row_faces, stats = select_row_faces(measurement, section_priors=priors)
        rows = _agreement(measurement, row_faces, typ.get("stylemap"))

        # A/B renders at identical canvas size (locked rule: same heights).
        wt = 760
        ht = int(round(wt * rec.height / rec.width))
        payload = {
            "words": words,
            "barcodes": barcodes,
            "merchant_name": merchant,
        }
        paths = {}
        for mode, extra in (
            ("stylemap", {}),
            (
                "measured",
                {"face_source": "measured", "row_faces": row_faces},
            ),
        ):
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
                    BytesIO(s3.get_object(Bucket=bkt, Key=key)["Body"].read())
                ).convert("RGB")
                break
            except Exception:  # noqa: BLE001
                continue
        if real is None:
            print(f"[skip] {tag}: no loadable real image")
            continue

        syn_a = Image.open(paths["stylemap"]).convert("RGB")
        syn_b = Image.open(paths["measured"]).convert("RGB")
        side = os.path.join(args.out_dir, f"{tag}.side_by_side.png")
        _panelize([real, syn_a, syn_b], ["REAL", "STYLEMAP", "MEASURED"], side)

        real_m = real.resize((wt, ht))
        cards = {
            m: _scorecard(real_m, im, words)
            for m, im in (("stylemap", syn_a), ("measured", syn_b))
        }
        # Pool agreement rows only for receipts that made it all the way
        # (else pooled vs per-receipt describe different populations).
        all_rows.extend(rows)
        per_receipt[tag] = {
            "image_id": iid,
            "receipt_id": rid,
            "selector_stats": stats,
            "agreement": _summarize(rows),
            "scorecard": cards,
            "side_by_side": side,
        }
        print(
            f"[ok] {tag}: rows={len(rows)} selector={stats} "
            f"scorecard stylemap={cards['stylemap']} "
            f"measured={cards['measured']}"
        )

    summary = {
        "merchant": args.merchant,
        "receipts": per_receipt,
        "pooled_agreement": _summarize(all_rows),
        "pooled_rows": len(all_rows),
    }
    out_json = os.path.join(args.out_dir, "m4_pilot_summary.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
    print(json.dumps(summary["pooled_agreement"], indent=1))
    print(f"-> {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

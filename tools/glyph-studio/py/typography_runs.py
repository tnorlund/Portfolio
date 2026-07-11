#!/usr/bin/env python3
"""Typography-runs pilot: per-line font attribution + typeface discovery.

For each merchant: OCR-vetted receipts (``ocr_overlap_score`` <= 2, the M3
rule) -> per visual line, cleaned letter masks -> attribution score vs the
merchant's compiled body atlas (median shifted IoU in the M2 shape space).
Poorly-attributed lines are clustered to discover the merchant's typeface set
T1..Tk; contiguous lines sharing (typeface, tier, underline, reverse-video)
become STYLE RUNS; runs are cross-tabbed against the QA'd VALID
ReceiptSection rows to quantify how often a semantic section spans more than
one typographic run.

MEASUREMENT ONLY: no renderer changes, no Dynamo writes, no publishes.
Extraction is cached per receipt (network blips resume cheaply).

Usage:
  python typography_runs.py [--merchant "Wild Fork:wildfork"] \
      [--merchant "Sprouts Farmers Market:sprouts"] [--receipts 12] \
      [--out-dir ../../../.out/typography]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from statistics import median

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _p in (
    _HERE,
    os.path.join(_ROOT, "receipt_dynamo"),
    os.path.join(_ROOT, "synthesis_loop"),
    os.path.join(_ROOT, "receipt_agent"),
    os.path.join(_ROOT, "receipt_upload"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from m3_acceptance import ocr_overlap_score  # noqa: E402

from glyphstudio.family_cluster import normalize_glyph  # noqa: E402
from glyphstudio.stylescan import (  # noqa: E402
    _run_widths,
    group_visual_lines,
    reverse_video_probe,
    underline_probe,
)
from glyphstudio.typography import (  # noqa: E402
    LineShape,
    assign_tiers,
    attribution_ious,
    build_style_runs,
    calibrated_deviation,
    clean_letter_mask,
    cluster_line_shapes,
    estimate_slant,
    exemplar_glyphs,
    intra_line_overlap,
    line_slant,
    per_char_medians,
    section_run_crosstab,
)

DEFAULT_MERCHANTS = ["Wild Fork:wildfork", "Sprouts Farmers Market:sprouts"]
# A line is a different-typeface CANDIDATE when its char-calibrated deviation
# (see per_char_medians / calibrated_deviation) sits this far below its own
# receipt's median deviation. Two calibrations, both measured as necessary:
# per-CHAR because the traced atlases have per-char weaknesses (WF '.' 0.21,
# 'e' 0.41 — raw medians false-flag lowercase/punct-heavy lines like
# 'Tender:'); per-RECEIPT because absolute IoU is resolution-sensitive (a
# 9px-cap Sprouts scan medians ~0.42 where a 28px one medians ~0.52).
REL_DELTA = 0.12
# Line-pair linking threshold, calibrated on the known-answer split: at 0.45
# single-linkage chains Sprouts' serif FARMERS MARKET onto the italic promo
# face through 0.47-0.50 cross-face pairs; at 0.50 they separate exactly
# (intra-face pairs run 0.47-0.80, cross-face 0.34-0.52).
CLUSTER_THRESHOLD = 0.50
CONTAMINATION_MAX = 0.20  # intra-line box-overlap fraction above this = flag
MIN_LETTERS = 4
ITALIC_DEG = 8.0  # |line slant| >= this counts as a true italic candidate
VIZ_COLORS = {
    "T0": (0, 160, 60),
    "T1": (220, 40, 40),
    "T2": (30, 80, 220),
    "T3": (160, 40, 200),
    "T4": (0, 150, 170),
    "T5": (150, 90, 20),
    "T6": (200, 30, 120),
    "T?": (240, 150, 0),
    "X": (120, 120, 120),
}


# --- phase A: extraction (network + pixels, cached per receipt) --------------


def _extract_receipt(client, merchant: str, image_id: str, receipt_id: int):
    """One receipt -> per-visual-line records + cleaned letter masks."""
    from receipt_line_scorecard import _load_words_and_real

    from glyph_segment import auto_polarity, sauvola_mask

    real, words = _load_words_and_real(merchant, image_id, receipt_id)
    overlaps = ocr_overlap_score(words)
    gray = np.asarray(real.convert("L"))
    H, W = gray.shape
    details = client.get_image_details(image_id)
    letters = [
        l
        for l in details.receipt_letters
        if str(l.receipt_id) == str(receipt_id)
    ]
    sections = [
        {"section_type": str(s.section_type), "line_ids": list(s.line_ids)}
        for s in client.get_receipt_sections_from_receipt(image_id, receipt_id)
        if getattr(s, "validation_status", None) == "VALID"
    ]

    ws = []
    for w in words:
        x0, y0, x1, y1 = w["bbox"]
        left, right = min(x0, x1) / 1000 * W, max(x0, x1) / 1000 * W
        top = (1 - max(y0, y1) / 1000) * H
        bottom = (1 - min(y0, y1) / 1000) * H
        ws.append(
            {
                "text": w["text"],
                "line_id": w.get("line_id"),
                "l": left,
                "r": right,
                "t": top,
                "b": bottom,
                "cy": (top + bottom) / 2,
                "h": bottom - top,
            }
        )
    vlines = group_visual_lines(ws)

    letters_by_line: dict[int, list] = defaultdict(list)
    for l in letters:
        letters_by_line[int(l.line_id)].append(l)

    def box_px(obj):
        tl, br = obj.top_left, obj.bottom_right
        return (
            min(tl["x"], br["x"]) * W,
            (1 - max(tl["y"], br["y"])) * H,
            max(tl["x"], br["x"]) * W,
            (1 - min(tl["y"], br["y"])) * H,
        )

    line_meta, masks, mask_line_idx, mask_chars, mask_slants = (
        [],
        [],
        [],
        [],
        [],
    )
    for idx, line in enumerate(vlines):
        line.sort(key=lambda w: w["l"])
        lt = min(w["t"] for w in line)
        lb = max(w["b"] for w in line)
        ll = min(w["l"] for w in line)
        lr = max(w["r"] for w in line)
        line_ids = sorted(
            {int(w["line_id"]) for w in line if w["line_id"] is not None}
        )
        boxes, caps, strokes, dens, n_letters = [], [], [], [], 0
        for lid in line_ids:
            for l in letters_by_line[lid]:
                x0, y0, x1, y1 = box_px(l)
                if not (y0 >= lt - 5 and y1 <= lb + 5):
                    continue
                xi0, yi0 = max(0, int(x0)), max(0, int(y0))
                xi1, yi1 = min(W, int(x1) + 1), min(H, int(y1) + 1)
                if xi1 - xi0 < 3 or yi1 - yi0 < 3:
                    continue
                ch = str(l.text or "")[:1]
                if not ch.strip():
                    continue
                boxes.append((x0, y0, x1, y1))
                crop, _ = auto_polarity(gray[yi0:yi1, xi0:xi1])
                mask = clean_letter_mask(sauvola_mask(crop))
                if mask.sum() < 8:
                    continue
                n_letters += 1
                ys, xs = np.where(mask)
                ink_h = int(ys.max() - ys.min() + 1)
                if ch.isupper() or ch.isdigit():
                    caps.append(float(ink_h))
                runs = _run_widths(mask)
                if runs:
                    strokes.append(float(np.mean(runs)))
                dens.append(float(mask.mean()))
                masks.append(normalize_glyph(mask))
                mask_line_idx.append(idx)
                mask_chars.append(ch)
                # slant is only meaningful on glyphs tall enough to lean
                mask_slants.append(
                    estimate_slant(mask) if ink_h >= 8 else float("nan")
                )
        line_meta.append(
            {
                "index": idx,
                "text": " ".join(w["text"] for w in line)[:60],
                "line_ids": line_ids,
                "bbox": [round(v, 1) for v in (ll, lt, lr, lb)],
                "cap_px": round(median(caps), 1) if caps else None,
                "stroke_med": round(median(strokes), 2) if strokes else None,
                "density_med": round(median(dens), 4) if dens else None,
                "n_letters": n_letters,
                "contamination": round(intra_line_overlap(boxes), 3),
                "underline": bool(underline_probe(gray, lt, lb, ll, lr)),
                "reverse_video": int(reverse_video_probe(gray, lt, lb, ll, lr)),
            }
        )
    meta = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant": merchant,
        "image_size": [W, H],
        "ocr_overlap_score": overlaps,
        "lines": line_meta,
        "sections": sections,
    }
    return meta, masks, mask_line_idx, mask_chars, mask_slants


# Bump when extraction output changes (cleaning, grouping, slant, meta keys):
# stale caches must re-extract, not silently reproduce old findings.
CACHE_SCHEMA = 2


def load_or_extract(client, merchant, slug, image_id, receipt_id, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{slug}_{image_id}_{receipt_id}.npz")
    if os.path.exists(path):
        try:
            d = np.load(path, allow_pickle=False)
            meta = json.loads(str(d["meta"]))
            if meta.get("cache_schema") == CACHE_SCHEMA:
                return (
                    meta,
                    d["masks"].astype(bool),
                    d["line_idx"].tolist(),
                    [str(c) for c in d["chars"]],
                    d["slants"].tolist(),
                )
            print(
                f"  [cache] {os.path.basename(path)}: schema "
                f"{meta.get('cache_schema')} != {CACHE_SCHEMA}, re-extracting",
                file=sys.stderr,
            )
        except Exception as e:  # noqa: BLE001 - truncated/corrupt = miss
            print(
                f"  [cache] {os.path.basename(path)} unreadable ({e}), "
                "re-extracting",
                file=sys.stderr,
            )
        os.remove(path)
    meta, masks, line_idx, chars, slants = _extract_receipt(
        client, merchant, image_id, receipt_id
    )
    meta["cache_schema"] = CACHE_SCHEMA
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:  # atomic publish: a killed run can't leave
        np.savez_compressed(  # a half-written npz behind
            fh,
            meta=json.dumps(meta),
            masks=(
                np.stack(masks).astype(bool)
                if masks
                else np.zeros((0, 32, 32), bool)
            ),
            line_idx=np.array(line_idx, dtype=np.int32),
            chars=np.array(chars, dtype="U1"),
            slants=np.array(slants, dtype=np.float32),
        )
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return meta, masks, line_idx, chars, slants


# --- phase B: analysis (pure, from caches) -----------------------------------


def compile_atlas(slug: str, out_dir: str) -> dict[str, np.ndarray]:
    """Compile fonts/<slug> and shape-normalize its glyphs (body atlas)."""
    from glyphstudio.compile import main as compile_main

    npz = os.path.join(out_dir, f"{slug}.glyphs.npz")
    if not os.path.exists(npz):
        compile_main(
            [os.path.join(_ROOT, "tools", "glyph-studio", "fonts", slug), npz]
        )
    data = np.load(npz)
    return {
        chr(int(k[1:])): normalize_glyph(data[k])
        for k in data.files
        if k.startswith("c")
    }


def analyze_merchant(receipts, atlas, args):
    """Attribution -> discovery -> tiers -> runs -> crosstab, per merchant."""
    # pass 1: per-letter IoUs vs atlas + the merchant's per-char norms
    all_lines = []  # (receipt, line_record, letters) refs
    corpus_ious = []
    for rec in receipts:
        meta = rec["meta"]
        per_line = defaultdict(list)
        per_line_slant = defaultdict(list)
        for m, li, ch, sl in zip(
            rec["masks"], rec["line_idx"], rec["chars"], rec["slants"]
        ):
            per_line[li].append((ch, m))
            per_line_slant[li].append((ch, sl))
        rec["per_line"] = per_line
        rec["per_line_ious"] = {
            li: attribution_ious(letters, atlas)
            for li, letters in per_line.items()
        }
        for line in meta["lines"]:
            if line["contamination"] <= args.contamination:
                corpus_ious.extend(rec["per_line_ious"].get(line["index"], []))
        rec["per_line_slant"] = per_line_slant
    # pass 2: per-line attribution (raw + slant, char_med-independent)
    for rec in receipts:
        meta = rec["meta"]
        for line in meta["lines"]:
            ious = rec["per_line_ious"].get(line["index"], [])
            letters = rec["per_line"].get(line["index"], [])
            vals = [v for _, v in ious]
            line["attr_n"] = len(vals)
            line["attribution"] = (
                round(float(median(vals)), 4)
                if len(vals) >= args.min_letters
                else None
            )
            sl = line_slant(rec["per_line_slant"].get(line["index"], []))
            line["slant_deg"] = round(sl, 1) if sl is not None else None
            line["contaminated"] = line["contamination"] > args.contamination
            all_lines.append((rec, line, letters))

    def _score(char_med):
        """Char-calibrated deviations + receipt centering + candidates."""
        for rec in receipts:
            meta = rec["meta"]
            for line in meta["lines"]:
                dev = calibrated_deviation(
                    rec["per_line_ious"].get(line["index"], []),
                    char_med,
                    min_letters=args.min_letters,
                )
                line["attr_dev"] = round(dev, 4) if dev is not None else None
            devs = [
                l["attr_dev"]
                for l in meta["lines"]
                if l["attr_dev"] is not None and not l["contaminated"]
            ]
            rec_med = float(np.median(devs)) if devs else None
            for line in meta["lines"]:
                line["attr_dev_rel"] = (
                    round(line["attr_dev"] - rec_med, 4)
                    if line["attr_dev"] is not None and rec_med is not None
                    else None
                )
        return [
            (rec, line, letters)
            for rec, line, letters in all_lines
            if line["attr_dev_rel"] is not None
            and line["attr_dev_rel"] < -args.rel_delta
            and not line["contaminated"]
        ]

    # discovery: cluster the poorly-attributed, uncontaminated lines.
    # Two rounds: char norms first include every uncontaminated line, then
    # the first round's candidates are dropped and norms recomputed, so a
    # display face that prints a char often (Sprouts' address digits) can't
    # pull that char's "body" norm toward itself and hide.
    cand = _score(per_char_medians(corpus_ious))
    cand_line_ids = {id(line) for _, line, _ in cand}
    corpus2 = [
        iou
        for rec in receipts
        for l in rec["meta"]["lines"]
        if id(l) not in cand_line_ids
        and l["contamination"] <= args.contamination
        for iou in rec["per_line_ious"].get(l["index"], [])
    ]
    cand = _score(per_char_medians(corpus2))
    shapes = []
    for rec, line, letters in cand:
        key = f"{rec['meta']['image_id'][:8]}#{rec['meta']['receipt_id']}:{line['index']}"
        by_char: dict[str, list] = defaultdict(list)
        for ch, g in letters:
            by_char[ch].append(g.astype(bool))
        shapes.append(
            LineShape(
                key=key,
                glyphs={
                    ch: (np.mean(np.stack(gs).astype(float), 0) >= 0.5)
                    for ch, gs in by_char.items()
                },
                text=line["text"],
            )
        )
    clusters = cluster_line_shapes(
        shapes,
        threshold=args.cluster_threshold,
        min_shared=args.min_shared,
    )
    # typeface labels: T0 = body; T1..Tk = discovered clusters (>=2 lines),
    # T? = unclustered candidates; X = contaminated (excluded, never guessed)
    label_of_shape: dict[int, str] = {}
    discovered = []
    k = 0
    for cl in clusters:
        if len(cl) >= 2:
            k += 1
            name = f"T{k}"
            for i in cl:
                label_of_shape[i] = name
            receipts_spanned = {shapes[i].key.split(":")[0] for i in cl}
            discovered.append(
                {
                    "typeface": name,
                    "n_lines": len(cl),
                    # a real merchant typeface recurs across receipts; a
                    # single-receipt cluster is a local anomaly (blur, tape)
                    "n_receipts": len(receipts_spanned),
                    "exemplar_chars": sorted(
                        exemplar_glyphs([shapes[i] for i in cl])
                    ),
                    "sample_texts": [shapes[i].text for i in cl[:6]],
                    "members": [shapes[i].key for i in cl],
                }
            )
        else:
            for i in cl:
                label_of_shape[i] = "T?"
    shape_idx = {id(line): n for n, (_, line, _) in enumerate(cand)}
    for rec, line, letters in all_lines:
        if line["contaminated"]:
            line["typeface"] = "X"
        elif line["attribution"] is None:
            line["typeface"] = None
        elif id(line) not in shape_idx:
            line["typeface"] = "T0"
        else:
            line["typeface"] = label_of_shape.get(shape_idx[id(line)], "T?")

    # exemplars per discovered typeface (for the viz sheet)
    exemplars = {}
    for d, cl in zip(discovered, [c for c in clusters if len(c) >= 2]):
        exemplars[d["typeface"]] = exemplar_glyphs([shapes[i] for i in cl])

    # 3. tiers + runs + crosstab per receipt
    per_receipt = []
    for rec in receipts:
        meta = rec["meta"]
        lines = meta["lines"]
        body_cap, body_stroke = assign_tiers(lines)
        runs = build_style_runs(lines)
        xt = section_run_crosstab(meta["sections"], runs)
        per_receipt.append(
            {
                "meta": meta,
                "runs": runs,
                "crosstab": xt,
                "body_cap": body_cap,
                "body_stroke": body_stroke,
            }
        )
    return per_receipt, discovered, exemplars, all_lines


# --- outputs ------------------------------------------------------------------


def write_run_map(out_dir, slug, pr):
    meta = pr["meta"]
    path = os.path.join(
        out_dir,
        slug,
        "run_maps",
        f"{meta['image_id']}_{meta['receipt_id']}.json",
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {
        "image_id": meta["image_id"],
        "receipt_id": meta["receipt_id"],
        "merchant": meta["merchant"],
        "ocr_overlap_score": meta["ocr_overlap_score"],
        "body_cap_px": pr["body_cap"],
        "body_stroke_px": pr["body_stroke"],
        "lines": [
            {
                k: line[k]
                for k in (
                    "index",
                    "text",
                    "line_ids",
                    "typeface",
                    "tier",
                    "attribution",
                    "attr_dev",
                    "attr_dev_rel",
                    "attr_n",
                    "slant_deg",
                    "underline",
                    "reverse_video",
                    "contaminated",
                    "cap_px",
                    "stroke_med",
                )
            }
            for line in meta["lines"]
        ],
        "runs": [
            {
                "run_id": r.run_id,
                "typeface": r.typeface,
                "tier": r.tier,
                "underline": r.underline,
                "reverse_video": r.reverse_video,
                "n_lines": len(r.line_indices),
                "line_ids": r.line_ids,
            }
            for r in pr["runs"]
        ],
        "sections": meta["sections"],
        "crosstab": pr["crosstab"],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    return path


def render_overlay(out_dir, slug, pr, client, merchant):
    """Receipt image with line boxes color-coded by attributed typeface."""
    from PIL import Image, ImageDraw

    from receipt_line_scorecard import _load_words_and_real

    meta = pr["meta"]
    try:
        real, _ = _load_words_and_real(
            merchant, meta["image_id"], meta["receipt_id"]
        )
    except Exception as e:  # noqa: BLE001
        print(f"  viz skip {meta['image_id'][:8]}: {e}", file=sys.stderr)
        return None
    img = real.convert("RGB")
    dr = ImageDraw.Draw(img)
    for line in meta["lines"]:
        tf = line.get("typeface")
        if tf is None:
            continue
        color = VIZ_COLORS.get(tf, (240, 150, 0))
        ll, lt, lr, lb = line["bbox"]
        dr.rectangle([ll - 2, lt - 2, lr + 2, lb + 2], outline=color, width=3)
        tag = tf + ("/B" if line.get("tier") == "bold" else "") + (
            "/L" if line.get("tier") == "large" else ""
        )
        dr.text((lr + 6, lt), tag, fill=color)
    path = os.path.join(
        out_dir, slug, f"viz_{meta['image_id'][:8]}_{meta['receipt_id']}.png"
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    return path


def render_exemplars(out_dir, slug, exemplars, atlas):
    """Sheet: body atlas row + one row per discovered typeface.

    Columns are the union of the DISCOVERED faces' chars (letters/digits
    first — punctuation exemplars are rarely diagnostic), so each discovered
    row is dense and directly comparable against the atlas row above it.
    """
    from PIL import Image, ImageDraw

    rows = [("T0 (atlas)", atlas)] + sorted(exemplars.items())
    seen = {c for name, g in rows[1:] for c in g} or set(atlas)
    order = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    )
    chars = [c for c in order if c in seen][:40]
    cell, label_w = 36, 90
    img = Image.new(
        "L", (label_w + cell * len(chars) + 4, cell * len(rows) + 4), 255
    )
    dr = ImageDraw.Draw(img)
    for r, (name, glyphs) in enumerate(rows):
        dr.text((4, r * cell + 12), name, fill=0)
        for c, ch in enumerate(chars):
            if ch in glyphs:
                g = np.asarray(glyphs[ch], bool)
                tile = Image.fromarray(
                    np.where(g, 0, 255).astype(np.uint8)
                )
                img.paste(tile, (label_w + c * cell + 2, r * cell + 2))
    path = os.path.join(out_dir, slug, "typeface_exemplars.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    return path, [name for name, _ in rows], chars


# --- main ----------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--merchant", action="append", default=None)
    ap.add_argument("--receipts", type=int, default=12)
    ap.add_argument(
        "--out-dir", default=os.path.join(_ROOT, ".out", "typography")
    )
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--rel-delta", type=float, default=REL_DELTA)
    ap.add_argument(
        "--cluster-threshold", type=float, default=CLUSTER_THRESHOLD
    )
    ap.add_argument("--min-shared", type=int, default=3)
    ap.add_argument("--min-letters", type=int, default=MIN_LETTERS)
    ap.add_argument("--contamination", type=float, default=CONTAMINATION_MAX)
    ap.add_argument("--max-overlaps", type=int, default=2)
    ap.add_argument("--viz", type=int, default=3)
    ap.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"),
    )
    args = ap.parse_args(argv)
    os.environ["DYNAMODB_TABLE_NAME"] = args.table
    # Namespaced by table: cached meta embeds that table's QA sections, so a
    # --table rerun must not silently reuse another environment's extraction.
    cache_dir = os.path.join(
        args.cache_dir or os.path.join(args.out_dir, "cache"), args.table
    )

    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(args.table)
    summary = {}
    for pair in args.merchant or DEFAULT_MERCHANTS:
        name, _, slug = pair.partition(":")
        slug = slug or name.lower().replace(" ", "")
        print(f"\n===== {name} ({slug}) =====")
        atlas = compile_atlas(slug, args.out_dir)
        places, _ = client.get_receipt_places_by_merchant(
            merchant_name=name, limit=60
        )
        receipts = []
        for p in places:
            if len(receipts) >= args.receipts:
                break
            try:
                meta, masks, line_idx, chars, slants = load_or_extract(
                    client, name, slug, str(p.image_id), int(p.receipt_id),
                    cache_dir,
                )
            except Exception as e:  # noqa: BLE001
                print(f"  [skip] {p.image_id[:8]}#{p.receipt_id}: {e}")
                continue
            if meta["ocr_overlap_score"] > args.max_overlaps:
                print(
                    f"  [vet] {p.image_id[:8]}#{p.receipt_id}: "
                    f"overlap={meta['ocr_overlap_score']} > {args.max_overlaps}"
                )
                continue
            receipts.append(
                {
                    "meta": meta,
                    "masks": masks,
                    "line_idx": line_idx,
                    "chars": chars,
                    "slants": slants,
                }
            )
            print(
                f"  [ok] {p.image_id[:8]}#{p.receipt_id}: "
                f"{len(meta['lines'])} lines, {len(chars)} letters, "
                f"{len(meta['sections'])} VALID sections"
            )
        if not receipts:
            print("  no vetted receipts; skipping")
            continue

        per_receipt, discovered, exemplars, all_lines = analyze_merchant(
            receipts, atlas, args
        )

        # outputs
        for pr in per_receipt:
            write_run_map(args.out_dir, slug, pr)
        viz_paths = [
            render_overlay(args.out_dir, slug, pr, client, name)
            for pr in per_receipt[: args.viz]
        ]
        ex_path, _, _ = render_exemplars(args.out_dir, slug, exemplars, atlas)

        # aggregates
        scores = [
            l["attribution"]
            for _, l, _ in all_lines
            if l["attribution"] is not None
        ]
        slants = [
            l["slant_deg"] for _, l, _ in all_lines if l["slant_deg"] is not None
        ]
        italic_lines = [
            (r["meta"]["image_id"][:8], l["text"], l["slant_deg"])
            for r, l, _ in all_lines
            if l["slant_deg"] is not None
            and abs(l["slant_deg"]) >= ITALIC_DEG
            and not l["contaminated"]  # double-struck crops fake a lean
        ]
        xt_all = [x for pr in per_receipt for x in pr["crosstab"]]
        xt_measured = [x for x in xt_all if x["n_measured"] > 0]
        tf_counts = defaultdict(int)
        tier_counts = defaultdict(int)
        for _, l, _ in all_lines:
            if l.get("typeface"):
                tf_counts[l["typeface"]] += 1
            tier_counts[l.get("tier", "normal")] += 1
        summary[slug] = {
            "merchant": name,
            "n_receipts": len(receipts),
            "n_lines_measured": len(scores),
            "attribution_quartiles": [
                round(float(np.percentile(scores, q)), 3)
                for q in (5, 25, 50, 75, 95)
            ]
            if scores
            else None,
            "typeface_line_counts": dict(sorted(tf_counts.items())),
            "tier_line_counts": dict(sorted(tier_counts.items())),
            "discovered_typefaces": discovered,
            "slant_deg_quartiles": [
                round(float(np.percentile(slants, q)), 1)
                for q in (5, 25, 50, 75, 95)
            ]
            if slants
            else None,
            "italic_candidate_lines": italic_lines,
            "sections_total": len(xt_all),
            "sections_measured": len(xt_measured),
            "sections_multi_run": sum(1 for x in xt_measured if x["multi_run"]),
            "sections_multi_style": sum(
                1 for x in xt_measured if x["multi_style"]
            ),
            "sections_multi_typeface": sum(
                1 for x in xt_measured if x["multi_typeface"]
            ),
            "multi_style_by_type": {
                t: [
                    sum(
                        1
                        for x in xt_measured
                        if x["section_type"] == t and x["multi_style"]
                    ),
                    sum(1 for x in xt_measured if x["section_type"] == t),
                ]
                for t in sorted({x["section_type"] for x in xt_measured})
            },
            "viz": [p for p in viz_paths if p],
            "exemplar_sheet": ex_path,
        }
        s = summary[slug]
        print(
            f"  lines={s['n_lines_measured']} attr_q={s['attribution_quartiles']}"
            f"\n  typefaces={s['typeface_line_counts']} discovered k={len(discovered)}"
            f"\n  sections measured={s['sections_measured']} "
            f"multi-run={s['sections_multi_run']} "
            f"multi-style={s['sections_multi_style']} "
            f"multi-typeface={s['sections_multi_typeface']}"
            f"\n  italic candidates={len(italic_lines)}"
        )

    out = os.path.join(args.out_dir, "summary.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    print(f"\nsummary -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

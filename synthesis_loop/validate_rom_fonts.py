#!/usr/bin/env python3
"""validate_rom_fonts.py -- match recreated ROM-font atlases against a
merchant's REAL receipt letter crops, and compare to the merchant's currently
shipped atlas on the same vetted crops.

Machinery is the typography pilot's (PR #1106): per-letter crops are cleaned
(``clean_letter_mask``), shape-normalized (``normalize_glyph``, the M2
aspect-preserving 32x32 space -- which cancels cap-height / scanner-resolution
differences), and scored by calibrated shifted IoU (``shifted_iou``, +-2 px).
A font's score against a merchant is the MEDIAN per-letter shifted IoU over all
that merchant's vetted crops (``ocr_overlap_score`` <= 2, the M3 vetting rule).

Extraction is cached per receipt so network blips resume cheaply. Pure
measurement: no Dynamo writes, no renders, no publishes.

Usage:
  python validate_rom_fonts.py \
      --rom-dir ../.out/rom-fonts \
      --merchant "Vons:vons" --merchant "CVS:cvs" \
      --merchant "Smith's:" --merchant "The Home Depot:homedepot" \
      --merchant "Target:target" --merchant "Whole Foods Market:" \
      --receipts 8 --out ../.out/rom-fonts/validation.json
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
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (
    _HERE,
    os.path.join(_ROOT, "receipt_dynamo"),
    os.path.join(_ROOT, "tools", "glyph-studio", "py"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from glyphstudio.family_cluster import normalize_glyph  # noqa: E402
from glyphstudio.typography import (  # noqa: E402
    attribution_ious,
    clean_letter_mask,
    shifted_iou,
)

CACHE_SCHEMA = 1
DEFAULT_ROMS = [
    "bitMatrix-C1",
    "bitMatrix-C1-heavy",
    "bitMatrix-D1",
    "pixCrog",
    "bitMatrix-B2",
    "bitArray-A2",
]


def ocr_overlap_score(words) -> int:
    """Same-row word pairs whose x-intervals overlap >30% (M3 vetting)."""
    rows: dict[float, list[tuple[float, float]]] = {}
    for w in words:
        bb = w.get("bbox") or ()
        if len(bb) != 4:
            continue
        y_center = round((bb[1] + bb[3]) / 2000.0, 2)
        x0, x1 = sorted((bb[0], bb[2]))
        rows.setdefault(y_center, []).append((x0, x1))
    bad = 0
    for spans in rows.values():
        spans.sort()
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            inter = min(a1, b1) - max(a0, b0)
            if inter > 0.3 * min(a1 - a0, b1 - b0):
                bad += 1
    return bad


# --- per-receipt crop extraction (cached) ------------------------------------


def _extract_receipt(client, merchant, image_id, receipt_id):
    """Real receipt -> (chars, normalized bool masks, cap-height px, overlap)."""
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

    def box_px(obj):
        tl, br = obj.top_left, obj.bottom_right
        return (
            min(tl["x"], br["x"]) * W,
            (1 - max(tl["y"], br["y"])) * H,
            max(tl["x"], br["x"]) * W,
            (1 - min(tl["y"], br["y"])) * H,
        )

    chars, masks, caps = [], [], []
    for l in letters:
        ch = str(l.text or "")[:1]
        if not ch.strip() or not (33 <= ord(ch) <= 126):
            continue
        x0, y0, x1, y1 = box_px(l)
        xi0, yi0 = max(0, int(x0)), max(0, int(y0))
        xi1, yi1 = min(W, int(x1) + 1), min(H, int(y1) + 1)
        if xi1 - xi0 < 3 or yi1 - yi0 < 3:
            continue
        crop, _ = auto_polarity(gray[yi0:yi1, xi0:xi1])
        mask = clean_letter_mask(sauvola_mask(crop))
        if mask.sum() < 8:
            continue
        ys, _xs = np.where(mask)
        ink_h = float(ys.max() - ys.min() + 1)
        chars.append(ch)
        masks.append(normalize_glyph(mask))
        if ch.isupper() or ch.isdigit():
            caps.append(ink_h)
    return chars, masks, caps, overlaps


def load_or_extract(client, merchant, slug_key, image_id, receipt_id, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{slug_key}_{image_id}_{receipt_id}.npz")
    if os.path.exists(path):
        try:
            d = np.load(path, allow_pickle=False)
            if int(d["schema"]) == CACHE_SCHEMA:
                return (
                    [str(c) for c in d["chars"]],
                    list(d["masks"].astype(bool)),
                    d["caps"].tolist(),
                    int(d["overlap"]),
                )
        except Exception:  # noqa: BLE001 - corrupt/old cache: re-extract
            pass
        os.remove(path)
    chars, masks, caps, overlap = _extract_receipt(
        client, merchant, image_id, receipt_id
    )
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        np.savez_compressed(
            fh,
            schema=np.int32(CACHE_SCHEMA),
            chars=np.array(chars, dtype="U1"),
            masks=(
                np.stack(masks).astype(bool)
                if masks
                else np.zeros((0, 32, 32), bool)
            ),
            caps=np.array(caps, dtype=np.float32),
            overlap=np.int32(overlap),
        )
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return chars, masks, caps, overlap


# --- atlases -----------------------------------------------------------------


def load_rom_atlas(npz_path):
    data = np.load(npz_path)
    return {
        chr(int(k[1:])): normalize_glyph(data[k])
        for k in data.files
        if k.startswith("c")
    }


def compile_shipped_atlas(slug, out_dir):
    """Compile fonts/<slug> to a temp npz and normalize (shipped baseline)."""
    from glyphstudio.compile import main as compile_main

    npz = os.path.join(out_dir, f"_shipped_{slug}.glyphs.npz")
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


# --- scoring -----------------------------------------------------------------


def score_atlas(letters, atlas):
    """Corpus (char, shifted-IoU) pairs + median/mean/n vs one atlas."""
    ious = attribution_ious(letters, atlas)  # skips chars atlas lacks
    vals = [v for _, v in ious]
    per_char = defaultdict(list)
    for ch, v in ious:
        per_char[ch].append(v)
    return {
        "median_iou": round(float(median(vals)), 4) if vals else None,
        "mean_iou": round(float(np.mean(vals)), 4) if vals else None,
        "n_letters": len(vals),
        "per_char_median": {
            ch: round(float(median(vs)), 3) for ch, vs in sorted(per_char.items())
        },
    }, ious


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom-dir", default=os.path.join(_ROOT, ".out", "rom-fonts"))
    ap.add_argument("--rom", action="append", default=None)
    ap.add_argument("--merchant", action="append", required=True)
    ap.add_argument("--receipts", type=int, default=8)
    ap.add_argument("--max-overlaps", type=int, default=2)
    ap.add_argument(
        "--out", default=os.path.join(_ROOT, ".out", "rom-fonts", "validation.json")
    )
    ap.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"),
    )
    args = ap.parse_args(argv)
    os.environ["DYNAMODB_TABLE_NAME"] = args.table
    cache_dir = os.path.join(args.rom_dir, "cache", args.table)

    rom_names = args.rom or DEFAULT_ROMS
    roms = {
        r: load_rom_atlas(os.path.join(args.rom_dir, f"{r}.glyphs.npz"))
        for r in rom_names
    }
    for r, a in roms.items():
        print(f"ROM {r}: {len(a)} glyphs")

    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(args.table)
    report = {"table": args.table, "roms": rom_names, "merchants": {}}

    for pair in args.merchant:
        name, _, slug = pair.partition(":")
        slug = slug.strip() or None
        slug_key = slug or name.lower().replace(" ", "").replace("'", "")
        print(f"\n===== {name} (shipped slug={slug}) =====")
        places, _ = client.get_receipt_places_by_merchant(
            merchant_name=name, limit=80
        )
        letters, caps_all, used = [], [], []
        for p in places:
            if len(used) >= args.receipts:
                break
            try:
                chars, masks, caps, overlap = load_or_extract(
                    client, name, slug_key, str(p.image_id), int(p.receipt_id),
                    cache_dir,
                )
            except Exception as e:  # noqa: BLE001
                print(f"  [skip] {str(p.image_id)[:8]}#{p.receipt_id}: {e}")
                continue
            if overlap > args.max_overlaps:
                print(
                    f"  [vet] {str(p.image_id)[:8]}#{p.receipt_id}: "
                    f"overlap={overlap} > {args.max_overlaps}"
                )
                continue
            if not masks:
                continue
            letters.extend(zip(chars, masks))
            caps_all.extend(caps)
            used.append(f"{p.image_id}#{p.receipt_id}")
            print(
                f"  [ok] {str(p.image_id)[:8]}#{p.receipt_id}: "
                f"{len(masks)} letters, cap~{np.median(caps):.1f}px"
                if caps else f"  [ok] {str(p.image_id)[:8]}#{p.receipt_id}"
            )
        if not letters:
            print("  no vetted crops; skipping")
            report["merchants"][name] = {"error": "no vetted crops"}
            continue

        cap_med = float(np.median(caps_all)) if caps_all else None
        entry = {
            "shipped_slug": slug,
            "n_receipts": len(used),
            "n_letters": len(letters),
            "median_cap_px": round(cap_med, 1) if cap_med else None,
            "receipts": used,
            "scores": {},
        }
        # shipped baseline
        if slug:
            try:
                shipped = compile_shipped_atlas(slug, args.rom_dir)
                s, _ = score_atlas(letters, shipped)
                entry["scores"]["CURRENT:" + slug] = s
                print(
                    f"  CURRENT ({slug}): median_iou={s['median_iou']} "
                    f"n={s['n_letters']}"
                )
            except Exception as e:  # noqa: BLE001
                print(f"  CURRENT ({slug}) failed: {e}")
                entry["scores"]["CURRENT:" + slug] = {"error": str(e)}
        # ROM candidates
        for r, atlas in roms.items():
            s, _ = score_atlas(letters, atlas)
            entry["scores"]["ROM:" + r] = s
            print(f"  ROM {r:20s}: median_iou={s['median_iou']} n={s['n_letters']}")

        rom_scores = {
            r: entry["scores"]["ROM:" + r]["median_iou"]
            for r in roms
            if entry["scores"]["ROM:" + r]["median_iou"] is not None
        }
        winner = max(rom_scores, key=rom_scores.get) if rom_scores else None
        entry["rom_winner"] = winner
        entry["rom_winner_iou"] = rom_scores.get(winner) if winner else None
        cur = (
            entry["scores"].get("CURRENT:" + slug, {}).get("median_iou")
            if slug
            else None
        )
        entry["current_iou"] = cur
        entry["rom_beats_current"] = (
            (entry["rom_winner_iou"] is not None and cur is not None
             and entry["rom_winner_iou"] > cur)
        )
        print(
            f"  => winner ROM={winner} ({entry['rom_winner_iou']}) "
            f"vs current={cur} "
            f"beats={entry['rom_beats_current']}"
        )
        report["merchants"][name] = entry

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nreport -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

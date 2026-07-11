#!/usr/bin/env python3
"""GlyphScore CLI: build crop references, score atlases and renders.

Subcommands:

  build-ref   --merchant "Costco Wholesale" --slug costco --receipts 12 \
              --out costco.refpack.npz
      Vetted (ocr_overlap_score <= 2) dev receipts -> cleaned, normalized
      per-char letter-crop stacks. Cached per receipt (resumable).

  score-atlas --atlas vons.glyphs.npz --ref costco.refpack.npz \
              [--anchor bitMatrix-C2.glyphs.npz] --out report.json
      Score a glyph atlas against the reference crops ('self' mode), and,
      with --anchor, against a designed font calibrated by the crops.

  score-render --render final.png --words final.labels.json \
              --ref costco.refpack.npz [--anchor chart.npz] --out report.json
      Segment a synthetic render into per-character masks (word boxes ->
      connected components) and score every instance; per-line roll-up.

  printer-distortion --ref costco.refpack.npz --anchor chart.npz
      Report how far REAL printing lands from the designed letterforms
      (the anchor-mode calibration distribution itself).

Scores are percentiles of real-print variation (see glyphstudio.glyph_score);
GlyphScore roll-ups are usage-frequency weighted, 0..100, higher = closer to
the merchant's real printing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _p in (
    _HERE,
    os.path.join(_ROOT, "receipt_dynamo"),
    os.path.join(_ROOT, "synthesis_loop"),
    os.path.join(_ROOT, "receipt_upload"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from glyphstudio.family_cluster import normalize_glyph  # noqa: E402
from glyphstudio.glyph_score import (  # noqa: E402
    MIN_REF,
    build_anchor_references,
    build_self_references,
    load_refpack,
    per_char_table,
    save_refpack,
    score_instances,
    segment_word_mask,
    group_rollup,
    weighted_glyphscore,
)
from glyphstudio.typography import clean_letter_mask  # noqa: E402

MIN_INK_REAL = 8  # the typography-runs floor for real letter crops
CACHE_SCHEMA = 1


# --- extraction (network + pixels; per-receipt cache) --------------------------


def _extract_receipt_crops(merchant: str, image_id: str, receipt_id: int):
    """One vetted receipt -> (meta, chars, normalized cleaned letter masks)."""
    from glyph_segment import auto_polarity, sauvola_mask
    from m3_acceptance import ocr_overlap_score
    from receipt_line_scorecard import _load_words_and_real

    from receipt_dynamo.data.dynamo_client import DynamoClient

    real, words = _load_words_and_real(merchant, image_id, receipt_id)
    overlaps = ocr_overlap_score(words)
    gray = np.asarray(real.convert("L"))
    H, W = gray.shape
    table = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    client = DynamoClient(table)
    details = client.get_image_details(image_id)
    letters = [
        l
        for l in details.receipt_letters
        if str(l.receipt_id) == str(receipt_id)
    ]
    chars: list[str] = []
    masks: list[np.ndarray] = []
    for l in letters:
        ch = str(l.text or "")[:1]
        if not ch.strip():
            continue
        tl, br = l.top_left, l.bottom_right
        x0 = min(tl["x"], br["x"]) * W
        x1 = max(tl["x"], br["x"]) * W
        y0 = (1 - max(tl["y"], br["y"])) * H
        y1 = (1 - min(tl["y"], br["y"])) * H
        xi0, yi0 = max(0, int(x0)), max(0, int(y0))
        xi1, yi1 = min(W, int(x1) + 1), min(H, int(y1) + 1)
        if xi1 - xi0 < 3 or yi1 - yi0 < 3:
            continue
        crop, _ = auto_polarity(gray[yi0:yi1, xi0:xi1])
        mask = clean_letter_mask(sauvola_mask(crop))
        if mask.sum() < MIN_INK_REAL:
            continue
        chars.append(ch)
        masks.append(normalize_glyph(mask))
    meta = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant": merchant,
        "ocr_overlap_score": overlaps,
        "n_letters": len(chars),
        "cache_schema": CACHE_SCHEMA,
    }
    return meta, chars, masks


def _load_or_extract(merchant, slug, image_id, receipt_id, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{slug}_{image_id}_{receipt_id}.npz")
    if os.path.exists(path):
        try:
            d = np.load(path, allow_pickle=False)
            meta = json.loads(str(d["meta"]))
            if meta.get("cache_schema") == CACHE_SCHEMA:
                return meta, [str(c) for c in d["chars"]], d["masks"].astype(bool)
        except Exception as e:  # noqa: BLE001 - corrupt cache = miss
            print(f"  [cache] {os.path.basename(path)} unreadable ({e})",
                  file=sys.stderr)
        os.remove(path)
    meta, chars, masks = _extract_receipt_crops(merchant, image_id, receipt_id)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:  # atomic: a killed run can't half-write
        np.savez_compressed(
            fh,
            meta=json.dumps(meta),
            chars=np.array(chars, dtype="U1"),
            masks=(
                np.stack(masks).astype(bool)
                if masks
                else np.zeros((0, 32, 32), bool)
            ),
        )
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return meta, chars, masks


def cmd_build_ref(args) -> int:
    os.environ["DYNAMODB_TABLE_NAME"] = args.table
    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(args.table)
    cache_dir = os.path.join(args.cache_dir, args.table)
    places, _ = client.get_receipt_places_by_merchant(
        merchant_name=args.merchant, limit=max(60, args.receipts * 4)
    )
    pack: dict[str, list[np.ndarray]] = defaultdict(list)
    used = 0
    skipped = 0
    for p in places:
        if used >= args.receipts:
            break
        try:
            meta, chars, masks = _load_or_extract(
                args.merchant, args.slug, str(p.image_id), int(p.receipt_id),
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
        if skipped < args.skip:
            skipped += 1
            continue
        for ch, m in zip(chars, masks):
            pack[ch].append(m)
        used += 1
        print(
            f"  [ok] {p.image_id[:8]}#{p.receipt_id}: {len(chars)} letters"
        )
    if not used:
        print("no vetted receipts", file=sys.stderr)
        return 1
    stacks = {ch: np.stack(ms) for ch, ms in pack.items()}
    save_refpack(args.out, stacks)
    scorable = sorted(ch for ch, s in stacks.items() if s.shape[0] >= MIN_REF)
    print(
        f"{args.out}: {used} receipts, "
        f"{sum(s.shape[0] for s in stacks.values())} crops, "
        f"{len(stacks)} chars ({len(scorable)} scorable >= {MIN_REF}): "
        f"{''.join(scorable)}"
    )
    return 0


# --- shared scoring plumbing -----------------------------------------------------


def _load_atlas(path: str) -> dict[str, np.ndarray]:
    data = np.load(path)
    return {
        chr(int(k[1:])): data[k]
        for k in data.files
        if k.startswith("c")
    }


def _build_refs(args, cohort: str = "single"):
    """Cohort must match the candidates: "exemplar" for atlas glyphs
    (median-voted, clean), "single" for render cells / letter crops."""
    pack = load_refpack(args.ref)
    if getattr(args, "anchor", None):
        anchors = _load_atlas(args.anchor)
        refs = build_anchor_references(
            pack, anchors, max_shift=args.max_shift, cohort=cohort
        )
        mode = f"anchor/{cohort}"
    else:
        refs = build_self_references(
            pack, max_shift=args.max_shift, cohort=cohort
        )
        mode = f"self/{cohort}"
    if not refs:
        raise SystemExit("no scorable chars (need >= "
                         f"{MIN_REF} crops per char)")
    return refs, mode


def _report(results, refs, mode, extra=None) -> dict:
    doc = {
        "mode": mode,
        "glyphscore": weighted_glyphscore(results, refs if mode_is_per_char(results) else None),
        "n_instances": len(results),
        "n_chars": len({r.char for r in results}),
        "per_char": per_char_table(results),
    }
    doc.update(extra or {})
    return doc


def mode_is_per_char(results) -> bool:
    """Atlas scoring yields exactly one result per char -> weight by usage."""
    chars = [r.char for r in results]
    return len(chars) == len(set(chars))


def _print_summary(doc: dict) -> None:
    print(
        f"GlyphScore = {doc['glyphscore']:.1f}  "
        f"(mode={doc['mode']}, {doc['n_instances']} instances, "
        f"{doc['n_chars']} chars)"
    )
    worst = sorted(
        doc["per_char"].items(), key=lambda kv: kv[1]["pct_med"]
    )[:8]
    best = sorted(
        doc["per_char"].items(), key=lambda kv: -kv[1]["pct_med"]
    )[:8]
    fmt = lambda kv: f"{kv[0]!r}:{kv[1]['pct_med']:.2f}(n={kv[1]['n']})"  # noqa: E731
    print("  worst chars:", " ".join(fmt(kv) for kv in worst))
    print("  best chars: ", " ".join(fmt(kv) for kv in best))


def _write(doc: dict, out: str | None) -> None:
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"-> {out}")


def cmd_score_atlas(args) -> int:
    refs, mode = _build_refs(args, cohort="exemplar")
    atlas = _load_atlas(args.atlas)
    instances = [
        (ch, normalize_glyph(g)) for ch, g in sorted(atlas.items())
    ]
    results = score_instances(refs, instances, max_shift=args.max_shift)
    doc = _report(results, refs, mode, {"atlas": args.atlas, "ref": args.ref})
    _print_summary(doc)
    _write(doc, args.out)
    return 0


def _words_from_json(path: str) -> list[dict]:
    """Accept either a labels doc ({tokens, bboxes}) or a word-dict list."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "tokens" in data:
        return [
            {"text": t, "bbox": b}
            for t, b in zip(data["tokens"], data["bboxes"])
        ]
    if isinstance(data, list):
        return [{"text": w["text"], "bbox": w["bbox"]} for w in data]
    raise SystemExit(f"unrecognized words JSON shape in {path}")


def segment_render(render_path: str, words: list[dict]):
    """Render PNG + word boxes -> (char, normalized mask, line key) instances.

    Word boxes are the labels convention: 0..1000, y-up. Binarization is
    Sauvola with auto polarity (reverse-video safe), the same treatment the
    real-crop extractor applies, so both sides of the comparison walk through
    the same pipeline.
    """
    from PIL import Image

    from glyph_segment import auto_polarity, sauvola_mask
    from glyphstudio.stylescan import group_visual_lines

    gray = np.asarray(Image.open(render_path).convert("L"))
    H, W = gray.shape
    ws = []
    for w in words:
        x0, y0, x1, y1 = (float(v) for v in w["bbox"][:4])
        left = min(x0, x1) / 1000 * W
        right = max(x0, x1) / 1000 * W
        top = (1 - max(y0, y1) / 1000) * H
        bottom = (1 - min(y0, y1) / 1000) * H
        ws.append(
            {
                "text": str(w.get("text") or ""),
                "l": left,
                "r": right,
                "t": top,
                "b": bottom,
                "cy": (top + bottom) / 2,
                "h": bottom - top,
            }
        )
    vlines = group_visual_lines(ws)

    # Pass A — locate each word's candidate ink run. The renderer draws
    # words on ITS grid, not the OCR box: ink routinely starts/ends a few px
    # outside the box (a tight crop clips leading glyphs) and neighbors sit
    # one space cell away. Pad by a pitch, despeckle texture noise, then
    # cluster ink columns into runs separated by >= 0.7 pitch and take the
    # run overlapping the box.
    located = []  # (line_idx, word, mask, run, n_chars, pitch_est)
    n_words = 0
    for li, line in enumerate(vlines):
        for w in line:
            text = w["text"].strip()
            n_chars = len([c for c in text if not c.isspace()])
            if not n_chars:
                continue
            pitch_est = max(2.0, (w["r"] - w["l"]) / n_chars)
            pad = max(3, int(round(pitch_est)))
            vpad = max(3, int(round(0.5 * (w["b"] - w["t"]))))
            xi0, yi0 = max(0, int(w["l"]) - pad), max(0, int(w["t"]) - vpad)
            xi1, yi1 = min(W, int(w["r"]) + pad), min(H, int(w["b"]) + vpad)
            if xi1 - xi0 < 3 or yi1 - yi0 < 3:
                continue
            n_words += 1
            crop, _ = auto_polarity(gray[yi0:yi1, xi0:xi1])
            mask = _despeckle(sauvola_mask(crop))
            # vertical first: the render's baseline sits a few px off the
            # OCR box, so a box-tight crop clips feet and catches the
            # neighboring line's bottoms. Locate the row band, then cut.
            band = _locate_row_band(
                mask, box=(int(w["t"]) - yi0, int(w["b"]) - yi0)
            )
            if band is None:
                continue
            mask = mask[band[0] : band[1]]
            run = _locate_word_run(
                mask, pitch_est, box=(int(w["l"]) - xi0, int(w["r"]) - xi0)
            )
            if run is None:
                continue
            located.append((li, w, mask, run, n_chars, pitch_est, xi0))

    # Per-line pitch: the grid renderer uses ONE pitch per line, so the
    # median over that line's well-behaved words beats any single word's
    # noisy extent (a speckle or a merged neighbor stretches one word's run;
    # it cannot stretch the line's median).
    line_pitch: dict[int, float] = {}
    by_line: dict[int, list[float]] = {}
    for li, w, mask, (a, b), n, pest, xi0 in located:
        if n >= 3 and 0.6 * pest <= (b - a) / n <= 1.4 * pest:
            by_line.setdefault(li, []).append((b - a) / n)
    for li, vals in by_line.items():
        line_pitch[li] = float(np.median(vals))

    # Pass B — cut cells. A run whose width disagrees with n * line_pitch
    # merged a neighbor or a speckle: re-fit an expected-width window inside
    # it (anchored by ink mass near the OCR box) instead of trusting the run.
    instances = []
    n_seg = 0
    for li, w, mask, (a, b), n, pest, xi0 in located:
        p = line_pitch.get(li, pest)
        expect = n * p
        if abs((b - a) - expect) > 0.15 * expect:
            fit = _best_window(
                mask, int(round(expect)),
                box=(int(w["l"]) - xi0, int(w["r"]) - xi0),
            )
            if fit is None:
                continue
            a, b = fit
        if not 0.6 * p <= (b - a) / n <= 1.4 * p:
            continue
        parts = segment_word_mask(mask[:, a:b], w["text"].strip())
        if parts is None:
            continue
        n_seg += 1
        for ch, part in zip(
            [c for c in w["text"].strip() if not c.isspace()], parts
        ):
            instances.append((ch, normalize_glyph(part), f"line{li:03d}"))
    return instances, {"n_words": n_words, "n_words_segmented": n_seg}


def _despeckle(mask: np.ndarray, min_px: int = 5) -> np.ndarray:
    """Drop sub-``min_px`` components: paper-texture speckles that would
    otherwise join a word's ink run and stretch its extent by a cell."""
    from glyphstudio.typography import connected_components

    m = np.asarray(mask, bool)
    if not m.any():
        return m
    keep = np.zeros_like(m)
    for c in connected_components(m):
        if int(c.sum()) >= min_px:
            keep |= c
    return keep


def _locate_row_band(
    mask: np.ndarray, box: tuple[int, int]
) -> tuple[int, int] | None:
    """The row span of the text band inside a vertically padded crop.

    Bands are maximal row groups separated by >= 2 blank rows; the one with
    the largest overlap with the OCR box's rows wins (fragments of the
    neighboring line at the crop's edge lose).
    """
    proj = np.asarray(mask, bool).sum(axis=1)
    rows = np.where(proj > 0)[0]
    if rows.size == 0:
        return None
    bands: list[list[int]] = [[int(rows[0]), int(rows[0])]]
    for rr in rows[1:]:
        if rr - bands[-1][1] - 1 < 2:
            bands[-1][1] = int(rr)
        else:
            bands.append([int(rr), int(rr)])
    b0, b1 = box

    def overlap(r: list[int]) -> int:
        return max(0, min(r[1], b1) - max(r[0], b0) + 1)

    best = max(bands, key=overlap)
    if overlap(best) <= 0:
        return None
    return best[0], best[1] + 1


def _locate_word_run(
    mask: np.ndarray, pitch_est: float, box: tuple[int, int]
) -> tuple[int, int] | None:
    """The column span of the ink run that IS the word inside a padded crop.

    Runs are maximal column groups whose internal gaps are < 0.7 * pitch
    (inter-character); a gap of a space cell or more separates neighbors.
    Returns the run with the largest column overlap with the OCR box span,
    or None when nothing overlaps.
    """
    proj = np.asarray(mask, bool).sum(axis=0)
    cols = np.where(proj > 0)[0]
    if cols.size == 0:
        return None
    gap_thresh = max(2.0, 0.7 * pitch_est)
    runs: list[list[int]] = [[int(cols[0]), int(cols[0])]]
    for c in cols[1:]:
        if c - runs[-1][1] - 1 < gap_thresh:
            runs[-1][1] = int(c)
        else:
            runs.append([int(c), int(c)])
    b0, b1 = box

    def overlap(r: list[int]) -> int:
        return max(0, min(r[1], b1) - max(r[0], b0) + 1)

    best = max(runs, key=overlap)
    if overlap(best) <= 0:
        return None
    return best[0], best[1] + 1


def _best_window(
    mask: np.ndarray, width: int, box: tuple[int, int]
) -> tuple[int, int] | None:
    """Best ``width``-column window for a word whose ink run over-merged.

    Maximizes ink weighted toward the OCR box (columns within the box +-3 px
    count full, columns outside count a quarter — the box anchors WHICH end
    of an over-wide run is the intruder, since the render is only ever
    displaced by a few px, not by a whole character).
    """
    m = np.asarray(mask, bool)
    Wc = m.shape[1]
    if width <= 0 or width > Wc:
        return None
    proj = m.sum(axis=0).astype(float)
    b0, b1 = box
    weight = np.full(Wc, 0.25)
    weight[max(0, b0 - 3) : min(Wc, b1 + 4)] = 1.0
    score = proj * weight
    csum = np.concatenate([[0.0], np.cumsum(score)])
    sums = csum[width:] - csum[:-width]
    s = int(np.argmax(sums))
    if sums[s] <= 0:
        return None
    return s, s + width


def cmd_score_render(args) -> int:
    refs, mode = _build_refs(args)
    words = _words_from_json(args.words)
    instances, seg_stats = segment_render(args.render, words)
    results = score_instances(refs, instances, max_shift=args.max_shift)
    doc = _report(
        results,
        refs,
        mode,
        {
            "render": args.render,
            "ref": args.ref,
            **seg_stats,
            "per_line": group_rollup(results),
        },
    )
    _print_summary(doc)
    print(
        f"  segmentation: {seg_stats['n_words_segmented']}/"
        f"{seg_stats['n_words']} words"
    )
    _write(doc, args.out)
    return 0


def cmd_printer_distortion(args) -> int:
    refs, _mode = _build_refs(args)  # requires --anchor
    rows = {
        ch: {
            "n": int(r.dist.size),
            "iou_med": float(np.median(r.dist)),
            "iou_p10": float(np.percentile(r.dist, 10)),
            "iou_p90": float(np.percentile(r.dist, 90)),
        }
        for ch, r in sorted(refs.items())
    }
    weights = np.array([refs[ch].count for ch in rows], float)
    meds = np.array([rows[ch]["iou_med"] for ch in rows], float)
    overall = float(np.average(meds, weights=weights))
    doc = {
        "ref": args.ref,
        "anchor": args.anchor,
        "overall_iou_weighted": overall,
        "per_char": rows,
    }
    print(
        f"printer distortion: usage-weighted median IoU(real crop, designed "
        f"glyph) = {overall:.3f} over {len(rows)} chars"
    )
    _write(doc, args.out)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build-ref", help="extract a merchant crop refpack")
    b.add_argument("--merchant", required=True)
    b.add_argument("--slug", required=True)
    b.add_argument("--receipts", type=int, default=12)
    b.add_argument(
        "--skip", type=int, default=0,
        help="skip the first N vetted receipts (held-out reference builds)",
    )
    b.add_argument("--max-overlaps", type=int, default=2)
    b.add_argument("--table",
                   default=os.environ.get("DYNAMODB_TABLE_NAME",
                                          "ReceiptsTable-dc5be22"))
    b.add_argument("--cache-dir",
                   default=os.path.join(_ROOT, ".out", "glyphscore", "cache"))
    b.add_argument("--out", required=True)
    b.set_defaults(fn=cmd_build_ref)

    for name, fn, extra in (
        ("score-atlas", cmd_score_atlas, ("--atlas",)),
        ("score-render", cmd_score_render, ("--render", "--words")),
        ("printer-distortion", cmd_printer_distortion, ()),
    ):
        s = sub.add_parser(name)
        for flag in extra:
            s.add_argument(flag, required=True)
        s.add_argument("--ref", required=True)
        s.add_argument(
            "--anchor",
            required=(name == "printer-distortion"),
            help="designed-font .glyphs.npz (anchor mode)",
        )
        s.add_argument("--max-shift", type=int, default=2)
        s.add_argument("--out")
        s.set_defaults(fn=fn)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

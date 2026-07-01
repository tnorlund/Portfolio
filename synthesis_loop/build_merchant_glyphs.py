#!/usr/bin/env python3
"""build_merchant_glyphs.py -- build a real glyph atlas from a merchant's receipts.

The Costco atlas came from a paid-font chart (extract_bitmatrix.py). Most merchants
have no chart, but every merchant HAS hundreds of their own receipts with per-letter
OCR boxes -- so we build the font from those directly: for each printed character,
median-vote its actual thermal letterform across every occurrence, cap-height
normalized and baseline-aligned. Output is the SAME BitmapFont atlas format
extract_bitmatrix produces (c<cp> = binary glyph, o<cp> = glyph-bottom minus the
cap baseline), so it drops straight into a merchant profile's bitmap_font.

Usage: build_merchant_glyphs.py <merchant_name> <out_dir> <atlas_name> [max_receipts]
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyph_segment import auto_polarity, sauvola_mask  # noqa: E402

TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
REGION = os.environ.get("AWS_REGION", "us-east-1")

# Reference chars that sit on the baseline at full cap height (define baseline +
# cap height per printed line). Uppercase-without-descenders + digits.
_CAP_REF = set("ABDEFGHKLMNPRSTUVXZ0123456789")
# Characters we build glyphs for (printable ASCII minus space).
_TARGET = set(chr(c) for c in range(33, 127))

REF_CAP = 40          # stored cap height in px (BitmapFont rescales at render)
CANVAS_H = REF_CAP * 3
CANVAS_W = REF_CAP * 2
CANVAS_BASE = int(REF_CAP * 2.3)   # baseline row inside the accumulation canvas
MIN_SAMPLES = 10      # inlier occurrences required to keep a glyph (else TTF)
MAX_SAMPLES = 140     # cap stored samples per char (memory)
MAX_SHIFT = 3         # clamp phase-correlation to jitter, not gross moves
VOTE = 0.45           # ink if >= this fraction of ALIGNED inliers are ink
SMALL_INK = 130       # glyphs with fewer ink px skip alignment (dots/commas are
                      # position-stable; IoU/phase-corr is unstable for them)


def _ink_bbox(mask):
    ys, xs = np.where(mask)
    if ys.size < 3:
        return None
    return ys.min(), ys.max(), xs.min(), xs.max()


def _phase_shift(ref, img):
    """Integer (dy, dx) aligning img onto ref via FFT phase correlation."""
    R = np.fft.fft2(ref) * np.conj(np.fft.fft2(img))
    R /= np.abs(R) + 1e-9
    c = np.fft.ifft2(R).real
    dy, dx = np.unravel_index(int(np.argmax(c)), c.shape)
    if dy > ref.shape[0] // 2:
        dy -= ref.shape[0]
    if dx > ref.shape[1] // 2:
        dx -= ref.shape[1]
    return dy, dx


def _iou(a, b):
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return inter / union if union else 0.0


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _median3(mask):
    """Light 3x3 median filter: fills pinholes, removes salt/pepper specks."""
    m = mask.astype(np.uint8)
    im = Image.fromarray(m * 255).filter(ImageFilter.MedianFilter(3))
    return np.asarray(im) > 127


def _vote(samples):
    """Phase-align a char's samples to their consensus, reject IoU outliers,
    then majority-vote a clean binary glyph. Returns (glyph_bool, n_inliers).

    Small glyphs (dots, commas) skip alignment/IoU -- they are placed by baseline
    and phase-correlation/IoU is unstable on a handful of pixels."""
    if float(np.median([s.sum() for s in samples])) < SMALL_INK:
        prob = np.mean(samples, axis=0)
        return _median3(prob >= 0.35), len(samples)

    def _shifted(stack, key):
        """Horizontally register each sample by its ink LEFT edge or CENTER."""
        out, pos = [], []
        for s in stack:
            xs = np.where(s.any(axis=0))[0]
            if xs.size == 0:
                pos.append(0)
            else:
                pos.append(int(xs.min()) if key == "left"
                           else int((xs.min() + xs.max()) / 2))
        tgt = int(np.median(pos))
        for s, p in zip(stack, pos):
            out.append(np.roll(s, _clamp(tgt - p, -MAX_SHIFT * 2, MAX_SHIFT * 2), 1))
        return out

    def _one(stack):
        ref = np.mean(stack, axis=0) >= 0.5
        ious = np.array([_iou(a, ref) for a in stack])
        thr = max(0.25, float(np.median(ious)) * 0.5)
        inl = [a for a, i in zip(stack, ious) if i >= thr] or stack
        return np.mean(inl, axis=0), len(inl)

    # Try LEFT-edge and CENTER registration; keep whichever votes SHARPER (fewest
    # ambiguous mid-probability pixels). Left wins on stem letters (E/F/L), center
    # on symmetric ones; neither rescues an inherently jittery diagonal.
    cands = [_one(_shifted(samples, k)) for k in ("left", "center")]
    # Sharpness = agreement mass: prefer the registration whose consensus pixels
    # are decisive (near 0/1) rather than fuzzy. Use mean squared deviation from
    # 0.5 over ink-ish pixels -- higher = crisper.
    def crisp(p):
        m = p[p > 0.15]
        return float(np.mean((m - 0.5) ** 2)) if m.size else 0.0
    prob, n = max(cands, key=lambda c: crisp(c[0]))
    return _median3(prob >= VOTE), len(samples)


def _collect(merchant, max_receipts):
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_upload.font_analysis import load_raw_image_from_s3

    client = DynamoClient(table_name=TABLE, region=REGION)
    places, _ = client.get_receipt_places_by_merchant(merchant)
    targets = [(str(p.image_id), int(p.receipt_id)) for p in places]
    if max_receipts:
        targets = targets[:max_receipts]
    print(f"{merchant}: {len(targets)} receipts", flush=True)

    # per char: keep individual baseline+center placed sample canvases (bool),
    # capped, so we can phase-align + outlier-reject + vote in main().
    samples = defaultdict(list)
    used = 0
    for k, (iid, rid) in enumerate(targets):
        try:
            d = client.get_image_details(iid)
            letters = [l for l in d.receipt_letters if l.receipt_id == rid]
            rec = next((c for c in d.receipts if c.receipt_id == rid), None)
            if rec is None or not letters:
                continue
            arr = np.asarray(load_raw_image_from_s3(rec).convert("L"))
        except Exception:  # noqa: BLE001
            continue
        H, W = arr.shape
        # group letters into printed lines
        lines = defaultdict(list)
        for lt in letters:
            lines[lt.line_id].append(lt)
        for _, lts in lines.items():
            glyphs = []          # (ch, mask, ink_bottom_abs, ink_h)
            for lt in lts:
                ch = (lt.text or "").strip()
                if ch not in _TARGET:
                    continue
                tl, br = lt.top_left, lt.bottom_right
                x0, x1 = tl["x"] * W, br["x"] * W
                y0, y1 = (1 - tl["y"]) * H, (1 - br["y"]) * H
                l, t = int(min(x0, x1)), int(min(y0, y1))
                r, b = int(max(x0, x1)), int(max(y0, y1))
                if r - l < 2 or b - t < 6:
                    continue
                crop, _ = auto_polarity(arr[t:b, l:r])
                mask = sauvola_mask(crop).astype(np.float32)
                bb = _ink_bbox(mask > 0.5)
                if bb is None:
                    continue
                iy0, iy1, ix0, ix1 = bb
                gm = (mask[iy0:iy1 + 1, ix0:ix1 + 1] > 0.5).astype(np.float32)
                glyphs.append((ch, gm, t + iy1, iy1 - iy0 + 1))
            # baseline + cap height from the reference glyphs on THIS line
            refs = [(bot, h) for ch, _, bot, h in glyphs if ch in _CAP_REF]
            if len(refs) < 2:
                continue
            baseline = float(np.median([b for b, _ in refs]))
            cap_h = float(np.median([h for _, h in refs]))
            if cap_h < 5:
                continue
            scale = REF_CAP / cap_h
            for ch, gm, bot, _h in glyphs:
                gh, gw = gm.shape
                nw, nh = max(1, int(round(gw * scale))), max(1, int(round(gh * scale)))
                g = np.asarray(Image.fromarray((gm * 255).astype(np.uint8)).resize(
                    (nw, nh), Image.NEAREST)) > 127
                off = int(round((bot - baseline) * scale))       # bottom vs baseline
                row_bot = CANVAS_BASE + off
                row_top = row_bot - nh
                col0 = CANVAS_W // 2 - nw // 2
                if row_top < 0 or row_bot > CANVAS_H or col0 < 0 or col0 + nw > CANVAS_W:
                    continue
                if len(samples[ch]) >= MAX_SAMPLES:
                    continue
                canvas = np.zeros((CANVAS_H, CANVAS_W), bool)
                canvas[row_top:row_bot, col0:col0 + nw] = g
                samples[ch].append(canvas)
        used += 1
        if (k + 1) % 20 == 0:
            print(f"  {k+1}/{len(targets)} ({used} used)", flush=True)
    return samples


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    merchant, out_dir, name = sys.argv[1], sys.argv[2], sys.argv[3]
    max_receipts = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    os.makedirs(out_dir, exist_ok=True)
    # Cache the (slow, S3-bound) collected samples so vote-parameter tuning is
    # instant on re-run. Set REBUILD_SAMPLES=1 to force re-collection.
    cache = os.path.join(out_dir, f"{name}.samples.npz")
    if os.path.exists(cache) and not os.environ.get("REBUILD_SAMPLES"):
        z = np.load(cache)
        samples = {chr(int(k)): [z[k][i] for i in range(z[k].shape[0])] for k in z.files}
        print(f"loaded cached samples from {cache}")
    else:
        samples = _collect(merchant, max_receipts)
        np.savez_compressed(cache, **{str(ord(ch)): np.array(s, bool)
                                      for ch, s in samples.items() if s})
        print(f"cached samples -> {cache}")

    glyphs, offsets, dropped = {}, {}, []
    for ch, samps in samples.items():
        if len(samps) < MIN_SAMPLES:
            dropped.append(ch)
            continue
        binm, n = _vote(samps)
        if binm is None or n < MIN_SAMPLES:
            dropped.append(ch)
            continue
        bb = _ink_bbox(binm)
        if bb is None:
            dropped.append(ch)
            continue
        iy0, iy1, ix0, ix1 = bb
        glyphs[ch] = binm[iy0:iy1 + 1, ix0:ix1 + 1].astype(np.uint8)
        offsets[ch] = int(iy1 - CANVAS_BASE)     # glyph bottom minus baseline
    print(f"built {len(glyphs)} glyphs: {''.join(sorted(glyphs))}")
    if dropped:
        print(f"dropped (too few samples -> TTF fallback): {''.join(sorted(dropped))}")

    payload = {f"c{ord(k)}": v for k, v in glyphs.items()}
    payload.update({f"o{ord(k)}": np.int16(v) for k, v in offsets.items()})
    np.savez_compressed(os.path.join(out_dir, f"{name}.glyphs.npz"), **payload)
    print(f"wrote {out_dir}/{name}.glyphs.npz")

    # verification contact sheet
    order = sorted(glyphs, key=lambda c: (c.isalpha(), c))
    cell = REF_CAP * 2
    cols = 16
    rows = (len(order) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), (255, 255, 255))
    dd = ImageDraw.Draw(sheet)
    for i, ch in enumerate(order):
        g = glyphs[ch]
        im = Image.fromarray(((1 - g) * 255).astype(np.uint8), "L").convert("RGB")
        cx, cy = (i % cols) * cell, (i // cols) * cell
        # place on baseline within the cell
        base = cy + int(cell * 0.7)
        top = base + offsets[ch] - g.shape[0]
        sheet.paste(im, (cx + 6, top))
        dd.text((cx + 2, cy + 2), ch, fill=(200, 0, 0))
    sheet.save(os.path.join(out_dir, f"{name}.verify.png"))
    print(f"wrote {out_dir}/{name}.verify.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

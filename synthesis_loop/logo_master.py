#!/usr/bin/env python3
"""logo_master.py -- canonical merchant logo by ALIGN + MEDIAN (not mean).

A logo is a single fixed brand asset; the per-receipt variation is pure capture
noise (skew, scale, fold lines, address bleed, partial crops). Naive averaging
(logo_prototype.py) ghosts because it blends those misalignments. This builds a
clean master instead:

  1. crop each receipt's wordmark, Otsu-binarize, morphological-OPEN to drop thin
     fold-lines / speckle BEFORE measuring the ink bbox (stray ink corrupts it),
  2. tight-crop to the ink bbox + resize to a common canvas (normalize scale +
     translation),
  3. phase-correlate each to a reference and REJECT outliers by IoU (skewed /
     partial / fold-crossed captures),
  4. per-pixel MAJORITY VOTE (median of the aligned binary stack) -> speckle gone,
     no ghost. Also emit the vote-FRACTION map: edges where captures disagree are
     naturally anti-aliased (authentic soft thermal edge) instead of hard binary.

Usage: logo_master.py <Merchant> <out_dir> [max_receipts]
Outputs in <out_dir>:
  logo_master_crisp.png  -- majority-vote binary (black ink on white)
  logo_master_soft.png   -- vote-fraction grayscale (anti-aliased edges)
  logo_compare.png       -- old mean | crisp | soft, stacked, for review
"""

from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image

TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
REGION = os.environ.get("AWS_REGION", "us-east-1")
CW = 690  # canvas width; height derived from the median wordmark aspect
KEEP_IOU = (
    0.55  # reject a capture if its aligned IoU vs reference is below this
)

# merchant -> the wordmark tokens that make up the logo (besides MERCHANT_NAME labels)
_WORDMARK = {
    "Costco Wholesale": ("COSTCO", "WHOLESALE"),
    "Vons": ("VONS",),
    "Smith's": ("SMITH'S", "SMITHS", "FRESH", "FOR", "EVERYONE"),
    "Sprouts Farmers Market": ("SPROUTS", "FARMERS", "MARKET"),
    "Target": ("TARGET",),
    "Amazon Fresh": ("AMAZON", "FRESH"),
    "Gelson's Westlake Village": ("GELSON'S", "GELSONS"),
    "Dollar Tree": ("DOLLAR", "TREE", "TREE."),
    "The Stand - American Classics Redefined": ("STAND", "THE"),
}


def _otsu_ink(a):
    a = a.astype(np.float64)
    hist, _ = np.histogram(a, 256, (0, 255))
    tot = a.size
    sm = (np.arange(256) * hist).sum()
    wB = sB = 0.0
    best_t, best = 128, -1.0
    for t in range(256):
        wB += hist[t]
        if wB == 0:
            continue
        wF = tot - wB
        if wF == 0:
            break
        sB += t * hist[t]
        var = wB * wF * ((sB / wB) - ((sm - sB) / wF)) ** 2
        if var > best:
            best, best_t = var, t
    return a < best_t


def _erode(m):
    out = m.copy()
    out[1:, :] &= m[:-1, :]
    out[:-1, :] &= m[1:, :]
    out[:, 1:] &= m[:, :-1]
    out[:, :-1] &= m[:, 1:]
    return out


def _dilate(m):
    out = m.copy()
    out[1:, :] |= m[:-1, :]
    out[:-1, :] |= m[1:, :]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    return out


def _open(m, it=1):
    """Erode then dilate -> removes thin lines (fold marks) + isolated speckle,
    keeps the thick logo strokes."""
    for _ in range(it):
        m = _erode(m)
    for _ in range(it):
        m = _dilate(m)
    return m


def _tight(m):
    ys, xs = np.where(m)
    if ys.size < 80:
        return None
    return m[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def _top_block(mask):
    """Drop a detached low-ink band below the wordmark (the store address line that
    bleeds into the crop). Keep the contiguous run of substantial-ink rows."""
    rowsum = mask.sum(axis=1).astype(float)
    on = rowsum > 0.015 * mask.shape[1]
    bands, i, n = [], 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            bands.append([i, j, rowsum[i:j].sum()])
            i = j
        else:
            i += 1
    if not bands:
        return mask
    # merge bands separated by a tiny (<5px) whitespace gap
    merged = [bands[0]]
    for b in bands[1:]:
        if b[0] - merged[-1][1] < 5:
            merged[-1][1] = b[1]
            merged[-1][2] += b[2]
        else:
            merged.append(b)
    maxink = max(b[2] for b in merged)
    keep = [
        b for b in merged if b[2] >= 0.30 * maxink
    ]  # address band is sparse -> dropped
    return mask[keep[0][0] : keep[-1][1]]


def _clean_tight(crop_gray):
    """Gray crop -> cleaned, tight-cropped binary (variable size, bool) or None."""
    ink = _otsu_ink(crop_gray)
    ink = _open(ink, 1)  # kill fold-lines/speckle before bbox
    t = _tight(ink)
    if t is None:
        return None
    t = _top_block(t)  # drop the address line below the wordmark
    return _tight(t)


def _resize_bin(t, ch):
    img = Image.fromarray((t.astype(np.uint8) * 255)).resize(
        (CW, ch), Image.LANCZOS
    )
    return (np.asarray(img) > 127).astype(np.float64)


def _phase_shift(ref, img):
    """Integer (dy, dx) that best aligns img onto ref via FFT phase correlation."""
    F = np.fft.fft2(ref)
    G = np.fft.fft2(img)
    R = F * np.conj(G)
    R /= np.abs(R) + 1e-9
    c = np.fft.ifft2(R).real
    dy, dx = np.unravel_index(int(np.argmax(c)), c.shape)
    if dy > ref.shape[0] // 2:
        dy -= ref.shape[0]
    if dx > ref.shape[1] // 2:
        dx -= ref.shape[1]
    return dy, dx


def _iou(a, b):
    inter = np.logical_and(a > 0.5, b > 0.5).sum()
    union = np.logical_or(a > 0.5, b > 0.5).sum()
    return inter / union if union else 0.0


def _logo_crop(arr, words, wordmark):
    H, W = arr.shape
    mn = [
        w
        for w in words
        if "MERCHANT_NAME" in (w.get("labels") or [])
        or str(w.get("text", "")).strip().upper() in wordmark
    ]
    if not mn:
        return None

    # The wordmark may also appear in body/footer text (e.g. "VONS.com", taglines)
    # which would stretch the bbox down the whole receipt. The LOGO is the topmost
    # cluster -> keep only tokens within one line-band of the highest match.
    def _cy(w):
        return (1 - (w["top_left"]["y"] + w["bottom_right"]["y"]) / 2) * H

    top_cy = min(_cy(w) for w in mn)
    mn = [w for w in mn if _cy(w) <= top_cy + 0.06 * H]
    tops, bottoms, lefts, rights = [], [], [], []
    for w in mn:
        tl, br = w["top_left"], w["bottom_right"]
        tops.append((1 - tl["y"]) * H)
        bottoms.append((1 - br["y"]) * H)
        lefts.append(tl["x"] * W)
        rights.append(br["x"] * W)
    y0 = int(max(0, min(tops) - 0.02 * H))
    y1 = int(min(H, max(bottoms) + 0.02 * H))
    x0 = int(max(0, min(lefts) - 0.03 * W))
    x1 = int(min(W, max(rights) + 0.03 * W))
    crop = arr[y0:y1, x0:x1]
    return crop if crop.shape[0] >= 20 and crop.shape[1] >= 60 else None


# --- fast collection: shared clients, thread-pool the S3/Dynamo I/O, disk-cache
# the small grayscale logo crop so re-tuning align/vote never re-hits S3.
CACHE_DIR = os.environ.get("LOGO_CACHE", "/tmp/logo_cache")
CACHE_VER = (
    "v2"  # bump when _logo_crop / cropping / image-source logic changes
)


def _load_receipt_gray(rec, s3):
    """Grayscale receipt image, trying multiple S3 locations. The raw upload key
    often 404s (cleaned up); the CDN copy under assets/ is reliable, so prefer it.
    Coords are normalized, so any variant works. Returns a PIL 'L' image or None.
    """
    from io import BytesIO

    from PIL import Image

    attempts = [
        (rec.cdn_s3_bucket, rec.cdn_s3_key),  # reliable processed copy
        (rec.raw_s3_bucket, rec.raw_s3_key),  # original upload (may 404)
        (
            rec.cdn_s3_bucket,
            getattr(rec, "cdn_medium_s3_key", None),
        ),  # smaller fallback
    ]
    for bucket, key in attempts:
        if not bucket or not key:
            continue
        try:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            return Image.open(BytesIO(body)).convert("L")
        except Exception:  # noqa: BLE001
            continue
    return None


def _cache_path(merchant, iid, rid):
    slug = merchant.replace(" ", "_").replace("'", "").lower()
    d = os.path.join(CACHE_DIR, CACHE_VER, slug)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{iid}_{rid}.npy")


def _collect_crops(merchant, targets, wordmark):
    """Grayscale logo crops for all targets: parallel + cached. Returns [uint8 array]."""
    import concurrent.futures as cf

    import boto3

    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(
        table_name=TABLE, region=REGION
    )  # boto3 client is thread-safe
    s3 = boto3.client("s3")  # ONE shared client, reused

    def _one(iid, rid):
        p = _cache_path(merchant, iid, rid)
        if os.path.exists(p):
            a = np.load(p)
            return None if a.size == 0 else a  # cached (incl. negative)
        crop = None
        try:
            d = client.get_image_details(iid)
            words = [
                {
                    "text": w.text,
                    "labels": [],
                    "top_left": w.top_left,
                    "bottom_right": w.bottom_right,
                }
                for w in d.receipt_words
                if w.receipt_id == rid
            ]
            lab = {
                (l.line_id, l.word_id): l.label
                for l in d.receipt_word_labels
                if l.receipt_id == rid
            }
            for w, ww in zip(
                words, [w for w in d.receipt_words if w.receipt_id == rid]
            ):
                if lab.get((ww.line_id, ww.word_id)):
                    w["labels"] = [lab[(ww.line_id, ww.word_id)]]
            rec = next((c for c in d.receipts if c.receipt_id == rid), None)
            if rec is not None:
                raw = _load_receipt_gray(rec, s3)
                if raw is not None:
                    crop = _logo_crop(np.asarray(raw), words, wordmark)
        except Exception:  # noqa: BLE001
            crop = None
        np.save(p, crop if crop is not None else np.empty(0, np.uint8))
        return crop

    crops, done = [], 0
    workers = int(os.environ.get("LOGO_WORKERS", "16"))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, iid, rid): (iid, rid) for iid, rid in targets}
        for f in cf.as_completed(futs):
            done += 1
            c = f.result()
            if c is not None:
                crops.append(c)
            if done % 25 == 0:
                print(
                    f"  ...{done}/{len(targets)} fetched, {len(crops)} crops",
                    flush=True,
                )
    return crops


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    merchant, out_dir = sys.argv[1], sys.argv[2]
    maxr = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    os.makedirs(out_dir, exist_ok=True)
    wordmark = _WORDMARK.get(merchant, ())

    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(table_name=TABLE, region=REGION)
    places, _ = client.get_receipt_places_by_merchant(merchant)
    targets = [(str(p.image_id), int(p.receipt_id)) for p in places]
    if maxr:
        targets = targets[:maxr]
    print(f"{merchant}: {len(targets)} receipts", flush=True)

    crops = _collect_crops(merchant, targets, wordmark)
    tights = [t for t in (_clean_tight(c) for c in crops) if t is not None]

    if len(tights) < 3:
        print(f"too few logo captures ({len(tights)})")
        return 1
    # Canvas height from the MEDIAN wordmark aspect, so the master keeps the real
    # proportions (the renderer scales it uniformly to fit -> aspect must be true).
    aspects = [t.shape[1] / t.shape[0] for t in tights]
    med_aspect = float(np.median(aspects))
    CH = max(8, int(round(CW / med_aspect)))
    print(
        f"collected {len(tights)} crops; median aspect {med_aspect:.2f} -> canvas {CW}x{CH}",
        flush=True,
    )
    canvases = [_resize_bin(t, CH) for t in tights]

    # reference = the canvas with median ink mass (a representative capture)
    masses = [c.sum() for c in canvases]
    ref = canvases[int(np.argsort(masses)[len(masses) // 2])]

    aligned, kept, rejected = [], 0, 0
    for c in canvases:
        dy, dx = _phase_shift(ref, c)
        a = np.roll(np.roll(c, dy, 0), dx, 1)
        if _iou(ref, a) >= KEEP_IOU:
            aligned.append(a)
            kept += 1
        else:
            rejected += 1
    print(
        f"aligned: kept {kept}, rejected {rejected} (IoU<{KEEP_IOU})",
        flush=True,
    )
    if len(aligned) < 3:
        # Low-count merchant (few receipts / missing S3): no stable vote -> fall
        # back to the single sharpest reference capture (already cleaned).
        print(
            f"too few aligned ({len(aligned)}); single-best fallback",
            flush=True,
        )
        aligned = [ref]

    stack = np.stack(aligned)
    frac = stack.mean(axis=0)  # vote fraction 0..1 (soft, anti-aliased)
    crisp = (frac >= 0.5).astype(
        np.float64
    )  # majority vote (median of binary stack)

    # mean of the SAME aligned stack, for comparison (what the old approach yields)
    mean_img = frac  # mean == fraction here; show old UNALIGNED mean too
    old_mean = np.stack(canvases).mean(axis=0)

    def _save_ink(arr01, path):
        Image.fromarray(((1 - arr01) * 255).astype(np.uint8)).save(path)

    _save_ink(crisp, os.path.join(out_dir, "logo_master_crisp.png"))
    _save_ink(frac, os.path.join(out_dir, "logo_master_soft.png"))

    # comparison sheet: old unaligned mean | aligned crisp | aligned soft
    gap = 10
    panel = lambda a: Image.fromarray(
        ((1 - a) * 255).astype(np.uint8)
    ).convert("RGB")
    imgs = [panel(old_mean), panel(crisp), panel(frac)]
    sheet = Image.new("RGB", (CW, CH * 3 + gap * 2), (245, 245, 245))
    for i, im in enumerate(imgs):
        sheet.paste(im, (0, i * (CH + gap)))
    sheet.save(os.path.join(out_dir, "logo_compare.png"))
    print(
        f"master -> {out_dir}/logo_master_crisp.png (+ _soft, + compare) "
        f"from {kept} aligned captures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

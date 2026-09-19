"""Assemble the SynthesisPipeline figure assets from committed inputs.

The portfolio figure (``portfolio/components/ui/Figures/SynthesisPipeline``)
plays back a static tree under
``portfolio/public/synthetic-receipts/pipeline/<slug>/``. Everything in that
tree derives from artefacts the repo already produces -- the letterform
corpus (``*.samples.npz``), the glyph-studio font sources, the compiled
atlas the renderer loads, the production render's ``box_sink`` boxes, and
the real receipt scan. This module holds the offline, pure-numpy/PIL glue
that turns those inputs into the figure's files; ``export_pipeline_assets``
(one level up) wires it to the renderer and to the dev AWS reads.

Every JSON writer here targets the TypeScript schema in
``pipelineData.ts`` / ``geometry.ts`` / ``AugmentationShowcase/labelGeometry.ts``.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from PIL import Image

from .samples import canvas_geometry

# ---------------------------------------------------------------------------
# Figure constants (mirrors pipelineData.ts / the figure spec).
# ---------------------------------------------------------------------------

#: Individual real prints exported for the hero character (act 2).
CHAR_PRINT_COUNT = 30
#: Nearest-neighbour upscale applied to every corpus-derived PNG.
PRINT_UPSAMPLE = 3
#: Corpus samples with fewer ink pixels than this are OCR specks, not
#: prints of the character; the shipped Costco set skipped exactly those.
MIN_PRINT_INK_PX = 30
#: Cap height (px) the font-grid glyph masks are exported at.
FONT_GRID_CAP_PX = 40
#: Printable ASCII, the 94 codepoints the font act cascades in.
FONT_CODEPOINTS = tuple(range(33, 127))
#: Bold is one parameter: the heavy face compiles at this weight multiple
#: (``synthesis_loop/publish_merchant_font.py`` HEAVY_RATIO).
HEAVY_WEIGHT_RATIO = 1.33
#: Finale receipts are normalised to this width; heights follow the scan.
RECEIPT_WIDTH = 760
#: Act-1 thumbnails are normalised to this height.
THUMB_HEIGHT = 900
#: Act-1 fan size.
REAL_THUMB_COUNT = 3
#: Lossy WebP quality for final/real/thumb images.
WEBP_QUALITY = 90
#: The renderer's inner-box inset, recorded in ``metadata.render.margin``.
RENDER_MARGIN = 10
#: Reveal groups in ``compose_steps.json`` are y-bands of the receipt
#: (fractions of the height from the top): header / items / summary / footer.
COMPOSE_BANDS = (0.20, 0.45, 0.75)
COMPOSE_GROUP_ORDER = ("header", "items", "summary", "footer")

#: A thresholded consensus (fraction of prints inked) is "solid ink" above
#: this; it fixes the cloud's ink centre for skeleton alignment.
CLOUD_SOLID_THRESHOLD = 0.2
#: Pixels at least one print in twenty inked are the cloud's soft halo;
#: below that are stray OCR crops that would only widen the frame.
CLOUD_HALO_THRESHOLD = 0.05
CLOUD_PAD_PX = 3


def tight_crop(mask: np.ndarray) -> np.ndarray | None:
    """Crop a boolean/uint8 mask to its ink bounding box (None if empty)."""
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None
    return mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def upsample_nearest(mask: np.ndarray, factor: int) -> np.ndarray:
    return np.repeat(np.repeat(mask, factor, axis=0), factor, axis=1)


def mask_to_gray(mask: np.ndarray) -> Image.Image:
    """Binary ink mask -> 8-bit 'L' image, black ink on white paper."""
    arr = np.where(mask.astype(bool), 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "L")


def mask_to_rgba(mask: np.ndarray) -> Image.Image:
    """Binary ink mask -> RGBA alpha mask (black, alpha 255 where inked).

    The figure paints these with ``mask-image`` in ``currentColor``, so the
    RGB channels are irrelevant and stay 0.
    """
    ink = mask.astype(bool)
    rgba = np.zeros((*ink.shape, 4), dtype=np.uint8)
    rgba[..., 3] = np.where(ink, 255, 0)
    return Image.fromarray(rgba, "RGBA")


# ---------------------------------------------------------------------------
# Act 2: one character -- real prints and the consensus cloud.
# ---------------------------------------------------------------------------


def char_prints(
    stack: np.ndarray,
    *,
    count: int = CHAR_PRINT_COUNT,
    upsample: int = PRINT_UPSAMPLE,
    min_ink_px: int = MIN_PRINT_INK_PX,
) -> list[np.ndarray]:
    """The first ``count`` usable prints of a corpus stack, tight-cropped and
    nearest-upsampled (bool arrays, True = ink), in corpus order."""
    out: list[np.ndarray] = []
    for sample in stack:
        sample = sample.astype(bool)
        if int(sample.sum()) < min_ink_px:
            continue
        crop = tight_crop(sample)
        if crop is None:
            continue
        out.append(upsample_nearest(crop, upsample))
        if len(out) >= count:
            break
    return out


def char_cloud(
    stack: np.ndarray,
    *,
    upsample: int = PRINT_UPSAMPLE,
    halo_threshold: float = CLOUD_HALO_THRESHOLD,
    solid_threshold: float = CLOUD_SOLID_THRESHOLD,
    pad_px: int = CLOUD_PAD_PX,
) -> tuple[Image.Image, dict[str, float]]:
    """Soft consensus cloud PNG + the ``cloudGeom`` the figure maps into.

    The cloud is the un-thresholded fraction-of-prints-inked map (see
    ``samples.consensus_soft``), cropped to its halo, mapped to grey
    (paper 255 -> full-consensus ink 0) and upsampled with bicubic
    interpolation so it reads as a soft cloud rather than blocks.
    ``cloudGeom`` records where the corpus baseline and cap height land in
    the exported pixels so the skeleton overlay (``geometry.ts`` mapToCloud)
    does not drift.
    """
    stack = stack.astype(bool)
    frac = stack.mean(axis=0)
    ref_cap, baseline_row = canvas_geometry(stack.shape[1])
    halo = frac > halo_threshold
    ys, xs = np.nonzero(halo)
    if ys.size == 0:
        raise ValueError("empty corpus stack: no consensus cloud")
    r0 = max(0, int(ys.min()) - pad_px)
    r1 = min(frac.shape[0] - 1, int(ys.max()) + pad_px)
    c0 = max(0, int(xs.min()) - pad_px)
    c1 = min(frac.shape[1] - 1, int(xs.max()) + pad_px)
    crop = frac[r0 : r1 + 1, c0 : c1 + 1]
    gray = np.clip(255.0 - 255.0 * crop, 0, 255).astype(np.uint8)
    image = Image.fromarray(gray, "L").resize(
        (gray.shape[1] * upsample, gray.shape[0] * upsample),
        Image.BICUBIC,
    )
    solid = frac >= solid_threshold
    sx = np.nonzero(solid.any(axis=0))[0]
    if sx.size == 0:
        sx = xs
    ink_center_col = (float(sx.min()) + float(sx.max()) + 1.0) / 2.0 - c0
    geom = {
        "imageW": image.width,
        "imageH": image.height,
        "baselineFromBottomPx": float((r1 + 1 - baseline_row) * upsample),
        "capHeightPx": int(ref_cap * upsample),
        "inkCenterXPx": float(ink_center_col * upsample),
    }
    return image, geom


def dot_params(
    font: dict[str, Any],
    *,
    hero: str,
    samples: int,
    cloud_geom: dict[str, float] | None,
    heavy_ratio: float = HEAVY_WEIGHT_RATIO,
) -> dict[str, Any]:
    """``dot_params.json`` from a glyph-studio ``font.json``."""
    params = font.get("params") or {}
    weight = float(params.get("weight", 1.0))
    out: dict[str, Any] = {
        "dotSize": float((params.get("dot") or {}).get("size", 110)),
        "refCap": int(font.get("refCap", 60)),
        "weightDefault": weight,
        "weightBold": round(weight * heavy_ratio, 4),
        "hero": hero,
        "samples": int(samples),
    }
    if cloud_geom is not None:
        out["cloudGeom"] = dict(cloud_geom)
    return out


# ---------------------------------------------------------------------------
# Act 3: the whole font -- one mask per glyph plus metrics.
# ---------------------------------------------------------------------------

#: ``BitmapFont.glyph(ch, cap_px)`` -> (PIL 'L' mask, height_px, baseline
#: offset_px). Passing the renderer's own font object guarantees the grid
#: shows exactly what the renderer prints.
GlyphFn = Callable[[str, int], tuple[Image.Image, int, int] | None]


def font_grid(
    glyph_fn: GlyphFn,
    *,
    cap_px: int = FONT_GRID_CAP_PX,
    codepoints: Iterable[int] = FONT_CODEPOINTS,
) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    """Tight glyph masks at ``cap_px`` plus ``font_metrics.json``.

    Metrics per glyph: the tight width/height and ``offset`` = ink-bottom
    row relative to the shared baseline (0 = sits on it, negative = above,
    positive = descends below), so the figure can place every mask on one
    baseline without measuring pixels in the browser.
    """
    masks: dict[int, np.ndarray] = {}
    metrics: dict[str, dict[str, int]] = {}
    for cp in codepoints:
        got = glyph_fn(chr(cp), cap_px)
        if got is None:
            continue
        image, _height, offset = got
        full = np.asarray(image) > 127
        ys, xs = np.nonzero(full)
        if ys.size == 0:
            continue
        tight = full[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
        trimmed_bottom = full.shape[0] - 1 - int(ys.max())
        masks[cp] = tight
        metrics[str(cp)] = {
            "width": int(tight.shape[1]),
            "height": int(tight.shape[0]),
            "offset": int(offset) - trimmed_bottom,
        }
    return masks, {"capHeight": int(cap_px), "glyphs": metrics}


# ---------------------------------------------------------------------------
# Finale: labels, reveal groups, logo, real scan.
# ---------------------------------------------------------------------------


def _ner_tag(labels: Sequence[str] | None) -> str:
    for label in labels or ():
        name = str(label or "").strip()
        if name and name != "O":
            return name if name[:2] in ("B-", "I-") else f"B-{name}"
    return "O"


def label_file(
    box_sink: Sequence[dict[str, Any]],
    words: Sequence[dict[str, Any]],
    *,
    width: int,
    height: int,
    merchant: str,
    receipt_key: str,
    margin: int = RENDER_MARGIN,
) -> dict[str, Any]:
    """``final.labels.json`` from the renderer's render-true ``box_sink``.

    Pixel boxes map into the renderer's inner box (``margin`` inset) as
    0-1000 LayoutLM coordinates with y UP, the convention
    ``labelGeometry.ts`` ``toCssRectInner`` inverts. ``word_index`` on a
    sink entry is the ``_box_index`` the exporter stamped on every source
    word, which is how each drawn token gets its source word's label.
    """
    inner_w = float(width - 2 * margin)
    inner_h = float(height - 2 * margin)
    tokens: list[str] = []
    bboxes: list[list[float]] = []
    tags: list[str] = []
    totals: list[str] = []
    for entry in box_sink:
        x0, y0, x1, y1 = (float(v) for v in entry["px"])
        box = [
            (x0 - margin) / inner_w * 1000.0,
            (1.0 - (y1 - margin) / inner_h) * 1000.0,
            (x1 - margin) / inner_w * 1000.0,
            (1.0 - (y0 - margin) / inner_h) * 1000.0,
        ]
        box = [min(1000.0, max(0.0, v)) for v in box]
        if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
            continue
        text = str(entry.get("text") or "")
        idx = entry.get("word_index")
        labels: Sequence[str] | None = None
        if isinstance(idx, int) and 0 <= idx < len(words):
            labels = words[idx].get("labels")
        tag = _ner_tag(labels)
        if tag == "B-GRAND_TOTAL":
            totals.append(text)
        tokens.append(text)
        bboxes.append(box)
        tags.append(tag)
    metadata: dict[str, Any] = {
        "operation": "re_render_real_receipt",
        "boxes": "render_true",
        "render": {
            "width": int(width),
            "height": int(height),
            "margin": margin,
        },
    }
    # The amount, not the "TOTAL" caption that shares the label.
    amounts = [t for t in totals if any(ch.isdigit() for ch in t)]
    if amounts or totals:
        metadata["quality"] = {"grand_total": (amounts or totals)[0]}
    return {
        "tokens": tokens,
        "bboxes": bboxes,
        "ner_tags": tags,
        "merchant_name": merchant,
        "receipt_key": receipt_key,
        "metadata": metadata,
    }


def compose_steps(
    labels: dict[str, Any],
    *,
    bands: Sequence[float] = COMPOSE_BANDS,
) -> dict[str, Any]:
    """``compose_steps.json``: token indices split into reveal groups.

    Groups are y-bands of the receipt (fractions of the height from the
    top, ``bands`` = header/items, items/summary, summary/footer cuts),
    decided on each token's render-true box centre. The four groups then
    type out in parallel in act 4, so the split only has to read as
    "top of receipt, body, totals, trailer".
    """
    groups: dict[str, list[int]] = {name: [] for name in COMPOSE_GROUP_ORDER}
    for idx, box in enumerate(labels["bboxes"]):
        y_from_top = (2000.0 - float(box[1]) - float(box[3])) / 2000.0
        group = COMPOSE_GROUP_ORDER[-1]
        for name, cut in zip(COMPOSE_GROUP_ORDER, bands):
            if y_from_top < cut:
                group = name
                break
        groups[group].append(idx)
    return {"groups": groups, "tokens_total": len(labels["tokens"])}


def logo_mask(logo: Image.Image) -> Image.Image:
    """Vault logo (black ink on white paper) -> trimmed RGBA alpha mask.

    Alpha is ``255 - grey`` so a binary logo master becomes a binary mask
    and an anti-aliased one keeps its edges; RGB stays 0 (painted with
    ``mask-image`` in currentColor). The mask is trimmed to its ink so the
    finale's ``mask-size: contain`` box is filled by the mark itself.
    """
    gray = np.asarray(logo.convert("L")).astype(np.uint8)
    alpha = (255 - gray).astype(np.uint8)
    trimmed = tight_crop(alpha)
    if trimmed is None:
        raise ValueError("logo has no ink")
    rgba = np.zeros((*trimmed.shape, 4), dtype=np.uint8)
    rgba[..., 3] = trimmed
    return Image.fromarray(rgba, "RGBA")


def receipt_height(width: int, height: int, target_width: int) -> int:
    """Height of a receipt scaled to ``target_width`` (true aspect kept)."""
    return max(1, int(round(target_width * height / float(width))))


def normalize_real(
    scan: Image.Image, *, width: int = RECEIPT_WIDTH, height: int | None = None
) -> Image.Image:
    """The real scan at the finale's normalised width.

    ``height`` pins the exact pixel height when the caller already knows the
    synthetic twin's canvas (real and synth must align 1:1 for the wipe).
    """
    if height is None:
        height = receipt_height(scan.width, scan.height, width)
    return scan.convert("RGB").resize((width, height), Image.LANCZOS)


def thumbnail(scan: Image.Image, *, height: int = THUMB_HEIGHT) -> Image.Image:
    width = max(1, int(round(height * scan.width / float(scan.height))))
    return scan.convert("RGB").resize((width, height), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Measured style: display strings + real-receipt crops.
# ---------------------------------------------------------------------------

_BODY_DISPLAY = "Body text"


def style_display(section: dict[str, Any]) -> str:
    """One measured claim per section, from the stylemap's numbers only."""
    parts: list[str] = []
    rate = section.get("underlineRate")
    underline = section.get("underline")
    if isinstance(rate, (int, float)) and rate >= 0.25:
        parts.append(f"Underlined ~{rate:.0%} of the time")
    elif underline is True:
        parts.append("Underlined")
    weight = str(section.get("weight") or "normal").lower()
    if weight in ("bold", "heavy"):
        parts.append("Bold")
    size = section.get("sizeScale")
    if isinstance(size, (int, float)):
        if size >= 1.08:
            parts.append(f"{size - 1.0:.0%} taller")
        elif size <= 0.92:
            parts.append(f"{1.0 - size:.0%} smaller")
        elif size < 0.97:
            parts.append("Slightly condensed")
    reverse = section.get("reverseVideo")
    if reverse:
        parts.append("White-on-black (reverse video)")
    return " + ".join(parts) if parts else _BODY_DISPLAY


def style_annotated(
    stylemap: dict[str, Any],
    *,
    merchant: str,
    crops: dict[str, str] | None = None,
) -> dict[str, Any]:
    """``style_annotated.json``: the stylemap's notable sections.

    Sections whose measured values are indistinguishable from body text are
    dropped; the rest carry their measured fields through untouched plus a
    ``display`` claim derived from those numbers and an optional ``crop``
    path (``style_crops/<name>.png``).
    """
    crops = crops or {}
    sections: list[dict[str, Any]] = []
    for name, measured in (stylemap.get("sections") or {}).items():
        if not isinstance(measured, dict):
            continue
        display = style_display(measured)
        if display == _BODY_DISPLAY and name not in crops:
            continue
        entry: dict[str, Any] = {"name": name, "display": display}
        if name in crops:
            entry["crop"] = crops[name]
        for key, val in measured.items():
            if key not in entry:
                entry[key] = val
        sections.append(entry)
    return {"merchant": merchant, "sections": sections}


def word_box(word: dict[str, Any]) -> tuple[float, float, float, float]:
    """``(x0, y_low, x1, y_high)`` of a payload word, y UP.

    Payload boxes are ``[tl.x, tl.y, br.x, br.y]`` in 0-1000 y-up space, so
    the top-left corner carries the LARGER y; normalise once here.
    """
    x0, ya, x1, yb = (float(v) for v in word["bbox"])
    return min(x0, x1), min(ya, yb), max(x0, x1), max(ya, yb)


def group_lines(
    words: Sequence[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Words -> printed lines by ``line_id``, top of receipt first."""
    by_line: dict[Any, list[dict[str, Any]]] = {}
    for word in words:
        by_line.setdefault(word.get("line_id"), []).append(word)
    lines = list(by_line.values())
    for line in lines:
        line.sort(key=lambda w: word_box(w)[0])
    lines.sort(key=lambda ln: -max(word_box(w)[3] for w in ln))
    return lines


def style_crop_boxes(
    words: Sequence[dict[str, Any]],
    classify: Callable[[str], str],
    sections: Iterable[str],
    *,
    matches: dict[str, str] | None = None,
    max_lines: int = 3,
) -> dict[str, list[float]]:
    """Union box (0-1000, y-up) of the first run of lines per section.

    ``classify`` maps a line's text to a section name (the stylescan
    classifier); ``matches`` optionally overrides a section with the
    stylemap's own ``match`` regex (Sprouts' ``balance_due``). The first
    matching line and up to ``max_lines - 1`` matching lines directly after
    it form the crop, so a section header shows with its neighbours.
    """
    matches = matches or {}
    lines = group_lines(words)
    texts = [" ".join(str(w.get("text") or "") for w in ln) for ln in lines]
    out: dict[str, list[float]] = {}
    for name in sections:
        pattern = matches.get(name)
        if pattern:
            rx = re.compile(pattern, re.I)
            hits = [bool(rx.search(t)) for t in texts]
        else:
            hits = [classify(t) == name for t in texts]
        start = next((i for i, hit in enumerate(hits) if hit), None)
        if start is None:
            continue
        end = start
        while end + 1 < len(lines) and end + 1 - start < max_lines:
            if not hits[end + 1]:
                break
            end += 1
        boxes = [word_box(w) for ln in lines[start : end + 1] for w in ln]
        out[name] = [
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        ]
    return out


def crop_receipt(
    scan: Image.Image, box: Sequence[float], *, pad_frac: float = 0.15
) -> Image.Image:
    """Crop a 0-1000 y-up box out of the real scan with a little air."""
    w, h = scan.size
    x0, y0, x1, y1 = (float(v) for v in box)
    pad_y = (y1 - y0) * pad_frac
    pad_x = (x1 - x0) * 0.03
    left = int(max(0.0, (x0 - pad_x) / 1000.0 * w))
    right = int(min(float(w), (x1 + pad_x) / 1000.0 * w))
    top = int(max(0.0, (1.0 - (y1 + pad_y) / 1000.0) * h))
    bottom = int(min(float(h), (1.0 - (y0 - pad_y) / 1000.0) * h))
    if right <= left or bottom <= top:
        raise ValueError(f"degenerate crop box {box!r}")
    return scan.convert("RGB").crop((left, top, right, bottom))

"""Style-aware glyph atlas built from a merchant's real receipt letter crops.

Milestone M1 of the glyph-rendering charter. A :class:`GlyphAtlas` holds the
*actual letterform crops* a merchant prints — one library of real ink glyphs,
keyed by visual *style* so a renderer can stamp a synthesized receipt in the
merchant's own font instead of a generic monospace.

What the data actually shows (resolved empirically before this was written, see
``docs/glyph-atlas-style-findings.md``)
----------------------------------------------------------------------------
The charter's working assumption was that a receipt mixes several fonts
(header / body / totals / bold). Looking at real Vons receipts plus #994's
letter- and line-level clustering, that is **not** what the data shows:

* The body is **one monospace family**. Items, prices, addresses, column
  headers and the totals block are all the same face at the same size — the
  totals are *not* a distinct font.
* The only genuine intra-receipt variation is a **bold weight** applied to whole
  lines (the "Member Savings" promo lines and category section headers). #994's
  *letter*-level clustering collapses this to one cluster (same letterforms,
  heavier ink); #994's *line*-level clustering separates those lines because the
  line signature carries ink/stroke aggregates.
* A **logo wordmark** ("VONS") sits at the top — a large display mark, ~3.5x the
  body height, effectively a fixed image rather than glyph-composable text.

So the atlas is keyed by **weight tier** (``body`` regular vs ``bold``), with the
logo captured separately as an image asset. This keeps the structure honest to
the data and generic: a merchant that genuinely prints a second face will form a
second #994 letter cluster, which :func:`build_glyph_atlas` keeps as its own
style; a merchant that only varies weight (the common case) gets the
regular/bold split this module derives at the line level.

Organizing principle (CHARTER.md): this is *review-realism only*. The atlas is
DATA consumed by the renderer; it never feeds or relaxes a training gate.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable, Mapping, Sequence


def _ensure_receipt_upload_on_path() -> None:
    """Make the sibling ``receipt_upload`` package importable (see font_profile)."""
    try:  # pragma: no cover - fast path when already importable
        import receipt_upload.font_letter_analysis  # noqa: F401

        return
    except ImportError:
        pass
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), *([os.pardir] * 5))
    )
    candidate = os.path.join(repo_root, "receipt_upload")
    if candidate not in sys.path:
        sys.path.insert(0, candidate)
    cached = sys.modules.get("receipt_upload")
    if cached is not None and getattr(cached, "__file__", None) is None:
        del sys.modules["receipt_upload"]


_ensure_receipt_upload_on_path()

from PIL import Image, ImageOps  # noqa: E402

from receipt_upload.font_analysis import (  # noqa: E402
    _coerce_image,
    _is_normalized_box,
    _otsu_threshold,
)
from receipt_upload.font_letter_analysis import (  # noqa: E402
    LetterImageSample,
    build_letter_image_samples,
    cluster_letter_styles,
)

_EPSILON = 1e-9

# --------------------------------------------------------------------------- #
# tunables (documented; conservative)
# --------------------------------------------------------------------------- #

# A line whose median ink_ratio exceeds the receipt body baseline by this factor
# is treated as a *bold* line. Heuristic — bold is a per-line decision because
# that is how receipts apply it (whole promo / section-header lines). Reported in
# the variation report so a reviewer can see the separation that justified it.
_BOLD_INK_FACTOR = 1.18

# A line whose median glyph height exceeds the body median by this factor is a
# display / logo line, not body text; its glyphs are excluded from the per-char
# styles and the line is captured as a logo image asset instead.
_LOGO_HEIGHT_FACTOR = 1.9

# Cap stored crops per (style, char) to bound atlas size; representatives are
# chosen nearest the style's median glyph height.
_MAX_VARIANTS_PER_CHAR = 8

# Skip any "letter" whose box is wider than this multiple of the receipt's median
# glyph width — those are whole-word / merged OCR boxes, not single glyphs. The
# relative cap is paired with an absolute normalized ceiling because a receipt
# whose OCR mis-segments most letters into word boxes inflates its own median.
_MAX_LETTER_WIDTH_FACTOR = 2.4
_MAX_LETTER_WIDTH_NORM = 0.055

# Reject an extracted glyph whose ink is wider than this multiple of its height.
# Single characters top out around 1.3-1.6 ('W', 'M', '%'); anything wider is a
# multi-char / horizontal-streak crop. Char-agnostic, so it catches merged crops
# the box guards miss (e.g. a per-receipt median that is itself inflated).
# Punctuation does NOT need an exemption: on real thermal receipts a tight-ink
# hyphen / equals crop measures ~1.1-1.6 aspect (the mark is thick), so it passes
# this guard on its own — and a char-agnostic guard keeps no merge hole open.
_MAX_GLYPH_ASPECT = 1.7

# Drop near-transparent paper noise below this alpha when building a glyph.
_MIN_INK_ALPHA = 24


@dataclass(frozen=True)
class GlyphCrop:
    """A single real letterform crop, ink-on-transparent, tight to content."""

    char: str
    normalized_char: str
    image: Image.Image  # RGBA, ink alpha; black ink (recolorable by renderer)
    width: int
    height: int
    aspect: float  # content width / height
    ink_ratio: float
    box_height_norm: float  # normalized receipt height — scale reference
    source_image_id: str | None
    source_receipt_id: int | None
    sample_id: str


@dataclass(frozen=True)
class AtlasStyle:
    """One visual style in the atlas: a library of real glyph crops by char."""

    style_id: str  # "body", "bold", or "cluster<N>" for extra #994 faces
    weight: str  # "regular" | "bold"
    glyphs: Mapping[str, tuple[GlyphCrop, ...]]  # normalized_char -> crops
    sample_count: int
    line_count: int
    median_box_height: float
    median_ink_ratio: float
    y_span: tuple[float, float]
    cluster_label: str  # #994 descriptive label for the dominant face

    def chars(self) -> set[str]:
        return set(self.glyphs)

    def glyph_for(
        self, char: str, *, index: int = 0
    ) -> GlyphCrop | None:
        crops = self.glyphs.get(_norm_char(char))
        if not crops:
            return None
        return crops[index % len(crops)]


@dataclass(frozen=True)
class GlyphAtlas:
    """A merchant's style-aware library of real glyph crops."""

    merchant_name: str
    receipt_count: int
    styles: Mapping[str, AtlasStyle]
    logo: Image.Image | None
    logo_text: str | None
    letter_cluster_count: int  # #994 letter-style clusters observed (median)
    notes: tuple[str, ...] = ()

    # ----- lookup -----
    def style_for_role(
        self, role: str = "body", *, bold: bool = False
    ) -> AtlasStyle | None:
        """Pick the atlas style for a line role.

        ``role`` is a coarse semantic hint (``header`` / ``body`` / ``total`` /
        ``item``). Empirically only weight separates body text, so this maps to
        the ``bold`` style when ``bold`` is requested and one exists, else
        ``body``. ``header`` falls back to body glyphs (the logo is a separate
        image asset, not glyph-composed).
        """
        if bold and "bold" in self.styles:
            return self.styles["bold"]
        if "body" in self.styles:
            return self.styles["body"]
        # Degenerate atlas: return the largest style by sample count.
        if not self.styles:
            return None
        return max(self.styles.values(), key=lambda s: s.sample_count)

    def glyph(
        self,
        char: str,
        *,
        role: str = "body",
        bold: bool = False,
        index: int = 0,
    ) -> GlyphCrop | None:
        style = self.style_for_role(role, bold=bold)
        if style is None:
            return None
        crop = style.glyph_for(char, index=index)
        if crop is None and bold:  # bold lacks this char -> body fallback
            body = self.styles.get("body")
            if body is not None:
                crop = body.glyph_for(char, index=index)
        return crop

    def covered_chars(self) -> set[str]:
        chars: set[str] = set()
        for style in self.styles.values():
            chars |= style.chars()
        return chars

    # ----- reporting -----
    def variation_report(self) -> dict[str, Any]:
        """Human-readable summary of the intra-receipt style variation found."""
        return {
            "merchant_name": self.merchant_name,
            "receipt_count": self.receipt_count,
            "letter_cluster_count_median": self.letter_cluster_count,
            "logo_present": self.logo is not None,
            "logo_text": self.logo_text,
            "covered_char_count": len(self.covered_chars()),
            "styles": {
                style_id: {
                    "weight": style.weight,
                    "samples": style.sample_count,
                    "lines": style.line_count,
                    "distinct_chars": len(style.chars()),
                    "median_box_height": round(style.median_box_height, 5),
                    "median_ink_ratio": round(style.median_ink_ratio, 4),
                    "y_span": [round(v, 3) for v in style.y_span],
                    "cluster_label": style.cluster_label,
                }
                for style_id, style in sorted(self.styles.items())
            },
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# glyph crop extraction
# --------------------------------------------------------------------------- #


def _glyph_crop_bounds(
    box: Mapping[str, float],
    *,
    image_size: tuple[int, int],
    y_origin: str,
    expand_x_ratio: float,
    expand_y_ratio: float,
) -> tuple[int, int, int, int] | None:
    """Pixel bounds for a glyph crop.

    Unlike #994's ``_image_crop_bounds`` (which forces a >=2px pad on every side
    to capture neighborhood context for *feature* extraction), this expands by a
    fraction of the box and defaults to **zero horizontal expansion** — letter
    advances are tight, so any horizontal pad bleeds the neighbor glyph into the
    crop. A small vertical pad recovers ascenders/descenders the OCR box clips.
    """
    image_width, image_height = image_size
    x, y = box["x"], box["y"]
    width = max(box["width"], _EPSILON)
    height = max(box["height"], _EPSILON)
    if _is_normalized_box(box):
        left = x * image_width
        right = (x + width) * image_width
        if y_origin == "bottom_left":
            top = (1.0 - y - height) * image_height
            bottom = (1.0 - y) * image_height
        else:
            top = y * image_height
            bottom = (y + height) * image_height
    else:
        left = x
        right = x + width
        if y_origin == "bottom_left":
            top = image_height - y - height
            bottom = image_height - y
        else:
            top = y
            bottom = y + height
    crop_w = max(right - left, 1.0)
    crop_h = max(bottom - top, 1.0)
    ex = crop_w * expand_x_ratio
    ey = crop_h * expand_y_ratio
    left = max(0, int(round(left - ex)))
    top = max(0, int(round(top - ey)))
    right = min(image_width, int(round(right + ex)))
    bottom = min(image_height, int(round(bottom + ey)))
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def extract_glyph_image(
    raw_image: Image.Image,
    box: Mapping[str, float],
    *,
    y_origin: str = "bottom_left",
    expand_x_ratio: float = 0.0,
    expand_y_ratio: float = 0.12,
) -> Image.Image | None:
    """Crop one letterform from the raw receipt as ink-on-transparent RGBA.

    Crops tight to the OCR letter box (no horizontal neighbor pad), then converts
    to alpha where alpha encodes ink darkness (paper -> transparent) and tightens
    to the ink bbox so the renderer can place it by content. The antialiased ink
    edge is preserved. Returns ``None`` when the crop has no usable ink.
    """
    bounds = _glyph_crop_bounds(
        box,
        image_size=raw_image.size,
        y_origin=y_origin,
        expand_x_ratio=expand_x_ratio,
        expand_y_ratio=expand_y_ratio,
    )
    if bounds is None:
        return None
    crop = raw_image.crop(bounds)
    if crop.width < 2 or crop.height < 2:
        return None

    gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
    pixels = list(gray.getdata())
    if not pixels:
        return None
    threshold = _otsu_threshold(pixels)

    # Decide ink polarity from the BACKGROUND, not a global minority count: a
    # dense or bold glyph can have more ink than paper, so "ink = minority" would
    # wrongly flip it and turn paper into opaque ink. The crop's border ring is
    # overwhelmingly paper (the vertical pad guarantees paper top/bottom rows
    # even when a glyph touches the left/right edges); if that border is dark,
    # the scan is inverted (light ink on dark paper) and we flip so paper -> ~255.
    if _border_median(gray) <= threshold:
        gray = ImageOps.invert(gray)
        pixels = list(gray.getdata())
        threshold = _otsu_threshold(pixels)

    # alpha = ink darkness = 255 - gray; zero out paper-bright pixels + faint noise.
    alpha_pixels = bytearray(len(pixels))
    cutoff = min(255, threshold + 1)
    for i, value in enumerate(pixels):
        if value >= cutoff:
            continue
        a = 255 - value
        if a >= _MIN_INK_ALPHA:
            alpha_pixels[i] = a
    alpha = Image.frombytes("L", gray.size, bytes(alpha_pixels))

    ink_bbox = alpha.getbbox()
    if ink_bbox is None:
        return None
    alpha = alpha.crop(ink_bbox)
    if alpha.width < 1 or alpha.height < 1:
        return None

    glyph = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    glyph.putalpha(alpha)
    return glyph


def _border_median(gray: Image.Image) -> float:
    """Median gray of the crop's 1px border ring (its background sample)."""
    width, height = gray.size
    px = gray.load()
    values: list[int] = []
    for x in range(width):
        values.append(px[x, 0])
        values.append(px[x, height - 1])
    for y in range(height):
        values.append(px[0, y])
        values.append(px[width - 1, y])
    if not values:
        return 255.0
    return float(median(values))


# --------------------------------------------------------------------------- #
# atlas construction
# --------------------------------------------------------------------------- #


@dataclass
class _ReceiptInput:
    image_id: str | None
    receipt_id: int | None
    letters: Sequence[Any]
    raw_image: Any


def build_glyph_atlas(
    receipts: Iterable[Mapping[str, Any] | _ReceiptInput],
    merchant_name: str,
    *,
    image_y_origin: str = "bottom_left",
    eps: float = 0.78,
    min_samples: int = 5,
    min_letters_per_line: int = 3,
    max_variants_per_char: int = _MAX_VARIANTS_PER_CHAR,
    bold_ink_factor: float = _BOLD_INK_FACTOR,
    logo_height_factor: float = _LOGO_HEIGHT_FACTOR,
    max_letter_width_factor: float = _MAX_LETTER_WIDTH_FACTOR,
    max_glyph_aspect: float = _MAX_GLYPH_ASPECT,
) -> GlyphAtlas | None:
    """Build a style-aware glyph atlas from real receipts.

    ``receipts`` is an iterable of ``{"image_id", "receipt_id", "letters",
    "raw_image"}`` mappings (``raw_image`` a PIL image or anything #994 can
    coerce). Pure / testable — the Dynamo wiring lives in
    :func:`build_glyph_atlas_from_dynamo`.
    """
    # style_id -> normalized_char -> list[GlyphCrop]
    style_glyphs: dict[str, dict[str, list[GlyphCrop]]] = defaultdict(
        lambda: defaultdict(list)
    )
    style_samples: dict[str, list[LetterImageSample]] = defaultdict(list)
    style_lines: dict[str, set[tuple[Any, int]]] = defaultdict(set)
    style_labels: dict[str, list[str]] = defaultdict(list)
    cluster_counts: list[int] = []
    logo: Image.Image | None = None
    logo_text: str | None = None
    logo_height = 0.0
    receipts_used = 0
    notes: list[str] = []

    for entry in receipts:
        image_id, receipt_id, letters, raw = _unpack_receipt(entry)
        if not letters or raw is None:
            continue
        try:
            raw_image = _coerce_image(raw)
        except Exception:  # noqa: BLE001 - a bad image must not sink the atlas
            continue
        samples = build_letter_image_samples(
            letters, raw_image=raw_image, image_y_origin=image_y_origin
        )
        if not samples:
            continue
        receipts_used += 1

        analysis = cluster_letter_styles(
            samples, eps=eps, min_samples=min_samples
        )
        cluster_counts.append(max(1, len(analysis.clusters)))

        body_height = _median([s.metrics.get("box_height", 0.0) for s in samples])
        body_width = _median([s.metrics.get("box_width", 0.0) for s in samples])
        # Some OCR "letters" carry a whole-word (or line) box; cropping those
        # yields a multi-char blob that would double-ink stamped text. Skip any
        # letter whose box is far wider/taller than the receipt's median glyph.
        width_cap = (
            min(body_width * max_letter_width_factor, _MAX_LETTER_WIDTH_NORM)
            if body_width > 0
            else _MAX_LETTER_WIDTH_NORM
        )
        height_cap = (
            body_height * logo_height_factor if body_height > 0 else None
        )
        line_groups = _group_by_line(samples)
        line_tier = _classify_lines(
            line_groups,
            body_height=body_height,
            bold_ink_factor=bold_ink_factor,
            logo_height_factor=logo_height_factor,
        )

        # Capture the tallest logo line as an image asset (best across receipts).
        for line_key, tier in line_tier.items():
            if tier != "logo":
                continue
            line_samples = line_groups[line_key]
            lh = _median([s.metrics.get("box_height", 0.0) for s in line_samples])
            if lh <= logo_height:
                continue
            captured = _capture_logo(
                line_samples, raw_image, image_y_origin
            )
            if captured is not None:
                logo, logo_text = captured
                logo_height = lh

        # Assign each sample to a weight-tier style and stamp its real glyph.
        # Style id is the line tier (body / bold) only — #994 letter clusters are
        # NOT used as style ids because DBSCAN cluster ids are local to each
        # receipt's clustering, so they cannot be aggregated across receipts into
        # one merchant atlas (same id != same face). The cluster *label* is kept
        # for description; a >1-cluster merchant is flagged in notes as needing
        # global cross-receipt clustering (future work).
        cluster_label_by_id: dict[int, str] = {
            c.cluster_id: c.label for c in analysis.clusters
        }
        for sample in samples:
            line_key = (sample.image_id, sample.line_id)
            tier = line_tier.get(line_key, "body")
            if tier == "logo":
                continue  # display mark, not a body glyph
            box = _box_from_metrics(sample.metrics)
            if box is None:
                continue
            if box["width"] > width_cap:
                continue  # whole-word / merged box, not a single glyph
            if height_cap is not None and box["height"] >= height_cap:
                continue  # stray display-size glyph on a body line
            glyph_image = extract_glyph_image(
                raw_image, box, y_origin=image_y_origin
            )
            if glyph_image is None:
                continue
            if glyph_image.width > glyph_image.height * max_glyph_aspect:
                continue  # multi-char / streak crop the box guards missed
            style_id = "bold" if tier == "bold" else "body"
            cluster_id = analysis.cluster_for_sample(sample.sample_id)
            crop = GlyphCrop(
                char=sample.text,
                normalized_char=sample.normalized_char,
                image=glyph_image,
                width=glyph_image.width,
                height=glyph_image.height,
                aspect=glyph_image.width / max(glyph_image.height, 1),
                ink_ratio=float(sample.metrics.get("ink_ratio", 0.0)),
                box_height_norm=float(sample.metrics.get("box_height", 0.0)),
                source_image_id=sample.image_id,
                source_receipt_id=sample.receipt_id,
                sample_id=sample.sample_id,
            )
            style_glyphs[style_id][sample.normalized_char].append(crop)
            style_samples[style_id].append(sample)
            style_lines[style_id].add(line_key)
            label = (
                cluster_label_by_id.get(cluster_id, "")
                if cluster_id is not None
                else ""
            )
            if label:
                style_labels[style_id].append(label)

    if receipts_used == 0 or not style_glyphs:
        return None

    styles = _finalize_styles(
        style_glyphs,
        style_samples,
        style_lines,
        style_labels,
        max_variants_per_char=max_variants_per_char,
    )
    if "bold" not in styles:
        notes.append(
            "no bold weight tier separated; receipt(s) appear single-weight"
        )
    if logo is None:
        notes.append("no logo/display line detected above body height")
    median_clusters = int(_median(cluster_counts)) if cluster_counts else 1
    if median_clusters > 1:
        notes.append(
            f"#994 letter clustering found >1 face (median {median_clusters}); "
            "this atlas keys by weight only — a genuinely multi-face merchant "
            "needs global cross-receipt clustering to separate faces (future work)"
        )

    return GlyphAtlas(
        merchant_name=merchant_name,
        receipt_count=receipts_used,
        styles=styles,
        logo=logo,
        logo_text=logo_text,
        letter_cluster_count=median_clusters,
        notes=tuple(notes),
    )


def build_glyph_atlas_from_dynamo(
    table_name: str,
    merchant_name: str,
    *,
    region: str = "us-east-1",
    max_receipts: int | None = 8,
    image_y_origin: str = "bottom_left",
    **atlas_kwargs: Any,
) -> GlyphAtlas | None:
    """Build a merchant glyph atlas from real receipts in Dynamo/S3.

    Resolves receipts via :class:`ReceiptPlace` (``ReceiptMetadata`` is
    deprecated), loads letters + the raw receipt crop per receipt, and delegates
    to :func:`build_glyph_atlas`. ``max_receipts`` bounds the S3 image pulls.
    """
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_upload.font_analysis import load_raw_image_from_s3

    client = DynamoClient(table_name=table_name, region=region)
    places, _ = client.get_receipt_places_by_merchant(merchant_name)
    seen: set[tuple[str, int]] = set()
    targets: list[tuple[str, int]] = []
    for place in places:
        key = (str(place.image_id), int(place.receipt_id))
        if key in seen:
            continue
        seen.add(key)
        targets.append(key)
    if max_receipts is not None:
        targets = targets[:max_receipts]

    receipt_inputs: list[_ReceiptInput] = []
    for image_id, receipt_id in targets:
        try:
            details = client.get_image_details(image_id)
            letters = [
                letter
                for letter in details.receipt_letters
                if letter.receipt_id == receipt_id
            ]
            if not letters:
                continue
            receipt = next(
                (
                    candidate
                    for candidate in details.receipts
                    if candidate.receipt_id == receipt_id
                ),
                None,
            )
            if receipt is None:
                continue
            raw_image = load_raw_image_from_s3(receipt)
        except Exception:  # noqa: BLE001 - one bad receipt must not abort
            continue
        receipt_inputs.append(
            _ReceiptInput(
                image_id=image_id,
                receipt_id=receipt_id,
                letters=letters,
                raw_image=raw_image,
            )
        )

    return build_glyph_atlas(
        receipt_inputs,
        merchant_name,
        image_y_origin=image_y_origin,
        **atlas_kwargs,
    )


# --------------------------------------------------------------------------- #
# persistence (so M2/M3 reuse an atlas without re-fetching)
# --------------------------------------------------------------------------- #


def save_atlas(atlas: GlyphAtlas, directory: str) -> str:
    """Persist an atlas as PNG glyph crops + an ``atlas.json`` index."""
    os.makedirs(directory, exist_ok=True)
    index: dict[str, Any] = {
        "merchant_name": atlas.merchant_name,
        "receipt_count": atlas.receipt_count,
        "letter_cluster_count": atlas.letter_cluster_count,
        "logo_text": atlas.logo_text,
        "logo_file": None,
        "notes": list(atlas.notes),
        "styles": {},
    }
    if atlas.logo is not None:
        logo_file = "logo.png"
        atlas.logo.save(os.path.join(directory, logo_file))
        index["logo_file"] = logo_file

    for style_id, style in atlas.styles.items():
        style_dir = os.path.join(directory, style_id)
        os.makedirs(style_dir, exist_ok=True)
        glyph_index: dict[str, list[dict[str, Any]]] = {}
        for char, crops in style.glyphs.items():
            entries = []
            for i, crop in enumerate(crops):
                fname = f"{_safe_char_name(char)}_{i}.png"
                crop.image.save(os.path.join(style_dir, fname))
                entries.append(
                    {
                        "file": fname,
                        "char": crop.char,
                        "aspect": round(crop.aspect, 4),
                        "ink_ratio": round(crop.ink_ratio, 4),
                        "box_height_norm": round(crop.box_height_norm, 5),
                        "source_image_id": crop.source_image_id,
                        "source_receipt_id": crop.source_receipt_id,
                    }
                )
            glyph_index[char] = entries
        index["styles"][style_id] = {
            "weight": style.weight,
            "sample_count": style.sample_count,
            "line_count": style.line_count,
            "median_box_height": round(style.median_box_height, 5),
            "median_ink_ratio": round(style.median_ink_ratio, 4),
            "y_span": list(style.y_span),
            "cluster_label": style.cluster_label,
            "glyphs": glyph_index,
        }

    path = os.path.join(directory, "atlas.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2)
    return path


def load_atlas(directory: str) -> GlyphAtlas:
    """Load an atlas previously written by :func:`save_atlas`."""
    with open(os.path.join(directory, "atlas.json"), encoding="utf-8") as handle:
        index = json.load(handle)

    styles: dict[str, AtlasStyle] = {}
    for style_id, raw_style in index.get("styles", {}).items():
        style_dir = os.path.join(directory, style_id)
        glyphs: dict[str, tuple[GlyphCrop, ...]] = {}
        for char, entries in raw_style.get("glyphs", {}).items():
            crops = []
            for entry in entries:
                image = Image.open(
                    os.path.join(style_dir, entry["file"])
                ).convert("RGBA")
                image.load()
                crops.append(
                    GlyphCrop(
                        char=entry["char"],
                        normalized_char=char,
                        image=image,
                        width=image.width,
                        height=image.height,
                        aspect=float(entry["aspect"]),
                        ink_ratio=float(entry["ink_ratio"]),
                        box_height_norm=float(entry["box_height_norm"]),
                        source_image_id=entry.get("source_image_id"),
                        source_receipt_id=entry.get("source_receipt_id"),
                        sample_id="",
                    )
                )
            glyphs[char] = tuple(crops)
        styles[style_id] = AtlasStyle(
            style_id=style_id,
            weight=raw_style["weight"],
            glyphs=glyphs,
            sample_count=raw_style["sample_count"],
            line_count=raw_style["line_count"],
            median_box_height=raw_style["median_box_height"],
            median_ink_ratio=raw_style["median_ink_ratio"],
            y_span=tuple(raw_style["y_span"]),
            cluster_label=raw_style["cluster_label"],
        )

    logo = None
    if index.get("logo_file"):
        logo = Image.open(
            os.path.join(directory, index["logo_file"])
        ).convert("RGBA")
        logo.load()

    return GlyphAtlas(
        merchant_name=index["merchant_name"],
        receipt_count=index["receipt_count"],
        styles=styles,
        logo=logo,
        logo_text=index.get("logo_text"),
        letter_cluster_count=index.get("letter_cluster_count", 1),
        notes=tuple(index.get("notes", ())),
    )


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _unpack_receipt(
    entry: Mapping[str, Any] | _ReceiptInput,
) -> tuple[str | None, int | None, Sequence[Any], Any]:
    if isinstance(entry, _ReceiptInput):
        return entry.image_id, entry.receipt_id, entry.letters, entry.raw_image
    return (
        entry.get("image_id"),
        entry.get("receipt_id"),
        entry.get("letters") or (),
        entry.get("raw_image"),
    )


def _group_by_line(
    samples: Sequence[LetterImageSample],
) -> dict[tuple[Any, int], list[LetterImageSample]]:
    groups: dict[tuple[Any, int], list[LetterImageSample]] = defaultdict(list)
    for sample in samples:
        groups[(sample.image_id, sample.line_id)].append(sample)
    return groups


def _classify_lines(
    line_groups: Mapping[tuple[Any, int], Sequence[LetterImageSample]],
    *,
    body_height: float,
    bold_ink_factor: float,
    logo_height_factor: float,
) -> dict[tuple[Any, int], str]:
    """Tag each line ``body`` / ``bold`` / ``logo`` from height + ink density.

    Bold is decided relative to the receipt's *body* ink baseline (the median
    over non-logo lines), so it is robust to overall scan darkness.
    """
    line_height: dict[tuple[Any, int], float] = {}
    line_ink: dict[tuple[Any, int], float] = {}
    for key, line_samples in line_groups.items():
        line_height[key] = _median(
            [s.metrics.get("box_height", 0.0) for s in line_samples]
        )
        line_ink[key] = _median(
            [s.metrics.get("ink_ratio", 0.0) for s in line_samples]
        )

    logo_cut = body_height * logo_height_factor if body_height > 0 else None
    tiers: dict[tuple[Any, int], str] = {}
    body_inks: list[float] = []
    for key in line_groups:
        if logo_cut is not None and line_height[key] >= logo_cut:
            tiers[key] = "logo"
        else:
            body_inks.append(line_ink[key])
    body_ink_baseline = _median(body_inks) if body_inks else 0.0
    bold_cut = body_ink_baseline * bold_ink_factor

    for key in line_groups:
        if tiers.get(key) == "logo":
            continue
        if bold_cut > 0 and line_ink[key] >= bold_cut:
            tiers[key] = "bold"
        else:
            tiers[key] = "body"
    return tiers


def _box_from_metrics(metrics: Mapping[str, float]) -> dict[str, float] | None:
    try:
        return {
            "x": float(metrics["box_x"]),
            "y": float(metrics["box_y"]),
            "width": max(float(metrics["box_width"]), _EPSILON),
            "height": max(float(metrics["box_height"]), _EPSILON),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _capture_logo(
    line_samples: Sequence[LetterImageSample],
    raw_image: Image.Image,
    y_origin: str,
) -> tuple[Image.Image, str] | None:
    """Crop the union box of a display line as the logo image + its text."""
    boxes = [_box_from_metrics(s.metrics) for s in line_samples]
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    min_x = min(b["x"] for b in boxes)
    min_y = min(b["y"] for b in boxes)
    max_x = max(b["x"] + b["width"] for b in boxes)
    max_y = max(b["y"] + b["height"] for b in boxes)
    union = {
        "x": min_x,
        "y": min_y,
        "width": max(max_x - min_x, _EPSILON),
        "height": max(max_y - min_y, _EPSILON),
    }
    image = extract_glyph_image(
        raw_image,
        union,
        y_origin=y_origin,
        expand_x_ratio=0.02,
        expand_y_ratio=0.08,
    )
    if image is None:
        return None
    ordered = sorted(
        line_samples, key=lambda s: s.metrics.get("box_center_x", 0.0)
    )
    text = "".join(s.text for s in ordered)
    return image, text


def _finalize_styles(
    style_glyphs: Mapping[str, Mapping[str, Sequence[GlyphCrop]]],
    style_samples: Mapping[str, Sequence[LetterImageSample]],
    style_lines: Mapping[str, set[tuple[Any, int]]],
    style_labels: Mapping[str, Sequence[str]],
    *,
    max_variants_per_char: int,
) -> dict[str, AtlasStyle]:
    styles: dict[str, AtlasStyle] = {}
    for style_id, char_map in style_glyphs.items():
        samples = style_samples[style_id]
        heights = [s.metrics.get("box_height", 0.0) for s in samples]
        med_height = _median(heights)
        capped: dict[str, tuple[GlyphCrop, ...]] = {}
        for char, crops in char_map.items():
            chosen = _select_representatives(
                crops, med_height, max_variants_per_char
            )
            capped[char] = tuple(chosen)  # chosen[0] is the cleanest variant
        ys = [s.metrics.get("box_center_y", 0.0) for s in samples]
        styles[style_id] = AtlasStyle(
            style_id=style_id,
            weight="bold" if style_id == "bold" else "regular",
            glyphs=capped,
            sample_count=len(samples),
            line_count=len(style_lines[style_id]),
            median_box_height=med_height,
            median_ink_ratio=_median(
                [s.metrics.get("ink_ratio", 0.0) for s in samples]
            ),
            y_span=(min(ys), max(ys)) if ys else (0.0, 0.0),
            cluster_label=_most_common(style_labels.get(style_id, ())) or "n/a",
        )
    return styles


def _select_representatives(
    crops: Sequence[GlyphCrop], target_height: float, limit: int
) -> list[GlyphCrop]:
    """Order crops cleanest-first and keep up to ``limit``.

    "Cleanest" = nearest this char's *median* ink aspect (so a crop that bled a
    neighbor glyph, which is wider, sorts last and never becomes the
    representative) and nearest the style's median glyph height. Always sorted —
    even under the cap — so ``chosen[0]`` is the best variant for stamping.
    """
    aspects = sorted(c.aspect for c in crops)
    median_aspect = aspects[len(aspects) // 2]
    target_h = target_height if target_height > 0 else (
        median(c.box_height_norm for c in crops) if crops else 0.0
    )

    def rank(crop: GlyphCrop) -> tuple[float, float]:
        aspect_dev = abs(crop.aspect - median_aspect) / max(median_aspect, 1e-6)
        height_dev = abs(crop.box_height_norm - target_h) / max(target_h, 1e-6)
        return (aspect_dev, height_dev)

    ordered = sorted(crops, key=rank)
    return ordered[:limit]


def _median(values: Sequence[float]) -> float:
    numbers = [float(v) for v in values if v is not None]
    if not numbers:
        return 0.0
    return float(median(numbers))


def _most_common(values: Sequence[str]) -> str | None:
    if not values:
        return None
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _norm_char(char: str) -> str:
    # Mirror #994's _normalize_char (strip) so lookups match stored keys.
    text = str(char).strip()
    return text[:1] if text else text


def _safe_char_name(char: str) -> str:
    """Filesystem-safe name for a glyph char (handles ``/``, ``.``, etc.)."""
    return "u%04x" % ord(char[0]) if char else "uempty"

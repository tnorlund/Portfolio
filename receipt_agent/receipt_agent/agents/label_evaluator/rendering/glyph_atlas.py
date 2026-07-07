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
import re
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

# A captured logo line must be the merchant wordmark, not a promo/coupon banner
# (those are often the tallest text on the receipt). Reject lines whose text reads
# as a promo, and prefer lines that match the merchant name.
_PROMO_TERMS = frozenset(
    {
        "OFF",
        "SAVE",
        "SAVINGS",
        "SAVING",
        "COUPON",
        "REWARD",
        "REWARDS",
        "HOT",
        "DEAL",
        "DEALS",
        "FREE",
        "BUY",
        "GET",
        "SCAN",
        "BONUS",
        "EARN",
        "SALE",
        "EXTRABUCKS",
        "EXTRACARE",
        "POINTS",
        "MEMBER",
        "VALUE",
        "PROMO",
        "%",
    }
)
_COMPACT_PROMO_PHRASES = frozenset(
    {
        "MEMBERSAVINGS",
        "MEMBERREWARDS",
        "HOTDEAL",
        "HOTDEALS",
        "BUYGET",
        "BUYGETFREE",
        "SCANCOUPON",
        "SAVEBIG",
        "SAVEMORE",
        "YOUSAVED",
    }
)

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

# Horizontal pad for body glyph crops. The OCR letter box is sometimes a pixel
# tight on the round side of a capital (D / O / G), clipping the right bowl so it
# reads open (D->C). A hair of pad recovers that edge; kept tiny (0.02) so it
# pulls in at most a 1-2px neighbour sliver, which the area-aware edge-sliver
# trim then removes. Larger pads (0.04+) drag in ink-heavy neighbour fragments
# that survive the trim and stamp as raised specks, so this stays small.
_BODY_GLYPH_EXPAND_X = 0.02

# An edge column-run is trimmed as a neighbour sliver only if it carries at most
# this share of the glyph's ink (in addition to being narrow). A detached letter
# bowl exceeds this and is kept; a thin neighbour fragment falls under it.
_SLIVER_MAX_INK_FRAC = 0.18


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

    def glyph_for(self, char: str, *, index: int = 0) -> GlyphCrop | None:
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
    trim_edge_slivers: bool = True,
) -> Image.Image | None:
    """Crop one letterform from the raw receipt as ink-on-transparent RGBA.

    Crops tight to the OCR letter box (no horizontal neighbor pad), then converts
    to alpha where alpha encodes ink darkness (paper -> transparent) and tightens
    to the ink bbox so the renderer can place it by content. The antialiased ink
    edge is preserved. ``trim_edge_slivers`` drops a thin detached neighbour-glyph
    sliver at the left/right edge (see :func:`_trim_edge_slivers`). Returns
    ``None`` when the crop has no usable ink.
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

    if trim_edge_slivers:
        alpha = _trim_edge_slivers(alpha)
        if alpha is None:
            return None

    glyph = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    glyph.putalpha(alpha)
    return glyph


def _trim_edge_slivers(alpha: Image.Image) -> Image.Image | None:
    """Drop thin detached ink columns at the left/right edge of a glyph crop.

    OCR letter boxes can overlap a neighbour by a pixel or two, leaving a thin
    vertical sliver of the next glyph in the crop; stamped, those read as stray
    strokes between letters. The crop is split into runs of ink-bearing columns
    separated by blank columns; a *leading or trailing* run that is both much
    narrower than the widest run AND carries only a sliver of the total ink is a
    neighbour fragment and is trimmed. The ink-area guard is what keeps a round
    letter's *detached* right bowl (D / O / G, whose top/bottom join faded on
    thermal print): the bowl is a narrow column run but a substantial share of the
    glyph's ink, so it survives where a 1-2px neighbour edge does not. Interior
    structure is left alone, and characters whose parts split only *vertically*
    (``i``, ``:``, ``%``, ``=``) are a single column-run, so they are untouched.
    """
    width, height = alpha.size
    if width < 3:
        return alpha
    px = alpha.load()
    col_count = [
        sum(1 for y in range(height) if px[x, y] >= _MIN_INK_ALPHA)
        for x in range(width)
    ]
    col_ink = [c > 0 for c in col_count]
    runs: list[tuple[int, int]] = []
    start = None
    for x, has in enumerate(col_ink):
        if has and start is None:
            start = x
        elif not has and start is not None:
            runs.append((start, x - 1))
            start = None
    if start is not None:
        runs.append((start, width - 1))
    if len(runs) <= 1:
        return alpha

    widest = max(run[1] - run[0] + 1 for run in runs)
    sliver_max = max(2, int(widest * 0.30))
    total_ink = sum(col_count) or 1

    def is_sliver(run: tuple[int, int]) -> bool:
        # Narrow AND ink-light: a neighbour fragment is a few columns carrying a
        # tiny share of the ink. A detached letter bowl is narrow too but ink-
        # heavy, so the area guard spares it.
        narrow = (run[1] - run[0] + 1) <= sliver_max
        light = (
            sum(col_count[run[0] : run[1] + 1])
            <= total_ink * _SLIVER_MAX_INK_FRAC
        )
        return narrow and light

    keep = list(runs)
    while len(keep) > 1 and is_sliver(keep[0]):
        keep.pop(0)
    while len(keep) > 1 and is_sliver(keep[-1]):
        keep.pop()
    left = keep[0][0]
    right = keep[-1][1]
    if left == 0 and right == width - 1:
        return alpha
    return alpha.crop((left, 0, right + 1, height))


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
    # (merchant_match_score, line_height, image, text) for each display line that
    # is NOT a promo/coupon banner — the wordmark is chosen from these afterwards.
    logo_candidates: list[tuple[float, float, "Image.Image", str]] = []
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

        body_height = _median(
            [s.metrics.get("box_height", 0.0) for s in samples]
        )
        body_width = _median(
            [s.metrics.get("box_width", 0.0) for s in samples]
        )
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

        # Collect display lines as logo candidates, rejecting promo/coupon banners
        # (often the tallest text on the receipt — e.g. Sprouts "$5 OFF $30").
        for line_key, tier in line_tier.items():
            if tier != "logo":
                continue
            line_samples = line_groups[line_key]
            lh = _median(
                [s.metrics.get("box_height", 0.0) for s in line_samples]
            )
            captured = _capture_logo(line_samples, raw_image, image_y_origin)
            if captured is None:
                continue
            image_crop, text = captured
            score = _logo_match_score(text, merchant_name)
            if _is_promo_text(text) and score < 0.75:
                continue
            logo_candidates.append((score, lh, image_crop, text))

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
                raw_image,
                box,
                y_origin=image_y_origin,
                expand_x_ratio=_BODY_GLYPH_EXPAND_X,
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

    # Choose the wordmark: highest merchant-name match, tie-broken by height. A
    # promo-free tall line wins even if its OCR text is garbled (score 0).
    logo, logo_text = (None, None)
    if logo_candidates:
        score, _, logo, logo_text = max(
            logo_candidates, key=lambda c: (c[0], c[1])
        )
        if score == 0.0:
            notes.append(
                "logo wordmark chosen by height only (no merchant-name match)"
            )

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
    from receipt_upload.font_analysis import load_raw_image_from_s3

    from receipt_dynamo.data.dynamo_client import DynamoClient

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
    with open(
        os.path.join(directory, "atlas.json"), encoding="utf-8"
    ) as handle:
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
        logo = Image.open(os.path.join(directory, index["logo_file"])).convert(
            "RGBA"
        )
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


def _dominant_logo_band(
    pairs: Sequence[tuple[LetterImageSample, dict[str, float]]],
) -> list[tuple[LetterImageSample, dict[str, float]]]:
    """Keep only the dominant (tallest) vertically-stacked band of a logo line.

    OCR groups a stacked wordmark + subtitle (e.g. Sprouts "SPROUTS" over a much
    smaller "FARMERS MARKET") into one ``line_id``. Cropping their union bakes the
    faint subtitle into the logo bitmap, which scales down into an illegible grey
    smudge when stamped. We split the letters into non-overlapping vertical bands
    and keep only the tallest one (the real wordmark). A normal single-row
    wordmark — including mixed-case marks whose caps and x-height letters share a
    baseline and therefore overlap vertically — collapses to a single band and is
    returned unchanged, so existing behaviour is preserved.
    """
    if len(pairs) <= 1:
        return list(pairs)
    ordered = sorted(pairs, key=lambda p: p[1]["y"])
    bands: list[dict[str, Any]] = []
    for sample, box in ordered:
        y0 = box["y"]
        y1 = box["y"] + box["height"]
        for band in bands:
            if min(y1, band["y1"]) - max(y0, band["y0"]) > 0:
                band["items"].append((sample, box))
                band["y0"] = min(band["y0"], y0)
                band["y1"] = max(band["y1"], y1)
                break
        else:
            bands.append({"y0": y0, "y1": y1, "items": [(sample, box)]})
    if len(bands) <= 1:
        return list(pairs)
    best = max(
        bands,
        key=lambda b: (
            _median([box["height"] for _, box in b["items"]]),
            len(b["items"]),
        ),
    )
    return list(best["items"])


def _capture_logo(
    line_samples: Sequence[LetterImageSample],
    raw_image: Image.Image,
    y_origin: str,
) -> tuple[Image.Image, str] | None:
    """Crop the union box of a display line as the logo image + its text."""
    pairs = [(s, _box_from_metrics(s.metrics)) for s in line_samples]
    pairs = [(s, b) for (s, b) in pairs if b is not None]
    if not pairs:
        return None
    # Stacked wordmark+subtitle logos (Sprouts) get cropped to the dominant
    # band only, dropping the faint subtitle that would otherwise smudge.
    pairs = _dominant_logo_band(pairs)
    band_samples = [s for (s, _) in pairs]
    boxes = [b for (_, b) in pairs]
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
        expand_x_ratio=0.06,
        expand_y_ratio=0.12,
        # A wordmark is a multi-letter graphic, NOT a single glyph: the
        # edge-sliver trim (meant to drop a neighbour-bleed column) was
        # clipping the leading/trailing letter (SPROUTS -> PROUT, the heart
        # in CVS). Keep the full mark.
        trim_edge_slivers=False,
    )
    if image is None:
        return None
    text = _line_text_from_samples(band_samples)
    return image, text


def _line_text_from_samples(line_samples: Sequence[LetterImageSample]) -> str:
    ordered = sorted(
        line_samples, key=lambda s: s.metrics.get("box_center_x", 0.0)
    )
    word_ids = {s.word_id for s in ordered if s.word_id >= 0}
    if len(word_ids) <= 1:
        return "".join(s.text for s in ordered)

    grouped: dict[int, list[LetterImageSample]] = defaultdict(list)
    for sample in ordered:
        grouped[sample.word_id].append(sample)

    words: list[str] = []
    for _, samples in sorted(
        grouped.items(),
        key=lambda item: min(
            s.metrics.get("box_center_x", 0.0) for s in item[1]
        ),
    ):
        letters = sorted(
            samples,
            key=lambda s: (
                s.letter_id if s.letter_id >= 0 else 1_000_000,
                s.metrics.get("box_center_x", 0.0),
            ),
        )
        words.append("".join(s.text for s in letters))
    return " ".join(word for word in words if word)


def _is_promo_text(text: str) -> bool:
    """Return True when a tall display line reads like an offer, not a logo."""
    upper = str(text).upper()
    tokens = set(re.findall(r"[A-Z]+", upper))
    compact = _merchant_match_text(upper)
    return (
        "$" in upper
        or "%" in upper
        or bool(tokens & _PROMO_TERMS)
        or any(phrase in compact for phrase in _COMPACT_PROMO_PHRASES)
    )


def _logo_match_score(text: str, merchant_name: str) -> float:
    """Score how strongly a captured display line matches the merchant name."""
    logo = _merchant_match_text(text)
    merchant = _merchant_match_text(merchant_name)
    if not logo or not merchant:
        return 0.0
    if logo == merchant:
        return 1.0

    logo_tokens = _merchant_tokens(text)
    merchant_tokens = _merchant_tokens(merchant_name)
    if logo_tokens and merchant_tokens:
        if merchant_tokens[: len(logo_tokens)] == logo_tokens:
            if len(logo_tokens) >= 2 or len(merchant_tokens) == 1:
                return 1.0

        merchant_set = set(merchant_tokens)
        matches = [token for token in logo_tokens if token in merchant_set]
        if matches:
            if len(logo_tokens) == 1 and len(merchant_tokens) > 1:
                merchant_chars = sum(len(token) for token in merchant_tokens)
                return len(matches[0]) / max(merchant_chars, 1)
            token_coverage = len(matches) / max(len(logo_tokens), 1)
            matched = sum(len(token) for token in matches)
            logo_chars = sum(len(token) for token in logo_tokens)
            char_coverage = matched / max(logo_chars, 1)
            return token_coverage * char_coverage

    if logo in merchant or merchant in logo:
        return min(len(logo), len(merchant)) / max(len(logo), len(merchant))

    matches = [token for token in merchant_tokens if token and token in logo]
    if not matches:
        return 0.0
    matched = sum(len(token) for token in matches)
    return matched / max(len(merchant), 1)


def _merchant_match_text(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(text).upper())


def _merchant_tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Z0-9]+", str(text).upper())
        if len(token) >= 3
    ]


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
            cluster_label=_most_common(style_labels.get(style_id, ()))
            or "n/a",
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
    target_h = (
        target_height
        if target_height > 0
        else (median(c.box_height_norm for c in crops) if crops else 0.0)
    )

    median_ink = median(c.ink_ratio for c in crops) if crops else 0.0

    def aspect_dev(crop: GlyphCrop) -> float:
        return abs(crop.aspect - median_aspect) / max(median_aspect, 1e-6)

    def rank(crop: GlyphCrop) -> tuple[float, float]:
        height_dev = abs(crop.box_height_norm - target_h) / max(target_h, 1e-6)
        return (aspect_dev(crop), height_dev)

    ordered = sorted(crops, key=rank)
    # Drop strong outliers — a doubled-strike / broken / neighbour-bled crop has a
    # markedly different aspect or much heavier ink than the char's norm, and if
    # it became a stored variant it would surface via variant rotation. Keep the
    # consistent crops (always at least the single best one).
    if len(ordered) > 2:
        kept = [
            c
            for c in ordered
            if aspect_dev(c) <= 0.5
            and (median_ink <= 0 or c.ink_ratio <= median_ink * 1.6)
        ]
        ordered = kept or ordered[:1]
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

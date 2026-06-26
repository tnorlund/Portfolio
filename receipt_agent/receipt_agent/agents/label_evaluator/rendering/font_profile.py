"""Per-merchant font/geometry profiles built on #994's font analyzer.

A :class:`MerchantFontProfile` captures the *typography geometry* of a
merchant's real receipts — character width, font height, line pitch, and the
price column — in resolution-independent normalized (0-1) units. Two consumers
use it:

* the receipt renderer (``receipt_renderer.py``), to draw a synthesized receipt
  at the merchant's real typography, and
* the synthesis spacing helpers in ``merchant_synthesis.py``, which can scale
  the profile into the 0-1000 synthesis coordinate space via
  :meth:`MerchantFontProfile.to_geometry_params`.

The profile is *data*. It feeds geometry parameters only; it must never relax a
deterministic structure-similarity or arithmetic gate (see CHARTER.md).

Coordinate conventions
----------------------
Real OCR boxes are normalized to ``[0, 1]`` with a bottom-left origin, so a
larger ``y`` is higher on the receipt (``image_y_origin="bottom_left"``). All
profile fields are stored in that normalized space.  ``to_geometry_params``
multiplies by 1000 to land in the synthesis pixel space
(``normalized_receipt_0_1000_y_high_is_top``).
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Mapping, Sequence


def _ensure_receipt_upload_on_path() -> None:
    """Make the sibling ``receipt_upload`` package importable.

    ``receipt_upload`` (which holds #994's font analyzer) is not pip-installed in
    the ``receipt_agent`` environment, so add the repo's ``receipt_upload``
    directory to ``sys.path`` — mirroring ``utils/combination_selector.py``.
    No-op once the package imports cleanly.
    """
    try:  # pragma: no cover - fast path when already importable
        import receipt_upload.font_analysis  # noqa: F401

        return
    except ImportError:
        pass
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), *([os.pardir] * 5))
    )
    candidate = os.path.join(repo_root, "receipt_upload")
    if candidate not in sys.path:
        sys.path.insert(0, candidate)
    # ``receipt_upload`` may already be cached as a namespace package resolved to
    # the repo's outer ``receipt_upload`` dir (which has no ``font_analysis``
    # submodule). Drop that cache so the real package on ``candidate`` resolves.
    cached = sys.modules.get("receipt_upload")
    if cached is not None and getattr(cached, "__file__", None) is None:
        del sys.modules["receipt_upload"]


_ensure_receipt_upload_on_path()

from receipt_upload.font_analysis import (  # noqa: E402
    FontCluster,
    ReceiptFontAnalysis,
    analyze_receipt_fonts,
)

_EPSILON = 1e-9

# A price/amount token: optional currency sign, digits with a 2-decimal tail,
# optional thousands separators, and an optional trailing single-letter tax flag
# (e.g. "1.99", "$12.00", "1,299.00", "3.49 T"). Receipts right-align these in a
# dedicated column, which is what we want to measure.
_PRICE_TOKEN = re.compile(r"^\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})\$?[A-Z]?$")


@dataclass(frozen=True)
class ReceiptFontProfile:
    """Font/geometry summary for one real receipt."""

    image_id: str | None
    receipt_id: int | None
    sample_count: int
    font_height: float
    char_width: float
    char_aspect: float
    line_pitch: float | None
    price_column_x: float | None
    dominant_style_label: str
    glyph_width_cv: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "receipt_id": self.receipt_id,
            "sample_count": self.sample_count,
            "font_height": round(self.font_height, 6),
            "char_width": round(self.char_width, 6),
            "char_aspect": round(self.char_aspect, 6),
            "line_pitch": (
                None if self.line_pitch is None else round(self.line_pitch, 6)
            ),
            "price_column_x": (
                None
                if self.price_column_x is None
                else round(self.price_column_x, 6)
            ),
            "dominant_style_label": self.dominant_style_label,
            "glyph_width_cv": round(self.glyph_width_cv, 6),
        }


@dataclass(frozen=True)
class MerchantFontProfile:
    """Aggregated font/geometry profile for a merchant.

    All scalar geometry fields are medians over the contributing receipts and
    are in normalized (0-1) receipt units.
    """

    merchant_name: str
    receipt_count: int
    font_height: float
    char_width: float
    char_aspect: float
    line_pitch: float | None
    price_column_x: float | None
    dominant_style_label: str
    style_label_counts: dict[str, int] = field(default_factory=dict)
    source_image_ids: tuple[str, ...] = ()

    def to_geometry_params(self, *, canvas: int = 1000) -> dict[str, float]:
        """Scale the normalized profile into synthesis pixel space.

        Returns the spacing parameters the ``merchant_synthesis`` helpers and the
        renderer consume, in the ``0..canvas`` coordinate space (default 1000).
        ``line_step_px`` / ``price_column_x_px`` are ``None`` when the profile
        could not observe them.
        """
        return {
            "font_height_px": self.font_height * canvas,
            "char_width_px": self.char_width * canvas,
            "char_aspect": self.char_aspect,
            "line_step_px": (
                None
                if self.line_pitch is None
                else self.line_pitch * canvas
            ),
            "price_column_x_px": (
                None
                if self.price_column_x is None
                else self.price_column_x * canvas
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "merchant_name": self.merchant_name,
            "receipt_count": self.receipt_count,
            "font_height": round(self.font_height, 6),
            "char_width": round(self.char_width, 6),
            "char_aspect": round(self.char_aspect, 6),
            "line_pitch": (
                None if self.line_pitch is None else round(self.line_pitch, 6)
            ),
            "price_column_x": (
                None
                if self.price_column_x is None
                else round(self.price_column_x, 6)
            ),
            "dominant_style_label": self.dominant_style_label,
            "style_label_counts": dict(self.style_label_counts),
            "source_image_ids": list(self.source_image_ids),
        }


def extract_receipt_font_profile(
    words: Sequence[Any],
    lines: Sequence[Any] | None = None,
    *,
    letters: Sequence[Any] | None = None,
    analysis: ReceiptFontAnalysis | None = None,
    image_y_origin: str = "bottom_left",
    **analysis_kwargs: Any,
) -> ReceiptFontProfile | None:
    """Summarize one receipt's typography geometry from OCR entities.

    Typography (``font_height`` / ``char_width`` / ``char_aspect``) is read from
    the dominant #994 font cluster — the merchant's main body-text style — when a
    clustering is available (or can be computed from ``letters``); otherwise it
    falls back to robust medians over the words themselves. ``line_pitch`` and
    ``price_column_x`` are always measured from word/line positions because they
    are layout, not glyph, properties.

    Returns ``None`` when there is not enough geometry to summarize.
    """
    word_metrics = [
        metric for metric in (_word_metrics(word) for word in words) if metric
    ]
    if not word_metrics:
        return None

    if analysis is None and letters:
        analysis = analyze_receipt_fonts(
            letters,
            words=words,
            lines=lines,
            image_y_origin=image_y_origin,
            **analysis_kwargs,
        )

    dominant = _dominant_cluster(analysis) if analysis is not None else None

    if dominant is not None and dominant.metrics.get("height", 0.0) > _EPSILON:
        font_height = float(dominant.metrics["height"])
        char_width = float(
            dominant.metrics.get("width_per_char")
            or _median_or_zero([m["width_per_char"] for m in word_metrics])
        )
        glyph_width_cv = float(dominant.metrics.get("glyph_width_cv", 0.0))
        style_label = dominant.label
    else:
        font_height = _median_or_zero([m["height"] for m in word_metrics])
        char_width = _median_or_zero(
            [m["width_per_char"] for m in word_metrics]
        )
        glyph_width_cv = 0.0
        style_label = "unknown"

    if font_height <= _EPSILON or char_width <= _EPSILON:
        return None

    char_aspect = char_width / font_height
    line_pitch = _line_pitch(words, lines)
    price_column_x = _price_column_x(word_metrics)

    first = word_metrics[0]
    return ReceiptFontProfile(
        image_id=first["image_id"],
        receipt_id=first["receipt_id"],
        sample_count=len(word_metrics),
        font_height=font_height,
        char_width=char_width,
        char_aspect=char_aspect,
        line_pitch=line_pitch,
        price_column_x=price_column_x,
        dominant_style_label=style_label,
        glyph_width_cv=glyph_width_cv,
    )


def build_merchant_font_profile(
    merchant_name: str,
    receipt_profiles: Sequence[ReceiptFontProfile],
) -> MerchantFontProfile | None:
    """Aggregate per-receipt profiles into one merchant profile (medians)."""
    profiles = [profile for profile in receipt_profiles if profile is not None]
    if not profiles:
        return None

    label_counts = Counter(
        profile.dominant_style_label
        for profile in profiles
        if profile.dominant_style_label
        and profile.dominant_style_label != "unknown"
    )
    dominant_label = (
        label_counts.most_common(1)[0][0] if label_counts else "unknown"
    )

    line_pitches = [
        profile.line_pitch
        for profile in profiles
        if profile.line_pitch is not None
    ]
    price_columns = [
        profile.price_column_x
        for profile in profiles
        if profile.price_column_x is not None
    ]
    image_ids = tuple(
        dict.fromkeys(
            profile.image_id for profile in profiles if profile.image_id
        )
    )

    return MerchantFontProfile(
        merchant_name=merchant_name,
        receipt_count=len(profiles),
        font_height=median(profile.font_height for profile in profiles),
        char_width=median(profile.char_width for profile in profiles),
        char_aspect=median(profile.char_aspect for profile in profiles),
        line_pitch=median(line_pitches) if line_pitches else None,
        price_column_x=median(price_columns) if price_columns else None,
        dominant_style_label=dominant_label,
        style_label_counts=dict(label_counts),
        source_image_ids=image_ids,
    )


def build_merchant_font_profile_from_dynamo(
    table_name: str,
    merchant_name: str,
    *,
    region: str = "us-east-1",
    max_receipts: int | None = None,
    eps: float = 3.0,
    min_samples: int = 2,
    min_letters_per_sample: int = 2,
    use_raw_image: bool = False,
    s3_client: Any | None = None,
) -> MerchantFontProfile | None:
    """Build a merchant profile from real receipts in Dynamo/S3.

    Resolves the merchant's receipts via :class:`ReceiptPlace`
    (``get_receipt_places_by_merchant`` — ``ReceiptMetadata`` is deprecated),
    loads each receipt's OCR lines/words/letters, and builds a per-receipt
    profile with :func:`extract_receipt_font_profile` (which runs #994's
    clustering on the letters). Profiles are then aggregated.

    ``eps=3.0`` matches the receipt-level recommendation from the font pilot
    (``docs/receipt-font-analysis-pilot.md``). When ``use_raw_image`` is set the
    raw receipt crop is downloaded from S3 so #994's pixel features sharpen the
    clustering; it is off by default because the box geometry that drives the
    profile (height/width/pitch/price column) does not need pixels and skipping
    the download keeps the pass fast. Receipts that fail to load are skipped so
    one bad image does not sink the merchant profile.
    """
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_upload.font_analysis import load_raw_image_from_s3

    client = DynamoClient(table_name=table_name, region=region)
    places, _ = client.get_receipt_places_by_merchant(merchant_name)

    # De-duplicate to one analysis per (image, receipt); a merchant can have many.
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

    receipt_profiles: list[ReceiptFontProfile] = []
    for image_id, receipt_id in targets:
        try:
            details = client.get_image_details(image_id)
            lines = [
                line
                for line in details.receipt_lines
                if line.receipt_id == receipt_id
            ]
            words = [
                word
                for word in details.receipt_words
                if word.receipt_id == receipt_id
            ]
            letters = [
                letter
                for letter in details.receipt_letters
                if letter.receipt_id == receipt_id
            ]
            raw_image = None
            if use_raw_image:
                receipt = next(
                    (
                        candidate
                        for candidate in details.receipts
                        if candidate.receipt_id == receipt_id
                    ),
                    None,
                )
                if receipt is not None:
                    raw_image = load_raw_image_from_s3(
                        receipt, s3_client=s3_client
                    )
            profile = extract_receipt_font_profile(
                words,
                lines,
                letters=letters,
                raw_image=raw_image,
                eps=eps,
                min_samples=min_samples,
                min_letters_per_sample=min_letters_per_sample,
            )
        except Exception:  # noqa: BLE001 - one bad receipt must not abort
            continue
        if profile is not None:
            receipt_profiles.append(profile)

    return build_merchant_font_profile(merchant_name, receipt_profiles)


def _dominant_cluster(
    analysis: ReceiptFontAnalysis | None,
) -> FontCluster | None:
    """The largest font-style cluster — the merchant's main body-text font."""
    if analysis is None or not analysis.clusters:
        return None
    return max(analysis.clusters, key=lambda cluster: cluster.letter_count)


def _word_metrics(word: Any) -> dict[str, Any] | None:
    box = _box(word)
    text = str(_get(word, "text", "") or "").strip()
    if box is None or not text:
        return None
    visible_chars = sum(1 for char in text if not char.isspace())
    if visible_chars == 0:
        return None
    width = max(box["width"], _EPSILON)
    height = max(box["height"], _EPSILON)
    return {
        "image_id": _get(word, "image_id"),
        "receipt_id": _get(word, "receipt_id"),
        "text": text,
        "x": box["x"],
        "y": box["y"],
        "width": width,
        "height": height,
        "center_x": box["x"] + width / 2.0,
        "right_x": box["x"] + width,
        "center_y": box["y"] + height / 2.0,
        "width_per_char": width / visible_chars,
    }


def _line_pitch(
    words: Sequence[Any], lines: Sequence[Any] | None
) -> float | None:
    """Median center-to-center vertical spacing between adjacent text rows.

    Uses explicit line boxes when present; otherwise groups words by
    ``line_id``. Rows are ordered top→bottom (y-high-is-top), so consecutive
    center gaps are the row pitch. Returns ``None`` with fewer than two rows.
    """
    centers: list[float] = []
    if lines:
        for line in lines:
            box = _box(line)
            if box is not None:
                centers.append(box["y"] + max(box["height"], _EPSILON) / 2.0)
    if len(centers) < 2:
        grouped: dict[Any, list[float]] = {}
        for word in words:
            box = _box(word)
            line_id = _get(word, "line_id")
            if box is None or line_id is None:
                continue
            grouped.setdefault(line_id, []).append(
                box["y"] + max(box["height"], _EPSILON) / 2.0
            )
        centers = [median(values) for values in grouped.values()]
    if len(centers) < 2:
        return None
    ordered = sorted(centers, reverse=True)  # top first
    gaps = [
        abs(ordered[i] - ordered[i + 1]) for i in range(len(ordered) - 1)
    ]
    gaps = [gap for gap in gaps if gap > _EPSILON]
    if not gaps:
        return None
    return median(gaps)


def _price_column_x(
    word_metrics: Sequence[Mapping[str, Any]],
    *,
    align_tolerance: float = 0.04,
    min_support: int = 2,
) -> float | None:
    """Center-x of the line-total column, or ``None`` when it can't be proven.

    A receipt can have several amount-like columns (weight, unit price, line
    total). Taking a plain median of every price token would land *between*
    columns. Instead, cluster price tokens by their right edge (line totals are
    right-aligned), pick the rightmost cluster that is both well aligned (its
    right edges fall within ``align_tolerance``) and adequately supported
    (``min_support`` tokens), and return that cluster's median center-x. Returns
    ``None`` if no column has enough aligned evidence — we never guess.
    """
    prices = [
        metric
        for metric in word_metrics
        if _PRICE_TOKEN.match(str(metric["text"]).replace(" ", ""))
    ]
    if not prices:
        return None

    # Greedily group by right edge, scanning right→left. A token joins the open
    # cluster while its right edge stays within tolerance of the cluster anchor.
    ordered = sorted(prices, key=lambda m: float(m["right_x"]), reverse=True)
    clusters: list[list[Mapping[str, Any]]] = []
    for metric in ordered:
        right_x = float(metric["right_x"])
        if clusters and (
            abs(float(clusters[-1][0]["right_x"]) - right_x)
            <= align_tolerance
        ):
            clusters[-1].append(metric)
        else:
            clusters.append([metric])

    # Clusters are already ordered rightmost-first; take the first that clears
    # the support bar (i.e. the rightmost real column).
    for cluster in clusters:
        if len(cluster) >= min_support:
            return median(float(metric["center_x"]) for metric in cluster)

    # No column meets the support bar. A lone price still anchors a column, so
    # fall back to the single rightmost token rather than discarding it.
    if len(prices) == 1:
        return float(prices[0]["center_x"])
    return None


def _median_or_zero(values: Sequence[float]) -> float:
    cleaned = [float(value) for value in values if value is not None]
    if not cleaned:
        return 0.0
    return float(median(cleaned))


def _box(value: Any) -> dict[str, float] | None:
    """Read a normalized bounding box from a Dynamo entity or mapping.

    Supports both the ``bounding_box`` dict form and the four-corner form, like
    ``font_analysis._box`` does.
    """
    if value is None:
        return None
    bounding_box = _get(value, "bounding_box")
    if isinstance(bounding_box, Mapping):
        return {
            "x": _float(bounding_box.get("x")),
            "y": _float(bounding_box.get("y")),
            "width": max(_float(bounding_box.get("width")), _EPSILON),
            "height": max(_float(bounding_box.get("height")), _EPSILON),
        }
    corners = [
        _get(value, "top_left"),
        _get(value, "top_right"),
        _get(value, "bottom_left"),
        _get(value, "bottom_right"),
    ]
    points = [
        (_float(point.get("x")), _float(point.get("y")))
        for point in corners
        if isinstance(point, Mapping)
    ]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x = min(xs)
    min_y = min(ys)
    return {
        "x": min_x,
        "y": min_y,
        "width": max(max(xs) - min_x, _EPSILON),
        "height": max(max(ys) - min_y, _EPSILON),
    }


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

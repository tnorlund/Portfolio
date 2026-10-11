"""Evidence-only detection of amounts lost by a receipt's second OCR pass.

Never synthesizes an item, amount or word. A caller may use the single,
spatially attributable observation to request a bounded re-read of the image.
Ambiguous ownership, conflicting readings and multiple explanations abstain.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Sequence

from receipt_dynamo.amounts import (
    NON_PAYMENT_SUMMARY_RE,
    SUBTOTAL_KEYWORD_RE,
    TAX_KEYWORD_RE,
    TOTAL_KEYWORD_RE,
    parse_receipt_amount,
)
from receipt_dynamo.entities.receipt_summary import _is_currency_prefix_token

from receipt_upload.geometry.transformations import find_perspective_coeffs
from receipt_upload.line_items.geometry import (
    _SLASH_UNIT_WORD,
    COLUMN_HEADER_ANCHOR_TOKENS,
    DISCOUNT_WORD_RE,
    MAX_PLAUSIBLE_BASELINE,
    NON_PRODUCT_NOTE_RE,
    PRICE_COLUMN_X_TOL,
    SALE_PRICE_RE,
    WAS_PRICE_RE,
    estimate_skew,
    is_column_header_row,
    is_line_price_word,
    is_settlement_row,
    is_unit_rate_row,
    strip_tax_flag,
)

_CHARGE_OR_SAVINGS_RE = re.compile(
    r"\b(?:taxes|tips?|gratuit(?:y|ies)|service\s+charges?|save[ds]?)\b", re.I
)

_MINUS_TRANSLATION = str.maketrans({char: "-" for char in "−‒–—﹣－‐‑"})

_REFERENCE_CONTEXT_RE = re.compile(r"\b(?:MSRP|LIST|RETAIL)\b", re.I)

_TAX_ABBREVIATION_RE = re.compile(
    r"\b(?:GST|HST|PST|QST|TVA|IVA|MWST)\b", re.I
)
_MEASUREMENT_CONTEXT_RE = re.compile(
    r"\b(?:weight|wt|tare|volume|vol|length|width|height|"
    r"oz|ounces?|lb|lbs|pounds?|kg|kilograms?|g|grams?|"
    r"gal|gallons?|ml|liters?|litres?|cm|mm|inches?)\b|\bunit\s+cost\b",
    re.I,
)

_UNIT_CONTEXT_RE = re.compile(
    rf"(?<![A-Za-z]){_SLASH_UNIT_WORD}(?![A-Za-z])", re.I
)

_RATE_CONTEXT_RE = re.compile(
    r"[×@]|\(\s*a\s*\)|\bper\b|\bunit\s+(?:price|cost)\b|"
    r"\b\d+\s*x\b|\b\d+\s+for\b|\b\d+\s*/",
    re.I,
)

_SIGN_CONTEXT_RE = re.compile(r"[-−–]|(?:^|\s)\(")

_CURRENCY_AMOUNT_RE = re.compile(
    r"(?:[$€£¥₹]\s*\d[\d.,]*|\d[\d.,]*\s*[$€£¥₹])"
)

_UNQUALIFIED_AMOUNT_RE = re.compile(
    r"[+-]?(?:[$€£¥₹]?\d[\d.,]*[$€£¥₹]?|"
    r"\([$€£¥₹]?\d[\d.,]*[$€£¥₹]?\))[+-]?"
)


@dataclass(frozen=True)
class LostAmountEvidence:
    """An existing source OCR observation, not a corrected receipt item."""

    source_line_id: int
    source_word_id: int
    text: str
    amount: float
    x: float
    y: float
    width: float
    height: float


def _cents(text: str) -> int | None:
    # OCR/printers use several Unicode dashes for a monetary minus. Preserve
    # their sign when counting ambiguity, rather than dropping the token.
    text = text.translate(_MINUS_TRANSLATION)
    if not is_line_price_word({"text": text}) and not re.search(
        r"[$€£¥₹]\s*[+-]?\s*\d|\d\s*[$€£¥₹]", text
    ):
        return None
    amount = parse_receipt_amount(strip_tax_flag(text))
    if amount is None or not math.isfinite(amount):
        return None
    return round(amount * 100)


def _transform(
    bounds: dict[str, Any], *, to_source: bool = False
) -> list[float]:
    corners = [
        bounds[key]
        for key in ("top_left", "top_right", "bottom_right", "bottom_left")
    ]
    points = [(float(point["x"]), float(point["y"])) for point in corners]
    if not all(math.isfinite(v) and 0 <= v <= 1 for p in points for v in p):
        raise ValueError("receipt bounds must be finite normalized points")
    # TL, TR, BR, BL must be a convex clockwise quad in bottom-origin Vision
    # space. Reflected/top-origin or self-crossing bounds are not supported.
    for index in range(4):
        p, q, r = (points[(index + offset) % 4] for offset in range(3))
        cross = (q[0] - p[0]) * (r[1] - q[1]) - (q[1] - p[1]) * (r[0] - q[0])
        if cross >= -1e-10:
            raise ValueError("receipt bounds must be clockwise and convex")
    if not (
        points[0][1] + points[1][1] > points[2][1] + points[3][1]
        and points[0][0] + points[3][0] < points[1][0] + points[2][0]
    ):
        raise ValueError("receipt corner labels do not match Vision axes")
    unit = [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
    return (
        find_perspective_coeffs(points, unit)
        if to_source
        else find_perspective_coeffs(unit, points)
    )


def _project_box(
    box: dict[str, float], transform: list[float], require_inside: bool = True
) -> tuple[float, float, float, float] | None:
    """Map original-image geometry without clamping outside words inward."""
    a, b, c, d, e, f, g, h = transform
    x, y = float(box["x"]), float(box["y"])
    width, height = float(box["width"]), float(box["height"])
    if not all(math.isfinite(v) for v in (x, y, width, height)):
        return None
    if width <= 0 or height <= 0:
        return None
    points = []
    denominators = []
    for px, py in (
        (x, y),
        (x + width, y),
        (x, y + height),
        (x + width, y + height),
    ):
        denominator = 1 + g * px + h * py
        if abs(denominator) < 1e-10:
            return None
        denominators.append(denominator)
        qx = (a * px + b * py + c) / denominator
        qy = (d * px + e * py + f) / denominator
        if require_inside and not (0 <= qx <= 1 and 0 <= qy <= 1):
            return None
        points.append((qx, qy))
    # Across a projective horizon, transformed corners do not bound the
    # interior. Such a box cannot prove receipt ownership or coverage.
    if min(denominators) < 0 < max(denominators):
        return None
    left, right = min(p[0] for p in points), max(p[0] for p in points)
    bottom, top = min(p[1] for p in points), max(p[1] for p in points)
    return left, bottom, right - left, top - bottom


def _near(source: LostAmountEvidence, word: dict[str, Any]) -> bool:
    box = word["bounding_box"]
    return (
        abs(source.y + source.height / 2 - box["y"] - box["height"] / 2)
        <= max(source.height, box["height"]) * 0.5
        and abs(source.x + source.width / 2 - box["x"] - box["width"] / 2)
        <= max(source.width, box["width"]) / 2
    )


def _overlaps(source: LostAmountEvidence, word: dict[str, Any]) -> bool:
    box = word["bounding_box"]
    return min(source.x + source.width, box["x"] + box["width"]) > max(
        source.x, box["x"]
    ) and min(source.y + source.height, box["y"] + box["height"]) > max(
        source.y, box["y"]
    )


def _same_source_observation(
    left: LostAmountEvidence, right: LostAmountEvidence
) -> bool:
    return (
        left.amount == right.amount
        and abs(left.x - right.x) < min(left.width, right.width) * 0.25
        and abs(left.y - right.y) < min(left.height, right.height) * 0.25
    )


def _established_price_column(words: Sequence[dict[str, Any]]) -> float | None:
    """Require explicit currency observations on two distinct surviving rows."""
    anchors: list[tuple[float, float, float]] = []
    for word in words:
        text = strip_tax_flag(word["text"]).strip()
        confidence = word.get("confidence", 0)
        cents = _cents(word["text"])
        if (
            not _CURRENCY_AMOUNT_RE.fullmatch(text)
            or not is_line_price_word(word)
            or cents is None
            or cents <= 0
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0.95 <= confidence <= 1
        ):
            continue
        box = word["bounding_box"]
        x, y, width, height = (
            float(box[key]) for key in ("x", "y", "width", "height")
        )
        if (
            not all(math.isfinite(value) for value in (x, y, width, height))
            or min(x, y) < 0
            or min(width, height) <= 0
            or x + width > 1
            or y + height > 1
        ):
            continue
        anchors.append((x, y + height / 2, height))
    if not anchors:
        return None
    column = max(anchor[0] for anchor in anchors)
    aligned = [a for a in anchors if column - a[0] <= PRICE_COLUMN_X_TOL]
    if not any(
        abs(left[1] - right[1]) > max(left[2], right[2])
        for index, left in enumerate(aligned)
        for right in aligned[index + 1 :]
    ):
        return None
    return column


def _has_unpriced_name(
    evidence: LostAmountEvidence,
    zone_words: Sequence[dict[str, Any]],
    slope: float = 0.0,
) -> bool | None:
    """Return product context, or None when a summary-shaped row is unsure."""
    # Product names must stay within canonical band_words' 0.6 median-
    # height boundary. Preserve wider warning context so small qualifiers
    # or split signs cannot disappear when the positive window tightens.
    heights = sorted(
        [evidence.height] + [float(word["h"]) for word in zone_words]
    )
    row_gap = (heights[len(heights) // 2] or 0.01) * 0.6
    evidence_y = evidence.y + evidence.height / 2 - slope * evidence.x
    nearby_row = [
        word
        for word in zone_words
        if abs(word["y_mid"] - slope * word["x"] - evidence_y)
        <= max(float(word["h"]), evidence.height) * 0.75
    ]
    row = [
        word
        for word in nearby_row
        if abs(word["y_mid"] - slope * word["x"] - evidence_y) < row_gap
    ]
    flat_centers = [evidence_y] + [
        word["y_mid"] - slope * word["x"] for word in row
    ]
    if max(flat_centers) - min(flat_centers) >= row_gap:
        # Two nearby words on opposite sides must not bridge distinct bands.
        return None
    if any(
        float(word["x"]) > evidence.x + evidence.width / 2
        for word in nearby_row
    ):
        # A surviving suffix (e.g. OZ) can identify an embedded measurement,
        # even if its decimal happens to align with the amount column.
        return None
    if any(is_line_price_word(word) for word in nearby_row):
        # A surviving unit price does not prove the extended price survived.
        return None
    text = " ".join(
        word["text"] for word in sorted(nearby_row, key=lambda w: w["x"])
    )
    text = text.translate(_MINUS_TRANSLATION)
    bare = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z\s]", " ", text)).strip()
    # Summary vocabulary can appear in product names as well as in
    # department totals and settlement rows. Text alone cannot resolve that
    # ambiguity: retain the missing observation as a blocker, regardless of
    # prefixes or suffixes, rather than guessing a product/summary class.
    if any(
        pattern.search(bare)
        for pattern in (
            SUBTOTAL_KEYWORD_RE,
            TAX_KEYWORD_RE,
            TOTAL_KEYWORD_RE,
            _TAX_ABBREVIATION_RE,
        )
    ):
        return None
    # Currency wrappers are not product names, whether OCR emitted them as
    # a standalone word or alongside actual name words in one observation.
    name_text = " ".join(
        token
        for word in row
        if not _is_currency_prefix_token(word["text"])
        for token in word["text"].split()
        if not _is_currency_prefix_token(token)
    )
    return (
        bool(re.search(r"[A-Za-z]{2}", _UNIT_CONTEXT_RE.sub(" ", name_text)))
        and not is_settlement_row(bare)
        and not NON_PAYMENT_SUMMARY_RE.search(text)
        and not DISCOUNT_WORD_RE.search(text)
        and not _CHARGE_OR_SAVINGS_RE.search(text)
        and not _REFERENCE_CONTEXT_RE.search(text)
        and not NON_PRODUCT_NOTE_RE.search(text)
        and not is_column_header_row(text)
        and COLUMN_HEADER_ANCHOR_TOKENS.isdisjoint(
            re.findall(r"[a-z]{2,}", bare.lower())
        )
        and not _MEASUREMENT_CONTEXT_RE.search(bare)
        and not SALE_PRICE_RE.search(text)
        and not _RATE_CONTEXT_RE.search(text)
        and not _SIGN_CONTEXT_RE.search(text)
        # Treat a reference keyword as ambiguous regardless of how OCR
        # split or spelled the currency qualifier between it and the price.
        and not any(
            WAS_PRICE_RE.search(f"{token} {evidence.amount:.2f}")
            for token in re.findall(r"[A-Za-z]+", text)
        )
        and not re.fullmatch(
            r"(?:suggested\s+)?(?:tips?|gratuity|service\s+charge)"
            r"(?:\s+(?:total|amount|included))?",
            bare,
            re.IGNORECASE,
        )
        and not is_unit_rate_row(text, 0)
    )


def detect_warp_lost_amount(
    source_words: Sequence[dict[str, Any]],
    warped_words: Sequence[dict[str, Any]],
    zone_words: Sequence[dict[str, Any]],
    receipt_bounds: dict[str, Any],
    other_receipt_bounds: Sequence[dict[str, Any]],
    item_sum: float,
    printed_subtotal: float | None,
    *,
    source_image_id: str,
    receipt_image_id: str,
) -> LostAmountEvidence | None:
    """Find one missing source price that exactly explains a near shortfall.

    Both OCR inputs must come from the stated same image revision. Bounds
    use bottom-origin Vision coordinates, never a legacy top-origin crop.
    Word inputs carry text, bounding_box, confidence and original line/word
    IDs; zone_words use the decoder's x/y_mid/h shape. Bounds must include
    every other receipt on the image. A same-price item elsewhere is valid:
    coverage is spatial and one-to-one, not a global amount-membership test.
    Two surviving high-confidence currency amounts on distinct rows must
    establish the price column. Missing observations outside that column or
    without clear product context cause abstention. This intentionally handles
    only one missing positive amount, not subset sum search, tax corrections,
    missing/implausible baselines, or over-extraction.
    """
    if not source_image_id or source_image_id != receipt_image_id:
        return None
    if (
        printed_subtotal is None
        or not all(math.isfinite(v) for v in (item_sum, printed_subtotal))
        or not 0 < printed_subtotal <= MAX_PLAUSIBLE_BASELINE
    ):
        return None
    gap = round(printed_subtotal * 100) - round(item_sum * 100)
    baseline_cents = round(printed_subtotal * 100)
    match_tolerance_cents = max(2, baseline_cents * 0.01)
    near_tolerance_cents = max(100, baseline_cents * 0.10)
    if not match_tolerance_cents < gap <= near_tolerance_cents:
        return None
    try:
        target = _transform(receipt_bounds)
        others = [_transform(bounds) for bounds in other_receipt_bounds]
        crop_boxes = [word["bounding_box"] for word in warped_words]
        for word in zone_words:
            # The legacy zone shape omits width. Its conservative
            # envelope reaches the crop's right edge until a width is
            # supplied, so unknown extent cannot prove ownership.
            x, height = word["x"], word["h"]
            box = {
                "x": x,
                "y": word["y_mid"] - height / 2,
                "width": word.get("w", 1.0 - x),
                "height": height,
            }
            crop_boxes.append(box)
        identity = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
        source_boxes = [word["bounding_box"] for word in source_words]
        for box in crop_boxes + source_boxes:
            if (
                any(
                    isinstance(box[key], bool)
                    or not isinstance(box[key], (int, float))
                    for key in ("x", "y", "width", "height")
                )
                or _project_box(box, identity, require_inside=False) is None
            ):
                # Unknown geometry cannot establish coverage, receipt
                # ownership, or product context for a missing amount.
                return None
        if any(_project_box(box, identity) is None for box in crop_boxes):
            return None
        if others:
            to_source = _transform(receipt_bounds, to_source=True)
            # Surviving prices also support the inferred column, so they
            # require the same ownership proof as the product context.
            for box in crop_boxes:
                source_box = _project_box(box, to_source, require_inside=False)
                if source_box is None:
                    return None
                source_box_dict = dict(
                    zip(("x", "y", "width", "height"), source_box)
                )
                for other in others:
                    projection = _project_box(
                        source_box_dict, other, require_inside=False
                    )
                    if projection is None:
                        return None
                    ox, oy, ow, oh = projection
                    if ox < 1 and oy < 1 and ox + ow > 0 and oy + oh > 0:
                        return None
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    amounts = [
        word for word in warped_words if _cents(word["text"]) is not None
    ]
    price_column = _established_price_column(amounts)
    if price_column is None:
        return None
    source_seen: list[LostAmountEvidence] = []
    eligible_source: set[LostAmountEvidence] = set()
    source_context: list[dict[str, Any]] = []
    source_projected_words: list[dict[str, Any]] = []
    crop_slope = estimate_skew(
        [
            {
                "line_id": word["line_id"],
                "x": word["bounding_box"]["x"],
                "y_mid": word["bounding_box"]["y"]
                + word["bounding_box"]["height"] / 2,
            }
            for word in warped_words
        ]
    )
    source_groups: list[list[LostAmountEvidence]] = []
    group_matches: list[set[int]] = []
    group_overlaps: list[set[int]] = []
    for word in source_words:
        cents = _cents(word["text"])
        projected = _project_box(word["bounding_box"], target)
        if projected is None:
            unbounded = _project_box(
                word["bounding_box"], target, require_inside=False
            )
            if unbounded is None:
                return None
            x, y, width, height = unbounded
            if not (x < 1 and y < 1 and x + width > 0 and y + height > 0):
                continue
            if cents is not None:
                return None
            # A partially cropped qualifier still constrains its price.
            # Preserve its original projection; never clamp it into place.
            projected = unbounded
        box = word["bounding_box"]
        # Ownership applies to names and qualifiers as well as prices. A
        # foreign context word cannot establish a target receipt product.
        for other in others:
            other_box = _project_box(box, other, require_inside=False)
            if other_box is None:
                return None
            ox, oy, ow, oh = other_box
            if ox < 1 and oy < 1 and ox + ow > 0 and oy + oh > 0:
                return None
        context_word = {
            "line_id": word["line_id"],
            "text": word["text"],
            "x": projected[0],
            "y_mid": projected[1] + projected[3] / 2,
            "h": projected[3],
        }
        source_projected_words.append(context_word)
        if cents is None:
            source_context.append(context_word)
            continue
        confidence = word.get("confidence", 0)
        eligible = (
            not isinstance(confidence, bool)
            and isinstance(confidence, (int, float))
            and math.isfinite(confidence)
            and 0.95 <= confidence <= 1
            and is_line_price_word(word)
        )
        evidence = LostAmountEvidence(
            source_line_id=int(word["line_id"]),
            source_word_id=int(word["word_id"]),
            text=word["text"],
            amount=cents / 100,
            x=projected[0],
            y=projected[1],
            width=projected[2],
            height=projected[3],
        )
        if eligible:
            eligible_source.add(evidence)
        evidence_word = {
            "bounding_box": {
                "x": evidence.x,
                "y": evidence.y,
                "width": evidence.width,
                "height": evidence.height,
            }
        }
        if any(
            previous.amount != evidence.amount
            and (
                _near(previous, evidence_word)
                or _overlaps(previous, evidence_word)
            )
            for previous in source_seen
        ):
            return None
        nearby = [
            i
            for i, candidate in enumerate(amounts)
            if _near(evidence, candidate)
        ]
        # A shifted crop reading can narrowly miss the strict same-word
        # window while still occupying the source price. It is ambiguous,
        # never positive evidence that the amount disappeared.
        overlaps: set[int] = set()
        for i, candidate in enumerate(amounts):
            if i in nearby:
                continue
            if _overlaps(evidence, candidate):
                overlaps.add(i)
        matches = [i for i in nearby if _cents(amounts[i]["text"]) == cents]
        # Any conflicting crop reading defeats single-loss attribution,
        # even if this row also has a matching observation or another row
        # contains a missing amount that happens to explain the gap.
        if len(matches) != len(nearby) or len(matches) > 1:
            return None
        # Check every observation for crop conflicts before suppressing
        # duplicate source evidence, whose slightly shifted box may reveal
        # a conflicting crop reading hidden from the first observation.
        duplicate_groups: list[int] = []
        for index, group in enumerate(source_groups):
            same = [
                _same_source_observation(previous, evidence)
                for previous in group
            ]
            if any(same):
                # Proximity is not transitive. A bridge between distinct
                # observations makes occurrence counting ambiguous.
                if not all(same):
                    return None
                duplicate_groups.append(index)
        if len(duplicate_groups) > 1:
            return None
        source_seen.append(evidence)
        if duplicate_groups:
            index = duplicate_groups[0]
            source_groups[index].append(evidence)
            group_matches[index].update(matches)
            group_overlaps[index].update(overlaps)
            continue
        source_groups.append([evidence])
        group_matches.append(set(matches))
        group_overlaps.append(overlaps)

    # Coverage belongs to an occurrence, not whichever duplicate happened
    # to be scanned first. Resolve all group members before counting loss.
    source_slope = estimate_skew(source_projected_words)
    used: set[int] = set()
    lost: list[LostAmountEvidence] = []
    for group, matches, overlaps in zip(
        source_groups, group_matches, group_overlaps
    ):
        # A duplicate's near match can resolve another member's wider
        # overlap; unresolved crop occupancy remains ambiguous.
        if overlaps - matches:
            return None
        if len(matches) > 1 or matches & used:
            return None
        if matches:
            used.update(matches)
            continue
        if group[0].amount == 0:
            continue
        if group[0].amount < 0:
            # A missing discount/refund defeats a single positive-loss
            # explanation, even if that positive amount matches the gap.
            return None
        eligible_group = [item for item in group if item in eligible_source]
        if not eligible_group or any(
            not _UNQUALIFIED_AMOUNT_RE.fullmatch(
                re.sub(r"\s+", "", strip_tax_flag(item.text))
            )
            for item in group
        ):
            return None
        if any(
            abs(item.x - price_column) > PRICE_COLUMN_X_TOL for item in group
        ):
            return None
        # Original qualifiers can disappear together with their price. Keep
        # any available source-row context as a second ambiguity guard.
        for item in group:
            source_row = [
                word
                for word in source_context
                if abs(
                    word["y_mid"]
                    - source_slope * word["x"]
                    - (item.y + item.height / 2 - source_slope * item.x)
                )
                <= max(float(word["h"]), item.height) * 0.75
            ]
            if (
                source_row
                and _has_unpriced_name(item, source_row, source_slope)
                is not True
            ):
                return None
        unpriced_names = [
            _has_unpriced_name(item, zone_words, crop_slope) for item in group
        ]
        # Every uncovered positive observation must have clear product
        # context. Missing/numeric-only rows and rejected summary context
        # cannot be ignored: they may conceal a second lost amount.
        if not all(context is True for context in unpriced_names):
            return None
        # Preserve an actual observation and stable provenance; never
        # synthesize an average box for the duplicate group.
        lost.append(
            min(
                eligible_group,
                key=lambda item: (
                    item.source_line_id,
                    item.source_word_id,
                    item.x,
                    item.y,
                    item.width,
                    item.height,
                    item.text,
                ),
            )
        )
    return (
        lost[0]
        if len(lost) == 1 and round(lost[0].amount * 100) == gap
        else None
    )

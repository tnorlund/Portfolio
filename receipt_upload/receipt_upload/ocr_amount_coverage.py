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

from receipt_upload.geometry.transformations import find_perspective_coeffs
from receipt_upload.line_items.geometry import (
    NON_PRODUCT_NOTE_RE,
    is_line_price_word,
    is_settlement_row,
    is_unit_rate_row,
    strip_tax_flag,
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
    if not is_line_price_word({"text": text}):
        return None
    amount = parse_receipt_amount(strip_tax_flag(text))
    if amount is None or not math.isfinite(amount):
        return None
    return round(amount * 100)


def _transform(bounds: dict[str, Any]) -> list[float]:
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
    return find_perspective_coeffs(
        [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)],
        points,
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
    for px, py in (
        (x, y),
        (x + width, y),
        (x, y + height),
        (x + width, y + height),
    ):
        denominator = 1 + g * px + h * py
        if abs(denominator) < 1e-10:
            return None
        qx = (a * px + b * py + c) / denominator
        qy = (d * px + e * py + f) / denominator
        if require_inside and not (0 <= qx <= 1 and 0 <= qy <= 1):
            return None
        points.append((qx, qy))
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


def _same_source_observation(
    left: LostAmountEvidence, right: LostAmountEvidence
) -> bool:
    return (
        left.amount == right.amount
        and abs(left.x - right.x) < min(left.width, right.width) * 0.25
        and abs(left.y - right.y) < min(left.height, right.height) * 0.25
    )


def _has_unpriced_name(
    evidence: LostAmountEvidence, zone_words: Sequence[dict[str, Any]]
) -> bool | None:
    """Return product context, or None when a summary-shaped row is unsure."""
    row = [
        word
        for word in zone_words
        if abs(word["y_mid"] - evidence.y - evidence.height / 2)
        <= max(float(word["h"]), evidence.height) * 0.75
    ]
    if any(is_line_price_word(word) for word in row):
        # A surviving unit price does not prove the extended price survived.
        return None
    text = " ".join(word["text"] for word in sorted(row, key=lambda w: w["x"]))
    bare = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z\s]", " ", text)).strip()
    # Summary vocabulary can appear in product names as well as in
    # department totals and settlement rows. Text alone cannot resolve that
    # ambiguity: retain the missing observation as a blocker, regardless of
    # prefixes or suffixes, rather than guessing a product/summary class.
    if any(
        pattern.search(bare)
        for pattern in (SUBTOTAL_KEYWORD_RE, TAX_KEYWORD_RE, TOTAL_KEYWORD_RE)
    ):
        return None
    return (
        bool(re.search(r"[A-Za-z]{2}", text))
        and not is_settlement_row(bare)
        and not NON_PAYMENT_SUMMARY_RE.search(text)
        and not NON_PRODUCT_NOTE_RE.search(text)
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
    This intentionally handles only one missing positive amount, not subset
    sum search, tax corrections, missing baselines, or over-extraction.
    """
    if not source_image_id or source_image_id != receipt_image_id:
        return None
    if (
        printed_subtotal is None
        or not all(math.isfinite(v) for v in (item_sum, printed_subtotal))
        or printed_subtotal <= 0
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
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    amounts = [
        word for word in warped_words if _cents(word["text"]) is not None
    ]
    source_seen: list[LostAmountEvidence] = []
    source_groups: list[list[LostAmountEvidence]] = []
    group_matches: list[set[int]] = []
    for word in source_words:
        cents = _cents(word["text"])
        confidence = word.get("confidence", 0)
        if (
            cents is None
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or confidence < 0.95
        ):
            continue
        box = word["bounding_box"]
        projected = _project_box(box, target)
        if projected is None:
            continue
        # Any second receipt owning this observation makes attribution unsafe.
        for other in others:
            other_box = _project_box(box, other, require_inside=False)
            if other_box is None:
                return None
            ox, oy, ow, oh = other_box
            if ox < 1 and oy < 1 and ox + ow > 0 and oy + oh > 0:
                return None
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
            and _near(previous, evidence_word)
            for previous in source_seen
        ):
            return None
        nearby = [
            i
            for i, candidate in enumerate(amounts)
            if _near(evidence, candidate)
        ]
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
            continue
        source_groups.append([evidence])
        group_matches.append(set(matches))

    # Coverage belongs to an occurrence, not whichever duplicate happened
    # to be scanned first. Resolve all group members before counting loss.
    used: set[int] = set()
    lost: list[LostAmountEvidence] = []
    for group, matches in zip(source_groups, group_matches):
        if len(matches) > 1 or matches & used:
            return None
        if matches:
            used.update(matches)
            continue
        if group[0].amount <= 0:
            continue
        unpriced_names = [
            _has_unpriced_name(item, zone_words) for item in group
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
                group,
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

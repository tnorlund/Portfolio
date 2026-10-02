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

from receipt_dynamo.amounts import NON_PAYMENT_SUMMARY_RE, parse_receipt_amount

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


def _has_unpriced_name(
    evidence: LostAmountEvidence, zone_words: Sequence[dict[str, Any]]
) -> bool:
    row = [
        word
        for word in zone_words
        if abs(word["y_mid"] - evidence.y - evidence.height / 2)
        <= max(float(word["h"]), evidence.height) * 0.75
    ]
    if any(is_line_price_word(word) for word in row):
        return False
    text = " ".join(word["text"] for word in sorted(row, key=lambda w: w["x"]))
    bare = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z\s]", " ", text)).strip()
    return (
        bool(re.search(r"[A-Za-z]{2}", text))
        and not is_settlement_row(bare)
        # Match the whole summary label so product names containing "tax"
        # remain eligible (for example, "SALES TAX GUIDE").
        and not re.fullmatch(
            r"(?:sales\s+)?tax(?:\s+(?:total|amount|included))?",
            bare,
            re.IGNORECASE,
        )
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
    used: set[int] = set()
    source_seen: list[LostAmountEvidence] = []
    lost: list[LostAmountEvidence] = []
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
        # Duplicate source observations are not separate lost-price evidence.
        if any(
            previous.amount == evidence.amount
            and abs(previous.x - evidence.x) < evidence.width * 0.25
            and abs(previous.y - evidence.y) < evidence.height * 0.25
            for previous in source_seen
        ):
            continue
        source_seen.append(evidence)
        nearby = [
            i
            for i, candidate in enumerate(amounts)
            if _near(evidence, candidate)
        ]
        matches = [i for i in nearby if _cents(amounts[i]["text"]) == cents]
        if matches:
            if len(matches) != 1 or matches[0] in used:
                return None
            used.add(matches[0])
            continue
        # A different crop reading at this position is a conflict, not loss.
        if nearby:
            continue
        if cents > 0 and _has_unpriced_name(evidence, zone_words):
            lost.append(evidence)
    return (
        lost[0]
        if len(lost) == 1 and round(lost[0].amount * 100) == gap
        else None
    )

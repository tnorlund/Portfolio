"""Lambda handler for generating word similarity cache.

Scans the receipt table's RECEIPT_LINE_EMBEDDING rows for dairy milk
products, fetches receipt details with prices using parallel DynamoDB
queries, and generates a summary table for visualization with receipt
images.
"""

import hashlib
import json
import logging
import os
import re
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import boto3
from receipt_dynamo import DynamoClient
from receipt_embeddings.keys import line_canonical_key

if TYPE_CHECKING:
    from receipt_dynamo.entities import (
        Receipt,
        ReceiptLine,
        ReceiptLineItem,
        ReceiptWord,
        ReceiptWordLabel,
    )

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET", "")
CACHE_KEY = "word-similarity-cache/milk.json"
TARGET_WORD = "MILK"
LOCAL_CACHE_OUTPUT = os.environ.get("LOCAL_CACHE_OUTPUT")

# Exclusion terms for dairy milk filtering
DAIRY_EXCLUDE_TERMS = ["CHOCOLATE", "CHOC", "COCONUT", "ALMOND", "OAT", "DAT"]

# Display-name overrides for merchants whose Google Places records vary in
# casing/branding across store locations (e.g. "TRADER JOE'S" vs
# "Trader Joe's" would otherwise render as two merchants in the table).
_MERCHANT_DISPLAY_OVERRIDES = {
    "trader joe's": "Trader Joe's",
    "sprouts farmers market": "Sprouts Farmers Market",
    "whole foods market": "Whole Foods Market",
    "cvs pharmacy": "CVS Pharmacy",
    "vons": "Vons",
    "target": "Target",
}


def normalize_merchant(name: str) -> str:
    """Collapse casing/whitespace variants of the same chain into one
    display name. RECEIPT_PLACE.merchant_name comes verbatim from Google
    Places and differs across store locations of the same chain."""
    key = " ".join((name or "").split()).casefold()
    if not key:
        return "Unknown"
    return _MERCHANT_DISPLAY_OVERRIDES.get(key, " ".join((name or "").split()))


def milk_line_exclusion_reason(
    line: "ReceiptLine",
    lines: list["ReceiptLine"],
    words: list["ReceiptWord"],
    labels: list["ReceiptWordLabel"],
) -> str | None:
    """Distinguish a dairy purchase from prepared drinks and milk options."""
    text = line.text.upper()
    if any(term in text for term in DAIRY_EXCLUDE_TERMS):
        return "excluded_milk_variant"
    drink_pattern = r"\b(?:LATTE|CAPPUCCINO|MATCHA|MILK\s*SHAKE)\b"
    if re.search(drink_pattern, text):
        return "prepared_drink"
    if re.search(r"\bSPLASH\s+OF\b", text):
        return "milk_modifier"
    following = next(
        (other for other in lines if other.line_id == line.line_id + 1),
        None,
    )
    if following and re.fullmatch(
        r"\s*(?:MATCHA\s+)?LATTE\s*", following.text.upper()
    ):
        return "prepared_drink"
    if (
        find_price_on_visual_line(line.line_id, words, labels).price
        is not None
    ):
        return None
    # A separately priced milk row remains a purchase even next to a latte.
    # Unpriced, indented milk beneath a priced drink is an option; permit a
    # size/preparation option between them, but never skip another product.
    preceding = sorted(
        (other for other in lines if 0 < line.line_id - other.line_id <= 2),
        key=lambda other: -other.line_id,
    )
    for other in preceding:
        if re.fullmatch(
            r"\s*(?:REGULAR|SMALL|MEDIUM|LARGE|HOT|ICED)\s*",
            other.text.upper(),
        ):
            continue
        if re.search(drink_pattern, other.text.upper()):
            milk_words = [w for w in words if w.line_id == line.line_id]
            drink_words = [w for w in words if w.line_id == other.line_id]
            if (
                milk_words
                and drink_words
                and min(w.top_left["x"] for w in milk_words)
                > min(w.top_left["x"] for w in drink_words)
                and find_price_on_visual_line(
                    other.line_id, words, labels
                ).price
                is not None
            ):
                return "milk_modifier"
        break
    return None


def find_milk_line(
    lines: list["ReceiptLine"],
    target_word: str = "MILK",
    candidate_line_ids: set[int] | None = None,
    *,
    words: list["ReceiptWord"] | None = None,
    labels: list["ReceiptWordLabel"] | None = None,
) -> tuple[str, int] | None:
    """Choose purchased milk, pairing a negative row with its exact purchase.

    A nearby VOID heading alone must not cancel an unrelated milk size or
    price. When a negative milk row is present, cancel the preceding matching
    product and amount, leaving other milk purchases eligible.
    """
    candidates = [
        line
        for line in sorted(lines, key=lambda line: line.line_id)
        if target_word in line.text.upper()
        and "VOID" not in line.text.upper()
        and milk_line_exclusion_reason(line, lines, words or [], labels or [])
        is None
    ]
    amounts = {
        line.line_id: find_price_on_visual_line(
            line.line_id, words or [], labels or [], allow_negative=True
        ).price
        for line in candidates
    }
    excluded: set[int] = set()
    negative_rows = [
        line
        for line in candidates
        if amounts[line.line_id] is not None
        and Decimal(amounts[line.line_id]) < 0
    ]

    def product_name(text: str) -> str:
        text = re.sub(r"\$?-?\d+\.\d{2}", "", strip_upc_prefix(text))
        return " ".join(text.upper().split())

    for negative in negative_rows:
        excluded.add(negative.line_id)
        amount = -Decimal(amounts[negative.line_id])
        preceding = [
            line
            for line in candidates
            if line.line_id < negative.line_id
            and line.line_id not in excluded
            and product_name(line.text) == product_name(negative.text)
            and amounts[line.line_id] is not None
            and Decimal(amounts[line.line_id]) == amount
        ]
        if preceding:
            excluded.add(preceding[-1].line_id)

    for marker in lines:
        if "VOID" not in marker.text.upper():
            continue
        # The heading describes the following negative line, not whichever
        # product happened to print immediately before the heading.
        if any(
            0 < line.line_id - marker.line_id <= 2 for line in negative_rows
        ):
            continue
        excluded.update(
            line.line_id
            for line in candidates
            if 0 < marker.line_id - line.line_id <= 2
        )

    eligible = [
        line
        for line in candidates
        if line.line_id not in excluded
        and (candidate_line_ids is None or line.line_id in candidate_line_ids)
    ]
    if not eligible:
        return None
    selected = next(
        (line for line in eligible if amounts[line.line_id] is not None),
        eligible[0],
    )
    return selected.text, selected.line_id


def parse_row_line_ids(metadata: dict) -> list[int]:
    """Return the visual row's DynamoDB line IDs from row metadata."""
    raw_line_ids = metadata.get("row_line_ids")
    if isinstance(raw_line_ids, str):
        try:
            raw_line_ids = json.loads(raw_line_ids)
        except (TypeError, ValueError):
            raw_line_ids = None

    if not isinstance(raw_line_ids, (list, tuple)):
        raw_line_ids = [metadata.get("line_id")]

    line_ids = []
    for line_id in raw_line_ids:
        if isinstance(line_id, bool):
            continue
        try:
            parsed = int(line_id)
        except (TypeError, ValueError):
            continue
        if parsed >= 0 and parsed not in line_ids:
            line_ids.append(parsed)

    if not line_ids:
        raise ValueError("Embedding row is missing a valid line_id")
    return line_ids


def add_line_context(line_ids: list[int], radius: int = 2) -> list[int]:
    """Include nearby OCR line IDs needed for price and VOID detection."""
    return sorted(
        {
            candidate
            for line_id in line_ids
            for candidate in range(
                max(0, line_id - radius), line_id + radius + 1
            )
        }
    )


def merge_row_line_ids(matches: list[dict]) -> list[int]:
    """Combine visual-row line IDs for every milk match on a receipt."""
    line_ids: list[int] = []
    for match in matches:
        for line_id in parse_row_line_ids(match):
            if line_id not in line_ids:
                line_ids.append(line_id)
    return line_ids


# Price ranges for inferring milk sizes
MILK_SIZE_RANGES = {
    "RAW WHOLE MILK": [
        (0, 12.00, "Half Gallon"),
        (12.00, 25.00, "Gallon"),
    ],
    "RAN WHOLE MILK": [  # OCR error for RAW
        (0, 12.00, "Half Gallon"),
        (12.00, 25.00, "Gallon"),
    ],
    "RAW MILK": [
        (0, 8.00, "Half Gallon"),
        (8.00, 25.00, "Gallon"),
    ],
    "ORG WHOLE MILK": [
        (0, 7.50, "Half Gallon"),
        (7.50, 15.00, "Gallon"),
    ],
    "ORGANIC WHOLE MILK": [  # full-word OCR variant of ORG WHOLE MILK
        (0, 7.50, "Half Gallon"),
        (7.50, 15.00, "Gallon"),
    ],
    "ORG FF GRASSFED MILK": [
        (0, 15.00, "Half Gallon"),
    ],
    "ORG FF GRASSED MILK": [
        (0, 15.00, "Half Gallon"),
    ],
    "WHOLE MILK": [
        (0, 6.00, "Half Gallon"),
        (6.00, 15.00, "Gallon"),
    ],
    "W WHOLE MILK": [  # OCR partial scan of WHOLE MILK
        (0, 6.00, "Half Gallon"),
        (6.00, 15.00, "Gallon"),
    ],
    "V CRNR WHOLE MILK": [
        (0, 10.00, "Gallon"),
    ],
    "VIT D WHOLE MILK": [
        (0, 10.00, "Gallon"),
    ],
    "MILK QUART WHOLE": [  # Trader Joe's OCR word-order variant
        (0, 15.00, "Quart"),
    ],
    "MILK RAW": [  # word-order OCR variant of RAW MILK
        (0, 8.00, "Half Gallon"),
        (8.00, 25.00, "Gallon"),
    ],
    "MILK RAW WHOLE": [  # word-order OCR variant of RAW WHOLE MILK
        (0, 12.00, "Half Gallon"),
        (12.00, 25.00, "Gallon"),
    ],
    "MILK WHOLE RAW LAT-": [  # truncated OCR variant of RAW WHOLE MILK
        (0, 12.00, "Half Gallon"),
        (12.00, 25.00, "Gallon"),
    ],
    "WHOLE MILK ORG": [  # word-reversed ORG WHOLE MILK
        (0, 7.50, "Half Gallon"),
        (7.50, 15.00, "Gallon"),
    ],
}

# Initialize clients
s3_client = boto3.client("s3")


@dataclass
class TimingStats:
    """Track timing for each step of the pipeline."""

    line_fetch_all: float = 0.0
    filter_lines: float = 0.0
    dynamo_fetch_total: float = 0.0
    dynamo_fetch_details: list = field(default_factory=list)
    dynamo_items_returned: list[int] = field(default_factory=list)
    visual_line_assembly: list = field(default_factory=list)
    total: float = 0.0
    parallel_workers: int = 0

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        result = {
            "line_fetch_all_ms": round(self.line_fetch_all * 1000, 1),
            "filter_lines_ms": round(self.filter_lines * 1000, 1),
            "dynamo_fetch_total_ms": round(self.dynamo_fetch_total * 1000, 1),
            "total_ms": round(self.total * 1000, 1),
            "parallel_workers": self.parallel_workers,
        }

        # Add DynamoDB breakdown if available
        if self.dynamo_fetch_details:
            avg_details = sum(self.dynamo_fetch_details) / len(
                self.dynamo_fetch_details
            )
            result["dynamo_details"] = {
                "avg_ms": round(avg_details * 1000, 1),
                "min_ms": round(min(self.dynamo_fetch_details) * 1000, 1),
                "max_ms": round(max(self.dynamo_fetch_details) * 1000, 1),
                "count": len(self.dynamo_fetch_details),
                "items_returned": sum(self.dynamo_items_returned),
            }

            # Calculate speedup from parallelization
            sequential_time = sum(self.dynamo_fetch_details)
            if self.dynamo_fetch_total > 0:
                result["dynamo_details"]["sequential_ms"] = round(
                    sequential_time * 1000, 1
                )
                result["dynamo_details"]["speedup"] = round(
                    sequential_time / self.dynamo_fetch_total, 1
                )

        if self.visual_line_assembly:
            avg_visual = sum(self.visual_line_assembly) / len(
                self.visual_line_assembly
            )
            result["visual_line_assembly"] = {
                "avg_ms": round(avg_visual * 1000, 1),
                "min_ms": round(min(self.visual_line_assembly) * 1000, 1),
                "max_ms": round(max(self.visual_line_assembly) * 1000, 1),
            }

        return result


# Explicit size tokens printed in product names, checked before any
# price-range inference (longest match first: HALF GALLON before GALLON).
_EXPLICIT_SIZE_TOKENS = [
    ("HALF GALLON", "Half Gallon"),
    ("HALF GAL", "Half Gallon"),
    ("1/2 GALLON", "Half Gallon"),
    ("1/2 GAL", "Half Gallon"),
    ("GALLON", "Gallon"),
    ("QUART", "Quart"),
    ("QT", "Quart"),
    ("PINT", "Pint"),
]

# Generic dairy-milk fallback when the product name has no explicit size
# and no per-product range entry. Half gallons of whole/organic/A2 milk
# cluster under ~$6.50; gallons above.
_GENERIC_MILK_RANGES = [
    (0, 6.50, "Half Gallon"),
    (6.50, 25.00, "Gallon"),
]


def strip_upc_prefix(product: str) -> str:
    """Drop a leading UPC/item-code (8+ digits) from a product name:
    '7989315000 V CRNR WHOLE MILK' -> 'V CRNR WHOLE MILK'."""
    return re.sub(r"^\d{8,}\s+", "", product or "").strip()


def infer_size(product: str, price: Optional[str]) -> str:
    """Infer product size: explicit size words in the name win, then
    per-product price ranges, then a generic dairy-milk price fallback."""
    product_upper = strip_upc_prefix(product).upper().strip()

    # 1) The receipt names the size outright — no price needed.
    padded = f" {product_upper} "
    for token, size in _EXPLICIT_SIZE_TOKENS:
        if f" {token} " in padded or padded.strip().endswith(token):
            return size

    if not price:
        return "Unknown"

    try:
        price_val = float(str(price).replace("$", "").replace(",", ""))
    except (ValueError, AttributeError):
        return "Unknown"

    # 2) Per-product calibrated ranges, else 3) generic milk ranges.
    ranges = MILK_SIZE_RANGES.get(product_upper, _GENERIC_MILK_RANGES)
    for min_price, max_price, size in ranges:
        if min_price <= price_val < max_price:
            return size

    return "Unknown"


def assemble_visual_lines(words, labels):
    """Group words into visual lines by y-coordinate proximity."""
    if not words:
        return []

    # Build label lookup
    labels_by_word = defaultdict(list)
    for label in labels:
        key = (label.line_id, label.word_id)
        labels_by_word[key].append(label)

    def get_valid_label(line_id, word_id):
        history = labels_by_word.get((line_id, word_id), [])
        valid = [lbl for lbl in history if lbl.validation_status == "VALID"]
        if valid:
            valid.sort(key=lambda lbl: str(lbl.timestamp_added), reverse=True)
            return valid[0]
        return None

    # Build word contexts with centroids
    word_contexts = []
    for word in words:
        centroid = word.calculate_centroid()
        label = get_valid_label(word.line_id, word.word_id)
        word_contexts.append(
            {
                "word": word,
                "label": label,
                "y": centroid[1],
                "x": centroid[0],
            }
        )

    # Sort by y descending
    sorted_contexts = sorted(word_contexts, key=lambda c: -c["y"])

    # Calculate tolerance
    heights = [
        w["word"].bounding_box.get("height", 0.02)
        for w in sorted_contexts
        if w["word"].bounding_box.get("height")
    ]
    if heights:
        y_tolerance = max(0.01, statistics.median(heights) * 0.75)
    else:
        y_tolerance = 0.015

    # Group by y-proximity
    visual_lines = []
    current_words = [sorted_contexts[0]]
    current_y = sorted_contexts[0]["y"]

    for ctx in sorted_contexts[1:]:
        if abs(ctx["y"] - current_y) <= y_tolerance:
            current_words.append(ctx)
            current_y = sum(c["y"] for c in current_words) / len(current_words)
        else:
            current_words.sort(key=lambda c: c["x"])
            visual_lines.append(current_words)
            current_words = [ctx]
            current_y = ctx["y"]

    current_words.sort(key=lambda c: c["x"])
    visual_lines.append(current_words)

    return visual_lines


@dataclass(frozen=True)
class PriceMatch:
    """A price or an explicit reason not to use a weaker fallback."""

    price: str | None
    source: str


def normalize_price(value: str | float | Decimal | None) -> str | None:
    """Return a finite, nonnegative money amount, including free items."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).strip().removeprefix("$").replace(",", ""))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return f"{amount:.2f}"


def _printed_amount(text: str) -> Decimal | None:
    """Parse complete printed money tokens; never fill missing OCR digits."""
    token = text.strip().removesuffix("*")
    if not re.fullmatch(r"\$?-?\d+(?:,\d{3})*\.\d{2}", token):
        return None
    return Decimal(token.removeprefix("$").replace(",", ""))


def _split_price_contexts(contexts: list[dict]) -> list[dict]:
    """Join a unique adjacent '$5' + '99' pair without inventing digits."""
    allowed = [
        ctx
        for ctx in contexts
        if ctx["label"] is None
        or ctx["label"].label in {"LINE_TOTAL", "UNIT_PRICE"}
    ]
    pairs = []
    for dollars in allowed:
        if not re.fullmatch(r"\$\d+", dollars["word"].text.strip()):
            continue
        left = dollars["word"]
        candidates = []
        for cents in allowed:
            if not re.fullmatch(r"\d{2}", cents["word"].text.strip()):
                continue
            right = cents["word"]
            height = min(
                left.bounding_box.get("height", 0.02),
                right.bounding_box.get("height", 0.02),
            )
            gap = right.top_left["x"] - left.top_right["x"]
            width = max(
                left.top_right["x"] - left.top_left["x"],
                right.top_right["x"] - right.top_left["x"],
            )
            if (
                cents["x"] > dollars["x"]
                and -0.2 * width <= gap <= 0.8 * height
                and abs(right.bottom_left["y"] - left.bottom_left["y"])
                <= 0.3 * height
            ):
                candidates.append(cents)
        if len(candidates) == 1:
            pairs.append((dollars, candidates[0]))
    return [
        {
            **dollars,
            "x": (dollars["x"] + cents["x"]) / 2,
            "amount": Decimal(
                dollars["word"].text.strip()[1:]
                + "."
                + cents["word"].text.strip()
            ),
        }
        for dollars, cents in pairs
        if sum(other is cents for _, other in pairs) == 1
    ]


def _row_baseline(contexts: list[dict]) -> tuple[float, float, float, float]:
    """Fit the bottom ink edge across product words, accounting for skew."""
    points = [
        (
            ctx["x"],
            (
                ctx["word"].bottom_left["y"]
                + getattr(
                    ctx["word"], "bottom_right", ctx["word"].bottom_left
                )["y"]
            )
            / 2,
        )
        for ctx in contexts
    ]
    mean_x = statistics.mean(x for x, _ in points)
    mean_y = statistics.mean(y for _, y in points)
    variance = sum((x - mean_x) ** 2 for x, _ in points)
    slope = (
        sum((x - mean_x) * (y - mean_y) for x, y in points) / variance
        if variance > 0.0001
        else 0.0
    )
    height = statistics.median(
        ctx["word"].bounding_box.get("height", 0.02) for ctx in contexts
    )
    return mean_x, mean_y, slope, max(height, 0.001)


def find_price_on_visual_line(
    target_line_id: int,
    words: list["ReceiptWord"],
    labels: list["ReceiptWordLabel"],
    *,
    same_line_only: bool = False,
    allow_negative: bool = False,
) -> PriceMatch:
    """Associate a printed amount with its product, not a broad visual band.

    Split prices must align with the product's projected bottom ink edge
    within 0.65 text heights, and that product must be the unambiguous closest
    product row. This handles skew and separate OCR price columns without
    borrowing a nearby row's price. Negative amounts remain visible to void
    pairing but are never returned as purchased milk prices.
    """
    contexts = [
        ctx for row in assemble_visual_lines(words, labels) for ctx in row
    ]
    by_line: dict[int, list[dict]] = defaultdict(list)
    for ctx in contexts:
        if re.search(r"[A-Za-z]{2}", ctx["word"].text):
            by_line[ctx["word"].line_id].append(ctx)
    target = by_line.get(target_line_id, [])
    if not target:
        return PriceMatch(None, "missing")

    def amount_result(candidates: list[dict], source: str) -> PriceMatch:
        for role in ("LINE_TOTAL", "UNIT_PRICE", None):
            amounts = {
                ctx["amount"]
                for ctx in candidates
                if (ctx["label"].label if ctx["label"] else None) == role
            }
            if len(amounts) > 1:
                return PriceMatch(None, "ambiguous")
            if amounts:
                amount = amounts.pop()
                if amount < 0 and not allow_negative:
                    return PriceMatch(None, "void")
                return PriceMatch(f"{amount:.2f}", source)
        return PriceMatch(None, "missing")

    prices = [
        {**ctx, "amount": _printed_amount(ctx["word"].text)}
        for ctx in contexts
        if _printed_amount(ctx["word"].text) is not None
        and (
            ctx["label"] is None
            or ctx["label"].label in {"LINE_TOTAL", "UNIT_PRICE"}
        )
    ]
    prices.extend(_split_price_contexts(contexts))
    same_line = [
        ctx for ctx in prices if ctx["word"].line_id == target_line_id
    ]
    if same_line:
        return amount_result(same_line, "labels")
    if same_line_only:
        return PriceMatch(None, "missing")

    # Multiword text also supplies competing rows when product labels are
    # missing. Single-word section headings and food-code letters do not.
    product_rows = {
        line_id: _row_baseline(row)
        for line_id, row in by_line.items()
        if line_id == target_line_id
        or (
            not any("VOID" in ctx["word"].text.upper() for ctx in row)
            and (
                sum(
                    len(re.findall(r"[A-Za-z]{2,}", ctx["word"].text))
                    for ctx in row
                )
                >= 2
                or any(
                    ctx["label"] and ctx["label"].label == "PRODUCT_NAME"
                    for ctx in row
                )
            )
        )
    }
    target_x, target_y, target_slope, target_height = product_rows[
        target_line_id
    ]
    target_left = min(ctx["word"].top_left["x"] for ctx in target)
    target_right = max(ctx["word"].top_right["x"] for ctx in target)
    # A deposit description can itself print "$2.00" and repeat that amount
    # in the total column. Once both agree on its row, it must not compete
    # for a different nearby milk total. Unit-price-only rows stay eligible
    # competitors because their extended total can legitimately differ.
    established_totals: dict[int, Decimal] = {}
    for line_id, (x, y, slope, height) in product_rows.items():
        explicit = {
            ctx["amount"]
            for ctx in prices
            if ctx["word"].line_id == line_id
            and (ctx["label"] is None or ctx["label"].label == "LINE_TOTAL")
        }
        if len(explicit) != 1:
            continue
        amount = next(iter(explicit))
        for ctx in prices:
            if (
                ctx["word"].line_id == line_id
                or ctx["amount"] != amount
                or ctx["label"] is None
                or ctx["label"].label != "LINE_TOTAL"
            ):
                continue
            word = ctx["word"]
            price_y = (word.bottom_left["y"] + word.bottom_right["y"]) / 2
            error = abs(price_y - (y + slope * (ctx["x"] - x)))
            if error <= height * 0.65 and all(
                abs(price_y - (oy + oslope * (ctx["x"] - ox))) > error
                for other_id, (ox, oy, oslope, _) in product_rows.items()
                if other_id != line_id
            ):
                established_totals[line_id] = amount
    owned = []
    nearby_price = False
    for ctx in prices:
        price_x = ctx["x"]
        if target_left <= price_x <= target_right:
            continue
        word = ctx["word"]
        price_y = (
            word.bottom_left["y"]
            + getattr(word, "bottom_right", word.bottom_left)["y"]
        ) / 2
        error = abs(price_y - (target_y + target_slope * (price_x - target_x)))
        if error > target_height * 0.65:
            continue
        nearby_price = True
        competing = [
            abs(price_y - (y + slope * (price_x - x)))
            for line_id, (x, y, slope, height) in product_rows.items()
            if line_id != target_line_id
            and (
                line_id not in established_totals
                or established_totals[line_id] == ctx["amount"]
            )
            and (price_x - x) * (price_x - target_x) > 0
            and abs(y - target_y) <= max(height, target_height) * 3
        ]
        if competing and min(competing) <= error + target_height * 0.1:
            continue
        owned.append(ctx)
    if owned:
        return amount_result(owned, "row_geometry")
    return PriceMatch(None, "ambiguous" if nearby_price else "missing")


def find_milk_price(
    target_line_id: int,
    words: list["ReceiptWord"],
    labels: list["ReceiptWordLabel"],
    line_items: list["ReceiptLineItem"],
    row_text: str,
    *,
    line_items_available: bool = True,
) -> PriceMatch:
    """Require direct row evidence, including for reconciled canonical items.

    A receipt total can reconcile despite swapped item prices. Canonical
    prices therefore need corroboration from the milk row; unrelated receipt
    mismatches or bad item grouping cannot veto uniquely owned row evidence.
    """
    result = find_price_on_visual_line(target_line_id, words, labels)
    matches = [item for item in line_items if target_line_id in item.line_ids]
    if result.price is not None:
        if len(matches) == 1:
            item = matches[0]
            name = item.name.upper()
            if (
                TARGET_WORD in name
                and not any(term in name for term in DAIRY_EXCLUDE_TERMS)
                and "VOID" not in name
                and item.name_quality == "ok"
                and not item.is_discount
                and not item.collapsed_banding
                and item.source_section_status in {"VALID", "PENDING"}
                and item.reconciliation_status in {"match", "near"}
                and normalize_price(item.price) == result.price
            ):
                return PriceMatch(result.price, "line_item")
        return result
    if result.source != "missing":
        return result
    if not line_items_available:
        return PriceMatch(None, "line_items_unavailable")
    if line_items:
        source = (
            "ambiguous"
            if len(matches) > 1
            else ("untrusted" if matches else "unmatched")
        )
        return PriceMatch(None, source)
    # A legacy row can retain an explicit trailing price only when there is
    # no competing geometric evidence and exactly one complete money token.
    tokens = list(
        re.finditer(r"(?<!\S)\$?\d+(?:,\d{3})*\.\d{2}(?!\S)", row_text)
    )
    if len(tokens) > 1:
        return PriceMatch(None, "ambiguous")
    if tokens and tokens[0].end() == len(row_text.rstrip()):
        return PriceMatch(normalize_price(tokens[0].group()), "row_text")
    return PriceMatch(None, "missing")


def calculate_product_bbox(target_line_id, words, labels):
    """Calculate bounding box around product line for cropping.

    Returns bbox in normalized coordinates (0-1) with format:
    {tl: {x, y}, tr: {x, y}, bl: {x, y}, br: {x, y}}
    where y=1 is top and y=0 is bottom (receipt coordinate system).
    """
    visual_lines = assemble_visual_lines(words, labels)

    # Find visual line containing target
    target_visual_line = None
    target_visual_line_idx = None
    for idx, vl in enumerate(visual_lines):
        for ctx in vl:
            if ctx["word"].line_id == target_line_id:
                target_visual_line = vl
                target_visual_line_idx = idx
                break
        if target_visual_line:
            break

    if not target_visual_line:
        return None

    # Get lines to include (target + 1 above + 1 below for context)
    lines_to_include = []
    if target_visual_line_idx > 0:
        lines_to_include.extend(visual_lines[target_visual_line_idx - 1])
    lines_to_include.extend(target_visual_line)
    if target_visual_line_idx < len(visual_lines) - 1:
        lines_to_include.extend(visual_lines[target_visual_line_idx + 1])

    if not lines_to_include:
        return None

    # Calculate bounding box from all words using normalized coordinates
    # Use top_left.x and top_right.x for horizontal bounds (normalized 0-1)
    min_x = min(ctx["word"].top_left.get("x", 0) for ctx in lines_to_include)
    max_x = max(ctx["word"].top_right.get("x", 1) for ctx in lines_to_include)
    # Y coordinates: use top_left.y (top) and bottom_left.y (bottom)
    max_y = max(ctx["word"].top_left.get("y", 1) for ctx in lines_to_include)
    min_y = min(
        ctx["word"].bottom_left.get("y", 0) for ctx in lines_to_include
    )

    # Add padding (5% on x, variable on y)
    padding_x = (max_x - min_x) * 0.05
    padding_y = max((max_y - min_y) * 0.05, 0.02)

    left = max(0, min_x - padding_x)
    right = min(1, max_x + padding_x)
    bottom = max(0, min_y - padding_y)
    top = min(1, max_y + padding_y)

    return {
        "tl": {"x": left, "y": top},
        "tr": {"x": right, "y": top},
        "bl": {"x": left, "y": bottom},
        "br": {"x": right, "y": bottom},
    }


def receipt_fingerprint(
    receipt: "Receipt", lines: list["ReceiptLine"], words: list["ReceiptWord"]
) -> str | None:
    """Identify exact duplicate receipt evidence without deleting source data."""
    if getattr(receipt, "sha256", None):
        return "image:" + receipt.sha256
    # Small fragments can repeat across distinct purchases. Require a full
    # nontrivial transcript and exact geometry, including transaction text.
    if len(lines) < 10 or len(words) < 40:
        return None
    evidence = {
        "width": receipt.width,
        "height": receipt.height,
        "lines": sorted(
            (
                line.text,
                line.top_left["x"],
                line.top_left["y"],
                line.bottom_right["x"],
                line.bottom_right["y"],
            )
            for line in lines
        ),
        "words": sorted(
            (
                word.text,
                word.top_left["x"],
                word.top_left["y"],
                word.bottom_right["x"],
                word.bottom_right["y"],
            )
            for word in words
        ),
    }
    return (
        "ocr:"
        + hashlib.sha256(
            json.dumps(evidence, sort_keys=True).encode("utf-8")
        ).hexdigest()
    )


def deduplicate_receipts(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep one deterministic priced example for each identical receipt."""
    seen: dict[str, dict] = {}
    unique = []
    duplicates = []
    for result in sorted(
        results,
        key=lambda result: (
            result["price"] is None,
            result["image_id"],
            result["receipt_id"],
        ),
    ):
        fingerprint = result.pop("_fingerprint", None)
        if fingerprint and fingerprint in seen:
            kept = seen[fingerprint]
            duplicates.append(
                {
                    "image_id": result["image_id"],
                    "receipt_id": result["receipt_id"],
                    "kept_image_id": kept["image_id"],
                    "kept_receipt_id": kept["receipt_id"],
                }
            )
            continue
        if fingerprint:
            seen[fingerprint] = result
        unique.append(result)
    return unique, duplicates


def receipt_to_dict(receipt):
    """Convert Receipt entity to dict for JSON serialization."""
    return {
        "image_id": receipt.image_id,
        "receipt_id": receipt.receipt_id,
        "width": receipt.width,
        "height": receipt.height,
        "timestamp_added": str(receipt.timestamp_added),
        "raw_s3_bucket": receipt.raw_s3_bucket,
        "raw_s3_key": receipt.raw_s3_key,
        "top_left": receipt.top_left,
        "top_right": receipt.top_right,
        "bottom_left": receipt.bottom_left,
        "bottom_right": receipt.bottom_right,
        "sha256": receipt.sha256,
        "cdn_s3_bucket": getattr(receipt, "cdn_s3_bucket", None),
        "cdn_s3_key": getattr(receipt, "cdn_s3_key", None),
        "cdn_webp_s3_key": getattr(receipt, "cdn_webp_s3_key", None),
        "cdn_avif_s3_key": getattr(receipt, "cdn_avif_s3_key", None),
        "cdn_thumbnail_s3_key": getattr(receipt, "cdn_thumbnail_s3_key", None),
        "cdn_thumbnail_webp_s3_key": getattr(
            receipt, "cdn_thumbnail_webp_s3_key", None
        ),
        "cdn_thumbnail_avif_s3_key": getattr(
            receipt, "cdn_thumbnail_avif_s3_key", None
        ),
        "cdn_small_s3_key": getattr(receipt, "cdn_small_s3_key", None),
        "cdn_small_webp_s3_key": getattr(
            receipt, "cdn_small_webp_s3_key", None
        ),
        "cdn_small_avif_s3_key": getattr(
            receipt, "cdn_small_avif_s3_key", None
        ),
        "cdn_medium_s3_key": getattr(receipt, "cdn_medium_s3_key", None),
        "cdn_medium_webp_s3_key": getattr(
            receipt, "cdn_medium_webp_s3_key", None
        ),
        "cdn_medium_avif_s3_key": getattr(
            receipt, "cdn_medium_avif_s3_key", None
        ),
    }


def line_to_dict(line):
    """Convert ReceiptLine entity to dict for JSON serialization."""
    return {
        "image_id": line.image_id,
        "line_id": line.line_id,
        "text": line.text,
        "bounding_box": line.bounding_box,
        "top_left": line.top_left,
        "top_right": line.top_right,
        "bottom_left": line.bottom_left,
        "bottom_right": line.bottom_right,
        "angle_degrees": getattr(line, "angle_degrees", 0),
        "angle_radians": getattr(line, "angle_radians", 0),
        "confidence": getattr(line, "confidence", 1.0),
    }


def _fetch_lines_from_dynamo(timing: TimingStats, dynamo_client) -> dict:
    """Fetch milk line rows from DynamoDB RECEIPT_LINE_EMBEDDING items.

    One GSITYPE query (projection skips the 1536-dim vectors). The match
    is case-insensitive on purpose so rows like "Milk Shake" reach the
    in-memory dairy filter below. Returns ``{ids, metadatas}``.
    """
    step_start = time.time()
    client = dynamo_client._client  # pylint: disable=protected-access
    rows = []
    kwargs = {
        "TableName": DYNAMODB_TABLE_NAME,
        "IndexName": "GSITYPE",
        "KeyConditionExpression": "#t = :t",
        "ExpressionAttributeNames": {"#t": "TYPE", "#x": "text"},
        "ExpressionAttributeValues": {":t": {"S": "RECEIPT_LINE_EMBEDDING"}},
        "ProjectionExpression": ("PK, SK, #x, merchant_name, row_line_ids"),
    }
    while True:
        response = client.query(**kwargs)
        for item in response.get("Items", []):
            text = item.get("text", {}).get("S", "")
            if TARGET_WORD not in text.upper():
                continue
            pk = item["PK"]["S"]  # IMAGE#<uuid>
            sk = item["SK"]["S"]  # RECEIPT#NNNNN#LINE#NNNNN#EMBEDDING
            parts = sk.split("#")
            if len(parts) < 4 or not sk.endswith("#EMBEDDING"):
                continue
            image_id = pk.split("#", 1)[1]
            receipt_id = int(parts[1])
            line_id = int(parts[3])
            row_line_ids = [
                int(value["N"])
                for value in item.get("row_line_ids", {}).get("L", [])
            ] or [line_id]
            rows.append(
                (
                    line_canonical_key(image_id, receipt_id, line_id),
                    {
                        "text": text,
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "line_id": line_id,
                        "row_line_ids": row_line_ids,
                        "merchant_name": item.get("merchant_name", {}).get(
                            "S", ""
                        ),
                    },
                )
            )
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    rows.sort(key=lambda pair: pair[0])
    timing.line_fetch_all = time.time() - step_start
    logger.info(
        "Fetched %d '%s' line rows from DynamoDB (%.2fs)",
        len(rows),
        TARGET_WORD,
        timing.line_fetch_all,
    )
    return {
        "ids": [key for key, _ in rows],
        "metadatas": [meta for _, meta in rows],
    }


def handler(_event, _context):
    """Handle EventBridge scheduled event to generate word similarity cache."""
    logger.info("Starting milk product cache generation v2")

    timing = TimingStats()
    total_start = time.time()

    try:
        # Use a larger connection pool to match parallel workers (50)
        # plus headroom for retries
        dynamo_client = DynamoClient(
            DYNAMODB_TABLE_NAME, max_pool_connections=100
        )

        # Step 1: Fetch the candidate line rows from DynamoDB
        all_lines = _fetch_lines_from_dynamo(timing, dynamo_client)

        # Step 2: Keep milk candidates; classify actual OCR products below.
        step_start = time.time()
        matching_lines = []
        for id_, meta in zip(all_lines["ids"], all_lines["metadatas"]):
            row_text = meta.get("text", "")
            row_text_upper = row_text.upper()

            if TARGET_WORD in row_text_upper:
                # Combined embedding rows can include neighboring oat milk or
                # chocolate products. Classify the actual OCR product later.
                matching_lines.append(
                    {
                        "id": id_,
                        "text": row_text,  # Will be refined later
                        "image_id": meta.get("image_id"),
                        "receipt_id": meta.get("receipt_id"),
                        "line_id": meta.get("line_id"),
                        "row_line_ids": meta.get("row_line_ids"),
                        "merchant_name": meta.get("merchant_name"),
                    }
                )

        timing.filter_lines = time.time() - step_start
        logger.info(
            "Found %d candidate milk rows (%.1fms)",
            len(matching_lines),
            timing.filter_lines * 1000,
        )

        # Step 3: Keep separate receipt regions on the same image distinct.
        by_receipt = defaultdict(list)
        for match in matching_lines:
            by_receipt[(match["image_id"], int(match["receipt_id"]))].append(
                match
            )

        logger.info("Found %d unique receipts", len(by_receipt))

        # Step 4: Fetch receipt details in parallel
        work_items = []
        for (image_id, receipt_id), matches in by_receipt.items():
            line_id = int(matches[0]["line_id"])
            product_text = matches[0]["text"]
            row_line_ids = merge_row_line_ids(matches)
            merchant_name = matches[0].get("merchant_name")
            work_items.append(
                (
                    image_id,
                    receipt_id,
                    line_id,
                    product_text,
                    row_line_ids,
                    merchant_name,
                )
            )

        def process_receipt(work_item):
            (
                image_id,
                receipt_id,
                row_line_id,
                row_text,
                row_line_ids,
                row_merchant_name,
            ) = work_item
            timings = {"details": 0, "visual_line": 0, "items": 0}
            stage = "receipt_details"
            try:
                t0 = time.time()
                # Ownership and void pairing need all product/price columns;
                # embedding rows and nearby line IDs can omit either side.
                details = dynamo_client.get_receipt_details(
                    image_id, receipt_id, consistent_read=True
                )
                line_items_available = True
                try:
                    line_items = (
                        dynamo_client.get_receipt_line_items_from_receipt(
                            image_id, receipt_id
                        )
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    # The complete OCR rows can still prove the price. Keep
                    # the receipt and surface the failed auxiliary read.
                    logger.warning(
                        "Canonical item read failed for %s/%s",
                        image_id,
                        receipt_id,
                        exc_info=True,
                    )
                    line_items = []
                    line_items_available = False
                timings["details"] = time.time() - t0
                timings["items"] = (
                    1
                    + int(details.place is not None)
                    + len(details.lines)
                    + len(details.words)
                    + len(details.labels)
                    + len(line_items)
                )

                stage = "receipt_processing"
                # Find the specific OCR line containing "MILK"
                # This returns both text and line_id for accurate price lookup
                milk_line = find_milk_line(
                    details.lines,
                    TARGET_WORD,
                    words=details.words,
                    labels=details.labels,
                )
                if milk_line:
                    product_text, milk_line_id = milk_line
                else:
                    # Do not resurrect a voided item from stale embedding text.
                    reasons = {
                        milk_line_exclusion_reason(
                            line, details.lines, details.words, details.labels
                        )
                        for line in details.lines
                        if TARGET_WORD in line.text.upper()
                    }
                    exclusion = next(
                        (
                            reason
                            for reason in (
                                "prepared_drink",
                                "milk_modifier",
                                "excluded_milk_variant",
                            )
                            if reason in reasons
                        ),
                        "no_purchased_milk",
                    )
                    return {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "_excluded": exclusion,
                        "line_items_available": line_items_available,
                    }

                t0 = time.time()
                # Use the actual milk line_id for the price lookup
                # (not the row's primary line)
                price_match = find_milk_price(
                    milk_line_id,
                    details.words,
                    details.labels,
                    line_items,
                    row_text,
                    line_items_available=line_items_available,
                )
                if not line_items_available and price_match.price is None:
                    return {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "_failure": "canonical_line_items",
                        "error_type": "UnavailablePriceEvidence",
                        "line_items_available": False,
                    }
                # Calculate bounding box for visual cropping
                bbox = calculate_product_bbox(
                    milk_line_id, details.words, details.labels
                )
                timings["visual_line"] = time.time() - t0

                merchant = normalize_merchant(
                    details.place.merchant_name
                    if details.place
                    else row_merchant_name or "Unknown"
                )
                price = price_match.price
                if (
                    price_match.source == "row_text"
                    and product_text == row_text
                ):
                    product_text = re.sub(
                        r"\s+\$?\d+(?:,\d{3})*\.\d{2}\s*$", "", product_text
                    ).rstrip()
                product_text = strip_upc_prefix(product_text)
                size = infer_size(product_text, price)

                # The receipt line entities are used only to calculate this
                # cache entry. WordSimilarity.tsx builds the crop from
                # `receipt` + `bbox` and never reads `lines`, so do not include
                # them in the response.

                return {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "product": product_text,
                    "merchant": merchant,
                    "price": price,
                    "price_source": price_match.source,
                    "line_items_available": line_items_available,
                    "size": size,
                    "line_id": milk_line_id,
                    # Receipt header (image_id, dimensions, CDN keys) — used
                    # to build the image URL for visual display.
                    "receipt": receipt_to_dict(details.receipt),
                    "bbox": bbox,
                    "_timings": timings,
                    "_fingerprint": receipt_fingerprint(
                        details.receipt, details.lines, details.words
                    ),
                }
            except Exception as e:  # pylint: disable=broad-exception-caught
                # Finish the parallel reads so every failure is visible;
                # the persistence gate below preserves the previous cache.
                logger.warning("Error processing %s: %s", image_id, e)
                return {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "_failure": stage,
                    "error_type": type(e).__name__,
                }

        results = []
        receipt_failures = []
        excluded_receipts = []
        canonical_read_failures = 0
        max_workers = 50
        timing.parallel_workers = max_workers

        logger.info(
            "Fetching receipt details with %d parallel workers", max_workers
        )

        dynamo_start = time.time()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(process_receipt, item): item
                for item in work_items
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    if result.get("line_items_available") is False:
                        canonical_read_failures += 1
                    if "_failure" in result:
                        result["stage"] = result.pop("_failure")
                        receipt_failures.append(result)
                        continue
                    if "_excluded" in result:
                        excluded_receipts.append(
                            {
                                "image_id": result["image_id"],
                                "receipt_id": result["receipt_id"],
                                "reason": result["_excluded"],
                            }
                        )
                        continue
                    # Extract and record individual timings
                    if "_timings" in result:
                        timing.dynamo_fetch_details.append(
                            result["_timings"]["details"]
                        )
                        timing.dynamo_items_returned.append(
                            result["_timings"]["items"]
                        )
                        timing.visual_line_assembly.append(
                            result["_timings"]["visual_line"]
                        )
                        del result[
                            "_timings"
                        ]  # Remove internal timing data from output
                    results.append(result)

        timing.dynamo_fetch_total = time.time() - dynamo_start
        logger.info(
            "Processed %d receipts successfully (%.2fs)",
            len(results),
            timing.dynamo_fetch_total,
        )

        if receipt_failures:
            logger.error(
                "Refusing incomplete milk cache: %s", receipt_failures
            )
            raise RuntimeError(
                "Preserving previous milk cache; "
                f"{len(receipt_failures)} receipt reads/processing attempts failed"
            )
        results, duplicates = deduplicate_receipts(results)
        price_sources = dict(Counter(r["price_source"] for r in results))
        priced_receipts = sum(r["price"] is not None for r in results)
        price_coverage = {
            "priced_receipts": priced_receipts,
            "unpriced_receipts": len(results) - priced_receipts,
            "canonical_read_failures": canonical_read_failures,
            "failed_receipts": len(receipt_failures),
            "excluded_receipts": len(excluded_receipts),
            "sources": price_sources,
        }
        logger.info("Milk price coverage: %s", price_coverage)

        # Step 5: Build summary table
        summary = defaultdict(
            lambda: {"count": 0, "prices": [], "receipts": []}
        )
        for r in results:
            key = (r["merchant"], r["product"], r["size"])
            summary[key]["count"] += 1
            summary[key]["receipts"].append(
                {
                    "image_id": r["image_id"],
                    "receipt_id": r["receipt_id"],
                }
            )
            if r["price"]:
                try:
                    summary[key]["prices"].append(
                        float(
                            str(r["price"]).replace("$", "").replace(",", "")
                        )
                    )
                except ValueError:
                    pass

        # Convert to list format
        summary_table = []
        for (merchant, product, size), data in sorted(summary.items()):
            avg_price = None
            total = None
            if data["prices"]:
                avg_price = sum(data["prices"]) / len(data["prices"])
                total = sum(data["prices"])

            summary_table.append(
                {
                    "merchant": merchant,
                    "product": product,
                    "size": size,
                    "count": data["count"],
                    "avg_price": (
                        round(avg_price, 2) if avg_price is not None else None
                    ),
                    "total": round(total, 2) if total is not None else None,
                    "receipts": data["receipts"],
                }
            )

        # Step 6: Build response
        timing.total = time.time() - total_start

        # Calculate grand total
        grand_total = sum(row["total"] or 0 for row in summary_table)

        # Generate commentary based on the grand total
        def dollars_to_words(amount: float) -> str:
            """Convert dollar amount to approximate words."""
            if amount < 100:
                return f"{int(amount)} dollars"
            if amount < 1000:
                hundreds = int(amount // 100) * 100
                return f"{hundreds} dollars"
            thousands = int(amount // 1000)
            return f"{thousands} thousand dollars"

        commentary = (
            f"${grand_total:.2f}. That's... significantly more than "
            "I expected. "
            "I knew I liked milk, but I didn't think I "
            f'"{dollars_to_words(grand_total)} a year" liked milk.'
        )

        response_data = {
            "query_word": TARGET_WORD,
            "total_receipts": len(results),
            "total_items": len(matching_lines),
            "summary_table": summary_table,
            "receipts": results,
            "price_coverage": price_coverage,
            "duplicate_receipts_skipped": duplicates,
            "receipt_failures": receipt_failures,
            "excluded_receipts": excluded_receipts,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "timing": timing.to_dict(),
            "grand_total": round(grand_total, 2),
            "commentary": commentary,
        }

        # Step 7: Persist the cache. Local runs can write the complete result
        # to disk without mutating the deployed S3 cache.
        response_json = json.dumps(response_data, default=str)
        if LOCAL_CACHE_OUTPUT:
            output_path = Path(LOCAL_CACHE_OUTPUT).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(response_json, encoding="utf-8")
            logger.info("Wrote local cache to %s", output_path)
        else:
            logger.info(
                "Uploading cache to S3: %s/%s", S3_CACHE_BUCKET, CACHE_KEY
            )
            s3_client.put_object(
                Bucket=S3_CACHE_BUCKET,
                Key=CACHE_KEY,
                Body=response_json,
                ContentType="application/json",
            )

        logger.info(
            "Cache generation complete: %d receipts, %d summary rows "
            "(total %.2fs)",
            len(results),
            len(summary_table),
            timing.total,
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Cache generated successfully",
                    "total_receipts": len(results),
                    "summary_rows": len(summary_table),
                }
            ),
        }

    except Exception:  # pylint: disable=broad-exception-caught
        # EventBridge retries and the DLQ require an invocation failure, not
        # an HTTP-shaped 500 returned as a successful Lambda invocation.
        logger.exception("Error generating cache")
        raise


if __name__ == "__main__":
    handler_result = handler({}, None)
    print(handler_result["body"])
    raise SystemExit(0 if handler_result["statusCode"] < 400 else 1)

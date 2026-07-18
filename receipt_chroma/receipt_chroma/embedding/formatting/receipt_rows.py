"""Materialize deterministic receipt rows and their price-column pairing."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Protocol

from receipt_chroma.embedding.formatting.line_format import (
    LineLike,
    get_primary_line_id,
    group_lines_into_visual_rows,
)
from receipt_dynamo.entities import ReceiptRow

_AMOUNT_RE = re.compile(
    r"^(?:\()?[-+]?\$?(?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2}-?(?:\))?$"
)
GROUPING_VERSION = "visual-rows-v1"


class WordLike(Protocol):
    """Geometry required from a receipt word."""

    line_id: int
    word_id: int
    text: str
    bounding_box: dict[str, float]


@dataclass(frozen=True)
class PriceColumn:
    """Price-column coordinate plus its crop-derived matching tolerance."""

    x: float
    tolerance: float


@dataclass(frozen=True)
class LabelAmountPair:
    """The right-column amount and the label text to its left."""

    label_text: str
    amount_text: str
    amount_line_id: int
    amount_word_id: int


def is_amount_text(text: str) -> bool:
    """Return whether OCR text is an unambiguous cents-denominated amount."""

    return bool(_AMOUNT_RE.fullmatch(text.strip().replace(" ", "")))


def _right_edge(word: WordLike) -> float:
    box = word.bounding_box
    return float(box.get("x", 0.0)) + float(box.get("width", 0.0))


def _character_width(word: WordLike) -> float:
    compact = word.text.strip().replace(" ", "")
    return float(word.bounding_box.get("width", 0.0)) / max(len(compact), 1)


def detect_price_column(words: Sequence[WordLike]) -> PriceColumn | None:
    """Find the dominant aligned amount edge using this receipt's geometry.

    Amount right edges are joined when their distance is no larger than the
    median observed character width.  The cluster with the most distinct rows
    wins; ties prefer the larger cluster and then the rightmost edge.  Both the
    coordinate and tolerance therefore come from the upload's crop
    distribution rather than a fixed normalized-image threshold.
    """

    amounts = [word for word in words if is_amount_text(word.text)]
    if not amounts:
        return None

    widths = [width for word in amounts if (width := _character_width(word))]
    tolerance = median(widths) if widths else 0.0
    ordered = sorted(amounts, key=_right_edge)
    clusters: list[list[WordLike]] = []
    for word in ordered:
        if not clusters or (
            _right_edge(word) - _right_edge(clusters[-1][-1]) > tolerance
        ):
            clusters.append([word])
        else:
            clusters[-1].append(word)

    winner = max(
        clusters,
        key=lambda cluster: (
            len({word.line_id for word in cluster}),
            len(cluster),
            median([_right_edge(word) for word in cluster]),
        ),
    )
    return PriceColumn(
        x=float(median([_right_edge(word) for word in winner])),
        tolerance=float(tolerance),
    )


def pair_row_label_amount(
    row: Sequence[LineLike],
    words: Sequence[WordLike],
    price_column: PriceColumn | None,
) -> LabelAmountPair | None:
    """Pair the row's aligned price with the text geometrically to its left."""

    if not row or price_column is None:
        return None
    line_ids = {line.line_id for line in row}
    row_words = sorted(
        (word for word in words if word.line_id in line_ids),
        key=lambda word: (
            float(word.bounding_box.get("x", 0.0)),
            word.line_id,
            word.word_id,
        ),
    )
    candidates = [
        word
        for word in row_words
        if is_amount_text(word.text)
        and abs(_right_edge(word) - price_column.x) <= price_column.tolerance
    ]
    if not candidates:
        return None
    amount = max(candidates, key=_right_edge)
    amount_x = float(amount.bounding_box.get("x", 0.0))
    label_text = " ".join(
        word.text.strip()
        for word in row_words
        if _right_edge(word) <= amount_x and not is_amount_text(word.text)
    ).strip()
    return LabelAmountPair(
        label_text=label_text,
        amount_text=amount.text,
        amount_line_id=amount.line_id,
        amount_word_id=amount.word_id,
    )


def build_receipt_rows(
    lines: Sequence[LineLike],
    words: Sequence[WordLike],
    *,
    created_at: datetime | None = None,
) -> list[ReceiptRow]:
    """Build persisted rows for one receipt from OCR lines and words."""

    if not lines:
        return []
    image_ids = {line.image_id for line in lines}
    receipt_ids = {line.receipt_id for line in lines}
    if len(image_ids) != 1 or len(receipt_ids) != 1:
        raise ValueError("lines must belong to exactly one receipt")
    timestamp = created_at or datetime.now(timezone.utc)
    price_column = detect_price_column(words)
    result: list[ReceiptRow] = []
    for row in group_lines_into_visual_rows(lines):
        boxes = [line.bounding_box for line in row]
        pair = pair_row_label_amount(row, words, price_column)
        result.append(
            ReceiptRow(
                image_id=next(iter(image_ids)),
                receipt_id=next(iter(receipt_ids)),
                row_id=get_primary_line_id(row),
                line_ids=[line.line_id for line in row],
                grouping_version=GROUPING_VERSION,
                y_min=min(float(box.get("y", 0.0)) for box in boxes),
                y_max=max(
                    float(box.get("y", 0.0)) + float(box.get("height", 0.0))
                    for box in boxes
                ),
                x_min=min(float(box.get("x", 0.0)) for box in boxes),
                x_max=max(
                    float(box.get("x", 0.0)) + float(box.get("width", 0.0))
                    for box in boxes
                ),
                created_at=timestamp,
                price_column_x=(price_column.x if price_column else None),
                label_text=pair.label_text if pair else None,
                amount_text=pair.amount_text if pair else None,
                amount_line_id=pair.amount_line_id if pair else None,
                amount_word_id=pair.amount_word_id if pair else None,
            )
        )
    return result

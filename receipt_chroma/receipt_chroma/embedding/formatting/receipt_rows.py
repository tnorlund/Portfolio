"""Back-compat re-export. Implementation lives in receipt_embeddings."""

from receipt_embeddings.formatting.receipt_rows import *  # noqa: F403
from receipt_embeddings.formatting.receipt_rows import (
    GROUPING_VERSION,
    LabelAmountPair,
    PriceColumn,
    WordLike,
    build_receipt_rows,
    detect_price_column,
    is_amount_text,
    pair_row_label_amount,
)

__all__ = [
    "GROUPING_VERSION",
    "LabelAmountPair",
    "PriceColumn",
    "WordLike",
    "build_receipt_rows",
    "detect_price_column",
    "is_amount_text",
    "pair_row_label_amount",
]

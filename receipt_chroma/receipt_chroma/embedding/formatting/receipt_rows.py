"""Compatibility exports for :mod:`receipt_embeddings.formatting.receipt_rows`."""

from receipt_embeddings.formatting.receipt_rows import *  # noqa: F401,F403

__all__ = [
    "GROUPING_VERSION",
    "WordLike",
    "PriceColumn",
    "LabelAmountPair",
    "is_amount_text",
    "detect_price_column",
    "pair_row_label_amount",
    "build_receipt_rows",
]

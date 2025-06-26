"""Utility modules for merchant validation."""

from .normalization import (format_canonical_merchant_name, normalize_address,
                            normalize_phone, normalize_text,
                            preprocess_for_comparison)
from .similarity import (get_address_similarity, get_name_similarity,
                         get_phone_similarity)

__all__ = [
    # Normalization functions
    "normalize_address",
    "normalize_phone",
    "normalize_text",
    "preprocess_for_comparison",
    "format_canonical_merchant_name",
    # Similarity functions
    "get_address_similarity",
    "get_name_similarity",
    "get_phone_similarity",
]

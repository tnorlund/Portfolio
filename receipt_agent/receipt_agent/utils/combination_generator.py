"""
Utility functions for generating receipt combinations.

Provides basic combinatorial logic for creating pairwise combinations
of receipt IDs, used across multiple receipt processing handlers.
"""

from itertools import combinations
from typing import List, Tuple


def generate_receipt_combinations(
    receipt_ids: List[int],
) -> List[Tuple[int, ...]]:
    """
    Generate all possible pairwise combinations of receipt IDs.

    For 3 receipts [1, 2, 3], returns: [(1, 2), (1, 3), (2, 3)]

    Args:
        receipt_ids: List of receipt IDs to combine

    Returns:
        List of tuples representing all possible pairs
    """
    return list(combinations(receipt_ids, 2))

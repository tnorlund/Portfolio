"""Helper functions for OpenAI batch operations.

This module provides utility functions used across OpenAI batch operations.
"""

from typing import List, Tuple


def get_unique_receipt_and_image_ids(
    results: List[dict],
) -> List[Tuple[int, str]]:
    """
    Get the unique receipt ids and image ids from the embedding results.

    Args:
        results: List of embedding result dictionaries with 'custom_id' field

    Returns:
        List of tuples, each containing (receipt_id, image_id)
    """
    return list(
        set(
            (int(r["custom_id"].split("#")[3]), r["custom_id"].split("#")[1])
            for r in results
        )
    )

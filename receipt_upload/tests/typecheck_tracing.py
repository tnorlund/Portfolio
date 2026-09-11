"""Static regression checks for the public tracing decorator."""

from typing import assert_type

from receipt_upload.tracing import traceable


@traceable(name="type-check")
def increment(value: int, *, amount: int = 1) -> int:
    """Keep positional, keyword-only, and return types after decoration."""
    return value + amount


assert_type(increment(1, amount=2), int)

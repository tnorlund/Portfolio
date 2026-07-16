"""Receipt re-segmentation Lambda infrastructure."""

from typing import Any

__all__ = ["ResegmentReceiptLambda", "create_resegment_receipt_lambda"]


def __getattr__(name: str) -> Any:
    """Load Pulumi-only objects lazily so handler tests stay lightweight."""
    if name not in __all__:
        raise AttributeError(name)
    from .infrastructure import (
        ResegmentReceiptLambda,
        create_resegment_receipt_lambda,
    )

    return {
        "ResegmentReceiptLambda": ResegmentReceiptLambda,
        "create_resegment_receipt_lambda": create_resegment_receipt_lambda,
    }[name]

"""Test helper functions for receipt_label tests."""

import uuid
from typing import Optional
from receipt_dynamo.entities import ReceiptWord


def create_test_receipt_word(
    text: str,
    image_id: Optional[str] = None,
    receipt_id: int = 1,
    line_id: int = 1,
    word_id: int = 1,
    x1: float = 100,
    y1: float = 100,
    x2: float = 200,
    y2: float = 120,
    confidence: float = 0.95,
    is_noise: bool = False,
    embedding_status: str = "NONE",
) -> ReceiptWord:
    """Create a ReceiptWord with simplified parameters for testing.

    This helper abstracts away the complex bounding box structure required
    by the actual ReceiptWord entity.
    """
    if image_id is None:
        image_id = str(uuid.uuid4())

    bounding_box = {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}

    top_left = {"x": x1, "y": y1}
    top_right = {"x": x2, "y": y1}
    bottom_left = {"x": x1, "y": y2}
    bottom_right = {"x": x2, "y": y2}

    return ReceiptWord(
        receipt_id=receipt_id,
        image_id=image_id,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box=bounding_box,
        top_left=top_left,
        top_right=top_right,
        bottom_left=bottom_left,
        bottom_right=bottom_right,
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=confidence,
        embedding_status=embedding_status,
        is_noise=is_noise,
    )

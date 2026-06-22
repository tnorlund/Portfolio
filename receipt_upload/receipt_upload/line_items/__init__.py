"""Line-item labeling: deterministic geometry + semantic (Chroma) recovery."""

from receipt_upload.line_items.reconstructor import (
    dedupe_grand_total,
    propose_line_item_labels,
    reclassify_mislabeled_totals,
)
from receipt_upload.line_items.semantic_proposer import propose_product_names

__all__ = [
    "dedupe_grand_total",
    "propose_line_item_labels",
    "propose_product_names",
    "reclassify_mislabeled_totals",
]

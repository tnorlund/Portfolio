"""Line-item labeling: deterministic geometry + semantic (Chroma) recovery."""

from receipt_upload.line_items.reconstructor import propose_line_item_labels
from receipt_upload.line_items.semantic_proposer import propose_product_names

__all__ = ["propose_line_item_labels", "propose_product_names"]

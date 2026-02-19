"""
Receipt combination utilities.

Functions for merging multiple receipt fragments into a single receipt:
geometry transforms, DynamoDB record creation, and metadata migration.
"""

from receipt_upload.combine.geometry_utils import (
    calculate_min_area_rect,
    create_warped_receipt_image,
    transform_point_to_warped_space,
)
from receipt_upload.combine.metadata_utils import (
    get_best_receipt_place,
    migrate_receipt_word_labels,
)
from receipt_upload.combine.records_builder import (
    combine_receipt_letters_to_image_coords,
    combine_receipt_words_to_image_coords,
    create_combined_receipt_records,
    create_receipt_letters_from_combined,
)

__all__ = [
    "calculate_min_area_rect",
    "create_warped_receipt_image",
    "transform_point_to_warped_space",
    "get_best_receipt_place",
    "migrate_receipt_word_labels",
    "combine_receipt_letters_to_image_coords",
    "combine_receipt_words_to_image_coords",
    "create_combined_receipt_records",
    "create_receipt_letters_from_combined",
]

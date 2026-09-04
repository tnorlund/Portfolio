"""Field change detection for update-relevant fields."""

from receipt_dynamo_stream.change_detection.detector import (
    UPDATE_RELEVANT_FIELDS,
    get_update_relevant_changes,
)

__all__ = ["UPDATE_RELEVANT_FIELDS", "get_update_relevant_changes"]

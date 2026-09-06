"""
Change detection for fields that drive downstream receipt updates.

The allowlist below decides which entity modifications fan out to the
summary / line-item update queues; changes to any other field are
ignored by the stream processor.
"""

from typing import Dict, Optional

from receipt_dynamo_stream.models import FieldChange, StreamEntity

UPDATE_RELEVANT_FIELDS = {
    "RECEIPT_PLACE": [
        "merchant_name",
        "merchant_category",
        "formatted_address",
        "phone_number",
        "place_id",
    ],
    "RECEIPT_WORD_LABEL": [
        "label",
        "reasoning",
        "validation_status",
        "label_proposed_by",
        "label_consolidated_from",
    ],
    # timestamp_computed only: the summary updater stamps a fresh value on
    # every write, and the nested ``summary`` dataclass is not JSON
    # serializable (it would crash the SQS publisher).
    "RECEIPT_SUMMARY": [
        "timestamp_computed",
    ],
    "RECEIPT_SECTION": [
        "section_type",
        "line_ids",
        "confidence",
        "validation_status",
    ],
}


def get_update_relevant_changes(
    entity_type: str,
    old_entity: Optional[StreamEntity],
    new_entity: Optional[StreamEntity],
) -> Dict[str, FieldChange]:
    """Identify changes to fields that trigger downstream updates."""
    fields_to_check = UPDATE_RELEVANT_FIELDS.get(entity_type, [])
    changes: Dict[str, FieldChange] = {}

    for field in fields_to_check:
        old_value = getattr(old_entity, field, None) if old_entity else None
        new_value = getattr(new_entity, field, None) if new_entity else None

        if old_value != new_value:
            changes[field] = FieldChange(old=old_value, new=new_value)

    return changes


__all__ = ["UPDATE_RELEVANT_FIELDS", "get_update_relevant_changes"]

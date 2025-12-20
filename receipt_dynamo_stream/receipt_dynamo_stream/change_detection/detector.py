"""
Change detection logic for ChromaDB-relevant fields.
"""

from typing import Dict, Optional

from receipt_dynamo_stream.models import FieldChange, StreamEntity

CHROMADB_RELEVANT_FIELDS = {
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
}


def get_chromadb_relevant_changes(
    entity_type: str,
    old_entity: Optional[StreamEntity],
    new_entity: Optional[StreamEntity],
) -> Dict[str, FieldChange]:
    """Identify changes to fields that affect ChromaDB metadata."""
    fields_to_check = CHROMADB_RELEVANT_FIELDS.get(entity_type, [])
    changes: Dict[str, FieldChange] = {}

    for field in fields_to_check:
        old_value = getattr(old_entity, field, None) if old_entity else None
        new_value = getattr(new_entity, field, None) if new_entity else None

        if old_value != new_value:
            changes[field] = FieldChange(old=old_value, new=new_value)

    return changes


__all__ = ["CHROMADB_RELEVANT_FIELDS", "get_chromadb_relevant_changes"]

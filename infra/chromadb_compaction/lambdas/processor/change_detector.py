"""
Change detection logic for ChromaDB-relevant fields.

Identifies which field changes require ChromaDB metadata updates.
"""

from typing import Dict, Optional, Union

from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

from .models import FieldChange

# Define ChromaDB-relevant fields for each entity type
CHROMADB_RELEVANT_FIELDS = {
    "RECEIPT_PLACE": [
        "canonical_merchant_name",
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
    old_entity: Optional[Union[ReceiptPlace, ReceiptWordLabel]],
    new_entity: Optional[Union[ReceiptPlace, ReceiptWordLabel]],
) -> Dict[str, FieldChange]:
    """
    Identify changes to fields that affect ChromaDB metadata.

    Uses typed entity objects for robust field access and comparison.

    Args:
        entity_type: Type of entity (RECEIPT_PLACE or RECEIPT_WORD_LABEL)
        old_entity: Previous entity state
        new_entity: Current entity state (None for REMOVE events)

    Returns:
        Dictionary mapping field names to FieldChange objects
    """
    fields_to_check = CHROMADB_RELEVANT_FIELDS.get(entity_type, [])
    changes = {}

    for field in fields_to_check:
        # Use getattr for safe attribute access on typed objects
        old_value = getattr(old_entity, field, None) if old_entity else None
        new_value = getattr(new_entity, field, None) if new_entity else None

        if old_value != new_value:
            changes[field] = FieldChange(old=old_value, new=new_value)

    return changes

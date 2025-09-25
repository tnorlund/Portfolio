from datetime import datetime, timezone
from typing import Dict, Any, Tuple

from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.constants import ValidationMethod


def metadata_from_places_candidate(
    key: Tuple[str, int], cand: Dict[str, Any]
) -> ReceiptMetadata:
    """Convert a Places candidate dict into ReceiptMetadata entity."""
    img, rec = key
    return ReceiptMetadata(
        image_id=str(img),
        receipt_id=int(rec),
        place_id=cand.get("place_id", ""),
        merchant_name=cand.get("name", ""),
        matched_fields=[f"google_places_{cand.get('reason', '')}"],
        timestamp=datetime.now(timezone.utc),
        merchant_category="",
        address=cand.get("address", ""),
        phone_number=cand.get("phone", ""),
        validated_by=ValidationMethod.INFERENCE.value,
        reasoning=f"Selected via {cand.get('source', '')}:{cand.get('reason', '')}",
        canonical_place_id=cand.get("place_id", ""),
        canonical_merchant_name=cand.get("name", ""),
        canonical_address=cand.get("address", ""),
        canonical_phone_number=cand.get("phone", ""),
        validation_status="",
    )

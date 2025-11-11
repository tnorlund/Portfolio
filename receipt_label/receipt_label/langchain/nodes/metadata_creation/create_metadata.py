"""Create ReceiptMetadata from Places API results."""

from typing import Dict, Any
from datetime import datetime, timezone

from receipt_label.langchain.state.metadata_creation import MetadataCreationState
from receipt_label.merchant_validation.metadata_builder import (
    build_receipt_metadata_from_result,
    build_receipt_metadata_from_result_no_match,
)


async def create_receipt_metadata(state: MetadataCreationState) -> Dict[str, Any]:
    """Create ReceiptMetadata from Places API results.

    Args:
        state: Current workflow state

    Returns:
        Dictionary with receipt_metadata and metadata_created flag
    """
    print(f"📝 Creating ReceiptMetadata...")

    image_id, receipt_id_str = state.receipt_id.split("/")
    receipt_id_int = int(receipt_id_str)
    client = state.dynamo_client

    try:
        # Check if we have a selected place from Places API
        if state.selected_place and state.selected_place.get("place_id"):
            # Create metadata from Places result
            print(f"   ✅ Creating metadata from Places API result")
            metadata = build_receipt_metadata_from_result(
                image_id=image_id,
                receipt_id=receipt_id_int,
                google_place=state.selected_place,
                gpt_result=None,
            )
        else:
            # No match found - create minimal metadata
            print(f"   ⚠️ No Places match found, creating minimal metadata")
            metadata = build_receipt_metadata_from_result_no_match(
                receipt_id=receipt_id_int,
                image_id=image_id,
                gpt_result=None,
            )

        # Save to DynamoDB
        client.add_receipt_metadatas([metadata])
        print(f"   ✅ Saved ReceiptMetadata to DynamoDB")
        print(f"      Place ID: {metadata.place_id}")
        print(f"      Merchant: {metadata.merchant_name}")

        return {
            "receipt_metadata": metadata,
            "metadata_created": True,
        }

    except Exception as e:
        print(f"   ❌ Error creating metadata: {e}")
        return {
            "receipt_metadata": None,
            "metadata_created": False,
            "error_message": str(e),
            "error_count": state.error_count + 1,
            "last_error": str(e),
        }


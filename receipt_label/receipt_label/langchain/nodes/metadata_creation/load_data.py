"""Load receipt data for metadata creation."""

from receipt_label.utils.text_reconstruction import ReceiptTextReconstructor
from receipt_label.langchain.state.metadata_creation import MetadataCreationState


async def load_receipt_data_for_metadata(state: MetadataCreationState) -> dict:
    """Loads and formats receipt data for metadata creation.

    Uses pre-fetched data if available (from initial state),
    otherwise fetches from DynamoDB.
    """
    print(f"📋 Loading receipt data for metadata creation: {state.receipt_id}")

    image_id, receipt_id_str = state.receipt_id.split("/")
    receipt_id_int = int(receipt_id_str)
    client = state.dynamo_client

    # Check if we already have lines and words pre-fetched
    if state.lines and state.words:
        # Use pre-fetched data
        print(f"   ✅ Using pre-fetched data: {len(state.lines)} lines, {len(state.words)} words")
        lines = state.lines
        words = state.words
    else:
        # Fetch from DynamoDB (fallback)
        print(f"   📊 Fetching from DynamoDB...")
        details = client.get_receipt_details(image_id, receipt_id_int)
        lines = details.lines
        words = details.words

    formatted_text, _ = ReceiptTextReconstructor().reconstruct_receipt(lines)

    return {
        "receipt_id": state.receipt_id,
        "image_id": image_id,
        "lines": lines,
        "words": words,
        "formatted_text": formatted_text,
        "dynamo_client": state.dynamo_client,
    }


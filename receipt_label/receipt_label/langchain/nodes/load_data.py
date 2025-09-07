from __future__ import annotations

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.utils.text_reconstruction import ReceiptTextReconstructor
from receipt_label.langchain.state.currency_validation import (
    CurrencyAnalysisState,
)


async def load_receipt_data(state: CurrencyAnalysisState) -> dict:
    """Loads and formats receipt data."""

    print(f"📋 Loading receipt data for {state.receipt_id}")

    image_id, receipt_id_str = state.receipt_id.split("/")
    receipt_id_int = int(receipt_id_str)
    client: DynamoClient = state.dynamo_client
    details = client.get_receipt_details(image_id, receipt_id_int)

    formatted_text, _ = ReceiptTextReconstructor().reconstruct_receipt(
        details.lines
    )

    return {
        "receipt_id": state.receipt_id,
        "image_id": image_id,
        "lines": details.lines,
        "words": details.words,
        "existing_word_labels": details.labels,
        "formatted_text": formatted_text,
        "dynamo_client": state.dynamo_client,
        "currency_labels": state.currency_labels,
        "line_item_labels": [],
        "discovered_labels": [],
        "confidence_score": 0.0,
        "processing_time": 0.0,
    }

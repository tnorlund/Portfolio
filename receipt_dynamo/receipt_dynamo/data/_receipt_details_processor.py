"""Functions for processing receipt details queries.

This module handles the complex logic of querying and processing receipt
details from GSI2, keeping the main _Receipt class focused on CRUD operations.
"""

from typing import Any, Dict, Optional

from receipt_dynamo.entities.receipt import item_to_receipt
from receipt_dynamo.entities.receipt_summary import (
    ReceiptSummary,
    ReceiptSummaryPage,
)
from receipt_dynamo.entities.receipt_word import item_to_receipt_word
from receipt_dynamo.entities.receipt_word_label import (
    item_to_receipt_word_label,
)


def process_receipt_details_query(
    client: Any,
    query_params: Dict[str, Any],
    limit: Optional[int],
) -> ReceiptSummaryPage:
    """Process GSI2 query response into receipt summaries.

    Args:
        client: DynamoDB client
        query_params: Query parameters for GSI2
        limit: Maximum number of receipts to return

    Returns:
        ReceiptSummaryPage with processed results
    """
    summaries: Dict[str, ReceiptSummary] = {}
    current_summary: Optional[ReceiptSummary] = None
    receipt_count = 0

    while True:
        response = client.query(**query_params)

        for item in response["Items"]:
            # Check if we should stop at receipt boundary
            if _should_stop_at_receipt_boundary(item, limit, receipt_count):
                return _create_receipt_summary_page(summaries, item)

            # Process item based on type
            result = _process_receipt_detail_item(item, summaries, current_summary)
            if result:
                current_summary = result.get("summary")
                if result.get("is_receipt"):
                    receipt_count += 1

        # Check for more pages
        if "LastEvaluatedKey" not in response:
            break

        query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    return ReceiptSummaryPage(summaries=summaries, last_evaluated_key=None)


def _should_stop_at_receipt_boundary(
    item: Dict[str, Any], limit: Optional[int], count: int
) -> bool:
    """Check if processing should stop at a receipt boundary."""
    if limit is None:
        return False
    item_type = item.get("TYPE", {}).get("S", "")
    return item_type == "RECEIPT" and count >= limit


def _process_receipt_detail_item(
    item: Dict[str, Any],
    summaries: Dict[str, ReceiptSummary],
    current: Optional[ReceiptSummary],
) -> Optional[Dict[str, Any]]:
    """Process a single item from the query."""
    item_type = item.get("TYPE", {}).get("S", "")

    if item_type == "RECEIPT":
        receipt = item_to_receipt(item)
        key = f"{receipt.image_id}_{receipt.receipt_id}"
        summary = ReceiptSummary(receipt=receipt, words=[], word_labels=[])
        summaries[key] = summary
        return {"summary": summary, "is_receipt": True}

    if item_type == "RECEIPT_WORD" and current:
        word = item_to_receipt_word(item)
        if _item_belongs_to_receipt(word, current.receipt):
            current.words.append(word)

    if item_type == "RECEIPT_WORD_LABEL" and current:
        label = item_to_receipt_word_label(item)
        if _item_belongs_to_receipt(label, current.receipt):
            current.word_labels.append(label)

    return None


def _item_belongs_to_receipt(item: Any, receipt: Any) -> bool:
    """Check if an item belongs to a specific receipt."""
    return bool(
        item.image_id == receipt.image_id and item.receipt_id == receipt.receipt_id
    )


def _create_receipt_summary_page(
    summaries: Dict[str, ReceiptSummary], item: Dict[str, Any]
) -> ReceiptSummaryPage:
    """Create a page when limit is reached."""
    return ReceiptSummaryPage(
        summaries=summaries,
        last_evaluated_key={
            "PK": item["PK"],
            "SK": item["SK"],
            "GSI2PK": item["GSI2PK"],
            "GSI2SK": item["GSI2SK"],
        },
    )

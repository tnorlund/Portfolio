"""Functions for processing receipt details queries.

This module handles the complex logic of querying and processing receipt
details from GSI2, keeping the main _Receipt class focused on CRUD operations.
"""

from typing import Any

from receipt_dynamo.entities.receipt import item_to_receipt
from receipt_dynamo.entities.receipt_bundle import (
    ReceiptBundle,
    ReceiptBundlePage,
)
from receipt_dynamo.entities.receipt_word import item_to_receipt_word
from receipt_dynamo.entities.receipt_word_label import (
    item_to_receipt_word_label,
)


def process_receipt_details_query(
    client: Any,
    query_params: dict[str, Any],
    limit: int | None,
) -> ReceiptBundlePage:
    """Process GSI2 query response into receipt bundles.

    Args:
        client: DynamoDB client
        query_params: Query parameters for GSI2
        limit: Maximum number of receipts to return

    Returns:
        ReceiptBundlePage with processed results
    """
    bundles: dict[str, ReceiptBundle] = {}
    current_bundle: ReceiptBundle | None = None
    receipt_count = 0

    while True:
        response = client.query(**query_params)

        for item in response["Items"]:
            # Check if we should stop at receipt boundary
            if _should_stop_at_receipt_boundary(item, limit, receipt_count):
                return _create_receipt_bundle_page(bundles, item)

            # Process item based on type
            result = _process_receipt_detail_item(
                item, bundles, current_bundle
            )
            if result:
                current_bundle = result.get("bundle")
                if result.get("is_receipt"):
                    receipt_count += 1

        # Check for more pages
        if "LastEvaluatedKey" not in response:
            break

        query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    return ReceiptBundlePage(bundles=bundles, last_evaluated_key=None)


def _should_stop_at_receipt_boundary(
    item: dict[str, Any], limit: int | None, count: int
) -> bool:
    """Check if processing should stop at a receipt boundary."""
    if limit is None:
        return False
    item_type = item.get("TYPE", {}).get("S", "")
    return item_type == "RECEIPT" and count >= limit


def _process_receipt_detail_item(
    item: dict[str, Any],
    bundles: dict[str, ReceiptBundle],
    current: ReceiptBundle | None,
) -> dict[str, Any] | None:
    """Process a single item from the query."""
    item_type = item.get("TYPE", {}).get("S", "")

    if item_type == "RECEIPT":
        receipt = item_to_receipt(item)
        key = f"{receipt.image_id}_{receipt.receipt_id}"
        bundle = ReceiptBundle(receipt=receipt, words=[], word_labels=[])
        bundles[key] = bundle
        return {"bundle": bundle, "is_receipt": True}

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
        item.image_id == receipt.image_id
        and item.receipt_id == receipt.receipt_id
    )


def _create_receipt_bundle_page(
    bundles: dict[str, ReceiptBundle], item: dict[str, Any]
) -> ReceiptBundlePage:
    """Create a page when limit is reached."""
    return ReceiptBundlePage(
        bundles=bundles,
        last_evaluated_key={
            "PK": item["PK"],
            "SK": item["SK"],
            "GSI2PK": item["GSI2PK"],
            "GSI2SK": item["GSI2SK"],
        },
    )

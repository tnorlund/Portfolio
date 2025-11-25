"""
DynamoDB tools for receipt metadata operations.

These tools enable the agent to read receipt data and metadata
from DynamoDB for validation purposes.
"""

import logging
from typing import Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class GetReceiptMetadataInput(BaseModel):
    """Input schema for get_receipt_metadata tool."""

    image_id: str = Field(description="UUID of the receipt image")
    receipt_id: int = Field(description="Receipt ID within the image")


class GetReceiptContextInput(BaseModel):
    """Input schema for get_receipt_context tool."""

    image_id: str = Field(description="UUID of the receipt image")
    receipt_id: int = Field(description="Receipt ID within the image")


class GetReceiptsByMerchantInput(BaseModel):
    """Input schema for get_receipts_by_merchant tool."""

    merchant_name: str = Field(description="Merchant name to search for")
    limit: int = Field(
        default=20,
        description="Maximum number of receipts to return",
        ge=1,
        le=100,
    )


@tool(args_schema=GetReceiptMetadataInput)
def get_receipt_metadata(
    image_id: str,
    receipt_id: int,
    # Injected at runtime
    _dynamo_client: Any = None,
) -> dict[str, Any]:
    """
    Retrieve the current ReceiptMetadata from DynamoDB.

    Use this tool to get the current merchant information stored
    for a receipt, including:
    - merchant_name, address, phone_number
    - place_id (Google Places ID)
    - validation_status
    - matched_fields and reasoning

    This is the starting point for validation - compare this against
    what you find in ChromaDB and Google Places.
    """
    if _dynamo_client is None:
        return {"error": "DynamoDB client not configured"}

    try:
        # Use receipt_dynamo client
        metadata = _dynamo_client.get_receipt_metadata(
            image_id=image_id,
            receipt_id=receipt_id,
        )

        if metadata is None:
            return {
                "found": False,
                "message": f"No metadata found for {image_id}#{receipt_id}",
            }

        return {
            "found": True,
            "image_id": metadata.image_id,
            "receipt_id": metadata.receipt_id,
            "merchant_name": metadata.merchant_name,
            "place_id": metadata.place_id,
            "address": metadata.address,
            "phone_number": metadata.phone_number,
            "merchant_category": metadata.merchant_category,
            "matched_fields": metadata.matched_fields,
            "validated_by": metadata.validated_by,
            "validation_status": metadata.validation_status,
            "reasoning": metadata.reasoning,
            "canonical_merchant_name": metadata.canonical_merchant_name,
            "canonical_place_id": metadata.canonical_place_id,
            "canonical_address": metadata.canonical_address,
            "canonical_phone_number": metadata.canonical_phone_number,
        }

    except Exception as e:
        logger.error(f"Error getting receipt metadata: {e}")
        return {"error": str(e)}


@tool(args_schema=GetReceiptContextInput)
def get_receipt_context(
    image_id: str,
    receipt_id: int,
    # Injected at runtime
    _dynamo_client: Any = None,
) -> dict[str, Any]:
    """
    Get the full context of a receipt including lines and words.

    Use this tool to understand what text is on the receipt.
    Returns:
    - Raw text lines from the receipt
    - Extracted data (addresses, phones, merchant names found)
    - Word-level details if needed for fine-grained validation

    This helps verify if the merchant metadata matches
    what's actually on the receipt.
    """
    if _dynamo_client is None:
        return {"error": "DynamoDB client not configured"}

    try:
        # Get receipt details
        image, receipt, words, lines, _, labels = _dynamo_client.get_receipt_details(
            image_id=image_id,
            receipt_id=receipt_id,
        )

        if receipt is None:
            return {
                "found": False,
                "message": f"Receipt {image_id}#{receipt_id} not found",
            }

        # Extract text lines
        raw_lines = []
        if lines:
            raw_lines = [
                {"line_id": ln.line_id, "text": ln.text}
                for ln in sorted(lines, key=lambda x: x.line_id)
            ]

        # Extract candidate merchant data from words
        extracted_data: dict[str, list[str]] = {
            "merchant_names": [],
            "addresses": [],
            "phones": [],
        }

        if words:
            for word in words:
                ext = getattr(word, "extracted_data", None) or {}
                data_type = (ext.get("type") or "").lower()

                if data_type == "merchant_name":
                    value = ext.get("value") or word.text
                    if value:
                        extracted_data["merchant_names"].append(value)
                elif data_type == "address":
                    value = ext.get("value") or word.text
                    if value:
                        extracted_data["addresses"].append(value)
                elif data_type == "phone":
                    value = ext.get("value") or word.text
                    if value:
                        extracted_data["phones"].append(value)

        # Get labels for context
        label_summary = {}
        if labels:
            for label in labels:
                label_type = label.label
                label_summary[label_type] = label_summary.get(label_type, 0) + 1

        return {
            "found": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "line_count": len(raw_lines),
            "word_count": len(words) if words else 0,
            "raw_lines": raw_lines[:30],  # Limit to first 30 lines
            "extracted_data": extracted_data,
            "label_summary": label_summary,
        }

    except Exception as e:
        logger.error(f"Error getting receipt context: {e}")
        return {"error": str(e)}


@tool(args_schema=GetReceiptsByMerchantInput)
def get_receipts_by_merchant(
    merchant_name: str,
    limit: int = 20,
    # Injected at runtime
    _dynamo_client: Any = None,
) -> dict[str, Any]:
    """
    Find all receipts associated with a merchant name in DynamoDB.

    Use this tool to discover other receipts from the same merchant.
    This helps validate consistency:
    - Do all receipts have the same place_id?
    - Are addresses consistent?
    - Are phone numbers consistent?

    Returns metadata for each receipt found.
    """
    if _dynamo_client is None:
        return {"error": "DynamoDB client not configured"}

    try:
        # Query using GSI on merchant name
        metadatas, _ = _dynamo_client.get_receipt_metadatas_by_merchant(
            merchant_name=merchant_name,
            limit=limit,
        )

        if not metadatas:
            return {
                "found": False,
                "merchant_name": merchant_name,
                "receipt_count": 0,
            }

        # Aggregate data
        place_ids: dict[str, int] = {}
        addresses: dict[str, int] = {}
        phones: dict[str, int] = {}
        validation_statuses: dict[str, int] = {}

        receipts = []
        for meta in metadatas:
            receipts.append({
                "image_id": meta.image_id,
                "receipt_id": meta.receipt_id,
                "place_id": meta.place_id,
                "validation_status": meta.validation_status,
            })

            if meta.place_id:
                place_ids[meta.place_id] = place_ids.get(meta.place_id, 0) + 1

            if meta.address:
                addresses[meta.address] = addresses.get(meta.address, 0) + 1

            if meta.phone_number:
                phones[meta.phone_number] = phones.get(meta.phone_number, 0) + 1

            if meta.validation_status:
                validation_statuses[meta.validation_status] = (
                    validation_statuses.get(meta.validation_status, 0) + 1
                )

        # Identify canonical values (most common)
        canonical_place_id = max(
            place_ids.items(), key=lambda x: x[1], default=(None, 0)
        )[0]

        # Check for inconsistencies
        inconsistencies = []
        if len(place_ids) > 1:
            inconsistencies.append(
                f"Multiple place_ids found: {list(place_ids.keys())}"
            )
        if len(addresses) > 3:  # Some variation is expected
            inconsistencies.append(
                f"High address variation: {len(addresses)} different addresses"
            )

        return {
            "found": True,
            "merchant_name": merchant_name,
            "receipt_count": len(metadatas),
            "receipts": receipts[:10],  # Limit detail output
            "place_ids": place_ids,
            "canonical_place_id": canonical_place_id,
            "addresses": dict(sorted(addresses.items(), key=lambda x: -x[1])[:5]),
            "phone_numbers": dict(sorted(phones.items(), key=lambda x: -x[1])[:5]),
            "validation_statuses": validation_statuses,
            "inconsistencies": inconsistencies,
        }

    except Exception as e:
        logger.error(f"Error getting receipts by merchant: {e}")
        return {"error": str(e)}


"""Receipt management tools."""

from typing import Optional
from core.client_manager import get_client_manager


def list_receipts(
    limit: Optional[int] = None,
    last_evaluated_key: Optional[dict] = None,
) -> dict:
    """List receipts from DynamoDB with pagination support.
    
    Args:
        limit: Maximum number of receipts to return (default: all)
        last_evaluated_key: Pagination key from previous query
        
    Returns:
        Dictionary containing:
        - success: Boolean indicating if operation succeeded
        - receipts: List of receipt dictionaries
        - last_evaluated_key: Pagination key for next query (if more results exist)
        - count: Number of receipts returned
    """
    manager = get_client_manager()

    try:
        # Use the existing DynamoClient from ClientManager
        dynamo_client = manager.dynamo

        # Call list_receipts method
        receipts, next_key = dynamo_client.list_receipts(
            limit=limit, last_evaluated_key=last_evaluated_key
        )

        # Convert Receipt objects to dictionaries
        receipt_dicts = []
        for receipt in receipts:
            receipt_dict = {
                "image_id": receipt.image_id,
                "receipt_id": receipt.receipt_id,
                "timestamp_added": receipt.timestamp_added.isoformat() if receipt.timestamp_added else None,
                "timestamp_completed": receipt.timestamp_completed.isoformat() if receipt.timestamp_completed else None,
                "status": receipt.status,
                "image_url": receipt.image_url,
                "label_count": receipt.label_count,
                "job_id": receipt.job_id,
                "batch_id": receipt.batch_id,
                "step": receipt.step,
            }
            # Add optional fields if they exist
            if hasattr(receipt, "merchant_name") and receipt.merchant_name:
                receipt_dict["merchant_name"] = receipt.merchant_name
            if hasattr(receipt, "total_amount") and receipt.total_amount:
                receipt_dict["total_amount"] = str(receipt.total_amount)
            if hasattr(receipt, "date") and receipt.date:
                receipt_dict["date"] = receipt.date

            receipt_dicts.append(receipt_dict)

        result = {
            "success": True,
            "receipts": receipt_dicts,
            "count": len(receipt_dicts),
        }

        # Add pagination key if there are more results
        if next_key:
            result["last_evaluated_key"] = next_key
            result["has_more"] = True
        else:
            result["has_more"] = False

        return result

    except Exception as e:
        return {"success": False, "error": str(e), "receipts": [], "count": 0}
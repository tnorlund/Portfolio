"""
Lambda handler for checking receipt metadata.

This handler verifies if a receipt has necessary metadata (merchant, location)
before proceeding with labeling.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB client
dynamodb = boto3.resource("dynamodb")
table_name = os.environ["DYNAMO_TABLE_NAME"]
table = dynamodb.Table(table_name)


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Check if receipt has necessary metadata for labeling.

    Args:
        event: Contains receipt_id
        context: Lambda context

    Returns:
        Dictionary with:
        - found: Boolean indicating if metadata exists
        - merchant_name: Merchant name if found
        - location: Location data if found
        - missing_fields: List of missing required fields
    """
    try:
        receipt_id = event["receipt_id"]
        logger.info("Checking metadata for receipt: %s", receipt_id)

        # Query for receipt metadata
        response = table.get_item(
            Key={"PK": f"RECEIPT#{receipt_id}", "SK": f"METADATA#{receipt_id}"}
        )

        result: Dict[str, Any] = {
            "found": False,
            "merchant_name": None,
            "location": None,
            "missing_fields": [],
        }

        if "Item" in response:
            item = response["Item"]
            result["found"] = True

            # Check for required fields
            if "merchant_name" in item:
                result["merchant_name"] = item["merchant_name"]
            else:
                missing_fields = result.get("missing_fields", [])
                if isinstance(missing_fields, list):
                    missing_fields.append("merchant_name")
                result["found"] = False

            if "location" in item:
                result["location"] = item["location"]
            else:
                missing_fields = result.get("missing_fields", [])
                if isinstance(missing_fields, list):
                    missing_fields.append("location")
                # Location is optional, so don't set found to False

            # Check if merchant has been validated
            if "merchant_validated" in item and not item["merchant_validated"]:
                result["found"] = False
                missing_fields = result.get("missing_fields", [])
                if isinstance(missing_fields, list):
                    missing_fields.append("merchant_validation")
        else:
            result["missing_fields"] = ["metadata_not_found"]

        logger.info("Metadata check result: %s", result)
        return result

    except Exception as e:
        logger.error("Error checking metadata: %s", str(e))
        raise

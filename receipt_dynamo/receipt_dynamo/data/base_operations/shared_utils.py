"""
Shared utility functions for DynamoDB operations.

This module contains common utility functions used across multiple
base operations classes to eliminate code duplication.
"""

import time
from typing import Any, Dict, Optional

from receipt_dynamo.data.shared_exceptions import EntityValidationError


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    """Validate that a LastEvaluatedKey has the required DynamoDB format.

    Args:
        lek: The LastEvaluatedKey dictionary to validate.

    Raises:
        EntityValidationError: If the key format is invalid or missing
            required fields.
    """
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise EntityValidationError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise EntityValidationError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


def validate_status_list_params(
    status: str,
    limit: Optional[int],
    last_evaluated_key: Optional[Dict[str, Any]],
) -> None:
    """
    Validate parameters for status-based listing methods.

    This eliminates duplicate validation code across data layer classes
    that have list_*_by_status methods.

    Args:
        status: The status string to filter by
        limit: Maximum number of items to return (or None)
        last_evaluated_key: Pagination key (or None)

    Raises:
        EntityValidationError: If any parameter is invalid
    """
    if not isinstance(status, str):
        raise EntityValidationError(
            f"status must be a string, got {type(status).__name__}"
        )
    if limit is not None and not isinstance(limit, int):
        raise EntityValidationError("limit must be an integer or None")
    if last_evaluated_key is not None and not isinstance(
        last_evaluated_key, dict
    ):
        raise EntityValidationError(
            "last_evaluated_key must be a dictionary or None"
        )


def validate_pagination_params(
    limit: Optional[int],
    last_evaluated_key: Optional[Dict[str, Any]],
    validate_attribute_format: bool = False,
) -> None:
    """
    Validate pagination parameters for DynamoDB queries.

    Args:
        limit: Maximum number of items to return
        last_evaluated_key: Key to start from for pagination
        validate_attribute_format: Whether to validate DynamoDB attribute
            format

    Raises:
        EntityValidationError: If parameters are invalid
    """
    if limit is not None:
        if not isinstance(limit, int):
            raise EntityValidationError("Limit must be an integer")
        if limit <= 0:
            raise EntityValidationError("Limit must be greater than 0")

    if last_evaluated_key is not None:
        if not isinstance(last_evaluated_key, dict):
            raise EntityValidationError(
                "LastEvaluatedKey must be a dictionary"
            )
        # Validate DynamoDB LastEvaluatedKey structure
        required_keys = {"PK", "SK"}
        if not required_keys.issubset(last_evaluated_key.keys()):
            raise EntityValidationError(
                f"LastEvaluatedKey must contain keys: {required_keys}"
            )

        # Optional: Validate proper DynamoDB attribute value format
        if validate_attribute_format:
            for key in required_keys:
                if (
                    not isinstance(last_evaluated_key[key], dict)
                    or "S" not in last_evaluated_key[key]
                ):
                    raise EntityValidationError(
                        f"LastEvaluatedKey[{key}] must be a dict "
                        f"containing a key 'S'"
                    )


def build_query_params(
    table_name: str,
    key_condition_expression: str,
    expression_attribute_values: Dict[str, Any],
    index_name: Optional[str] = None,
    expression_attribute_names: Optional[Dict[str, str]] = None,
    filter_expression: Optional[str] = None,
    exclusive_start_key: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    scan_index_forward: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Build query parameters for DynamoDB queries.

    Args:
        table_name: DynamoDB table name
        key_condition_expression: Key condition expression
        expression_attribute_values: Expression attribute values
        index_name: Optional GSI name
        expression_attribute_names: Optional attribute names mapping
        filter_expression: Optional filter expression
        exclusive_start_key: Optional start key for pagination
        limit: Optional query limit
        scan_index_forward: Optional sort order flag (True=ascending,
            False=descending)

    Returns:
        Dictionary of query parameters
    """
    query_params = {
        "TableName": table_name,
        "KeyConditionExpression": key_condition_expression,
        "ExpressionAttributeValues": expression_attribute_values,
    }

    if index_name:
        query_params["IndexName"] = index_name
    if expression_attribute_names:
        query_params["ExpressionAttributeNames"] = expression_attribute_names
    if filter_expression:
        query_params["FilterExpression"] = filter_expression
    if exclusive_start_key:
        query_params["ExclusiveStartKey"] = exclusive_start_key
    if limit:
        query_params["Limit"] = limit
    if scan_index_forward is not None:
        query_params["ScanIndexForward"] = scan_index_forward

    return query_params


def build_get_item_key(
    primary_key: str, sort_key: str
) -> Dict[str, Dict[str, str]]:
    """
    Build a DynamoDB key for get_item operations.

    Args:
        primary_key: Primary key value
        sort_key: Sort key value

    Returns:
        DynamoDB key dictionary
    """
    return {
        "PK": {"S": primary_key},
        "SK": {"S": sort_key},
    }


def validate_receipt_field_params(
    receipt_id,
    image_id,
    field_name,
) -> None:
    """
    Validate common parameters for receipt field-based operations.

    Args:
        receipt_id: The receipt ID (must be int)
        image_id: The image ID (must be str)
        field_name: The field name (must be str)

    Raises:
        EntityValidationError: If any parameter is invalid
    """
    if not isinstance(receipt_id, int):
        raise EntityValidationError(
            f"receipt_id must be an integer, got {type(receipt_id).__name__}"
        )
    if not isinstance(image_id, str):
        raise EntityValidationError(
            f"image_id must be a string, got {type(image_id).__name__}"
        )
    if not isinstance(field_name, str):
        raise EntityValidationError(
            f"field_name must be a string, got {type(field_name).__name__}"
        )


def validate_batch_get_keys(
    keys,
    entity_type: str,
    pk_prefix: str = "IMAGE#",
    sk_prefix: str = "LINE#",
) -> None:
    """
    Validate keys for batch get operations.

    Args:
        keys: List of key dictionaries to validate
        entity_type: The expected entity type in the SK (e.g., "LETTER", "WORD")
        pk_prefix: Expected prefix for PK (default: "IMAGE#")
        sk_prefix: Expected prefix for SK (default: "LINE#")

    Raises:
        EntityValidationError: If any key is invalid
    """
    for key in keys:
        if not {"PK", "SK"}.issubset(key.keys()):
            raise EntityValidationError("Keys must contain 'PK' and 'SK'")
        if not key["PK"]["S"].startswith(pk_prefix):
            raise EntityValidationError(f"PK must start with '{pk_prefix}'")
        if not key["SK"]["S"].startswith(sk_prefix):
            raise EntityValidationError(f"SK must start with '{sk_prefix}'")
        if key["SK"]["S"].split("#")[-2] != entity_type:
            raise EntityValidationError(f"SK must contain '{entity_type}'")


def batch_write_with_retry(
    client,
    table_name: str,
    request_items,
    max_retries: int = 3,
    initial_backoff: float = 0.1,
) -> None:
    """
    Perform batch write with automatic retry for unprocessed items.

    Args:
        client: DynamoDB client
        table_name: DynamoDB table name
        request_items: List of write request items
        max_retries: Maximum number of retries for unprocessed items
        initial_backoff: Initial backoff time in seconds
    """
    # Format request items for DynamoDB
    formatted_items = {table_name: request_items}
    backoff = initial_backoff

    for attempt in range(max_retries + 1):
        response = client.batch_write_item(RequestItems=formatted_items)

        unprocessed_items = response.get("UnprocessedItems", {})
        if not unprocessed_items:
            break

        if attempt < max_retries:
            time.sleep(backoff)
            backoff *= 2  # Exponential backoff
            formatted_items = unprocessed_items
        else:
            # Final attempt failed, log unprocessed items
            raise RuntimeError(
                f"Failed to process all items after {max_retries} retries"
            )


def batch_write_with_retry_dict(
    client,
    request_items,
    max_retries: int = 3,
    initial_backoff: float = 0.1,
) -> None:
    """
    Perform batch write with automatic retry for unprocessed items (dict
    format).

    Args:
        client: DynamoDB client
        request_items: Dict of table name to list of write request items
        max_retries: Maximum number of retries for unprocessed items
        initial_backoff: Initial backoff time in seconds
    """
    backoff = initial_backoff

    for attempt in range(max_retries + 1):
        response = client.batch_write_item(RequestItems=request_items)

        unprocessed_items = response.get("UnprocessedItems", {})
        if not unprocessed_items:
            break

        if attempt < max_retries:
            time.sleep(backoff)
            backoff *= 2  # Exponential backoff
            request_items = unprocessed_items
        else:
            # Final attempt failed, log unprocessed items
            raise RuntimeError(
                f"Failed to process all items after {max_retries} retries"
            )

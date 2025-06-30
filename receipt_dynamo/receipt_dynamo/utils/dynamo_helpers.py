"""Shared DynamoDB utility functions to reduce code duplication."""

import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, TypeVar

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

from botocore.exceptions import ClientError

# Type variable for entities
T = TypeVar("T", bound="DynamoEntity")


class DynamoEntity(Protocol):
    """Protocol for DynamoDB entities that can be converted to items."""

    def to_item(self) -> Dict[str, Any]:
        """Convert entity to DynamoDB item format."""
        ...


def validate_last_evaluated_key(lek: dict) -> None:
    """
    Validate the structure of a DynamoDB LastEvaluatedKey.

    Args:
        lek: The LastEvaluatedKey dict to validate

    Raises:
        ValueError: If the key structure is invalid
    """
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise ValueError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise ValueError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


def batch_write_items(
    client: "DynamoDBClient",
    table_name: str,
    items: List[T],
    chunk_size: int = 25,
) -> None:
    """
    Write items to DynamoDB in batches with automatic retry for unprocessed items.

    Args:
        client: The DynamoDB client
        table_name: Name of the DynamoDB table
        items: List of items implementing the DynamoEntity protocol
        chunk_size: Maximum items per batch (DynamoDB limit is 25)

    Raises:
        Exception: If items cannot be written after retries
    """
    for i in range(0, len(items), chunk_size):
        batch = items[i : i + chunk_size]
        request_items = {
            table_name: [
                {"PutRequest": {"Item": item.to_item()}} for item in batch
            ]
        }

        # Retry logic for unprocessed items
        max_retries = 3
        retry_count = 0

        while request_items and retry_count < max_retries:
            try:
                response = client.batch_write_item(RequestItems=request_items)

                # Check for unprocessed items
                if (
                    "UnprocessedItems" in response
                    and response["UnprocessedItems"]
                ):
                    request_items = response["UnprocessedItems"]
                    retry_count += 1
                    # Exponential backoff
                    time.sleep(2**retry_count)
                else:
                    # All items processed successfully
                    break

            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "ProvisionedThroughputExceededException":
                    retry_count += 1
                    if retry_count >= max_retries:
                        # Re-raise the original exception after max retries
                        raise
                    time.sleep(2**retry_count)
                else:
                    raise Exception(f"Error in batch write: {e}")

        if request_items and retry_count >= max_retries:
            raise Exception(
                f"Failed to write {len(request_items[table_name])} items after {max_retries} retries"
            )


def batch_get_items(
    client: "DynamoDBClient",
    table_name: str,
    keys: List[Dict[str, Any]],
    chunk_size: int = 100,
) -> List[Dict[str, Any]]:
    """
    Get items from DynamoDB in batches with automatic retry for unprocessed keys.

    Args:
        client: The DynamoDB client
        table_name: Name of the DynamoDB table
        keys: List of primary keys to fetch
        chunk_size: Maximum keys per batch (DynamoDB limit is 100)

    Returns:
        List of items retrieved from DynamoDB

    Raises:
        Exception: If items cannot be retrieved after retries
    """
    all_items = []

    for i in range(0, len(keys), chunk_size):
        batch_keys = keys[i : i + chunk_size]
        request_items = {table_name: {"Keys": batch_keys}}

        # Retry logic for unprocessed keys
        max_retries = 3
        retry_count = 0

        while request_items and retry_count < max_retries:
            try:
                response = client.batch_get_item(RequestItems=request_items)

                # Collect items from this batch
                if table_name in response.get("Responses", {}):
                    all_items.extend(response["Responses"][table_name])

                # Check for unprocessed keys
                if (
                    "UnprocessedKeys" in response
                    and response["UnprocessedKeys"]
                ):
                    request_items = response["UnprocessedKeys"]
                    retry_count += 1
                    # Exponential backoff
                    time.sleep(2**retry_count)
                else:
                    # All keys processed successfully
                    break

            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "ProvisionedThroughputExceededException":
                    retry_count += 1
                    time.sleep(2**retry_count)
                else:
                    raise Exception(f"Error in batch get: {e}")

        if request_items and retry_count >= max_retries:
            raise Exception(
                f"Failed to get {len(request_items[table_name]['Keys'])} items after {max_retries} retries"
            )

    return all_items


def create_key(pk: str, sk: str) -> Dict[str, Dict[str, str]]:
    """
    Create a DynamoDB key structure.

    Args:
        pk: Partition key value
        sk: Sort key value

    Returns:
        Dict with PK and SK in DynamoDB format
    """
    return {"PK": {"S": pk}, "SK": {"S": sk}}


def handle_conditional_check_failed(
    error: ClientError, entity_type: str, entity_id: str
) -> None:
    """
    Handle ConditionalCheckFailedException with appropriate error message.

    Args:
        error: The ClientError exception
        entity_type: Type of entity (e.g., "Job", "User")
        entity_id: ID of the entity

    Raises:
        ValueError: With a descriptive message for the specific entity
    """
    error_code = error.response.get("Error", {}).get("Code", "")
    if error_code == "ConditionalCheckFailedException":
        raise ValueError(f"{entity_type} with ID {entity_id} already exists")
    raise error


def build_update_expression(
    attributes: Dict[str, Any], exclude_keys: Optional[List[str]] = None
) -> tuple[str, Dict[str, Any], Dict[str, str]]:
    """
    Build DynamoDB UpdateExpression, ExpressionAttributeValues, and ExpressionAttributeNames.

    Args:
        attributes: Dictionary of attributes to update
        exclude_keys: List of keys to exclude from update (e.g., PK, SK)

    Returns:
        Tuple of (UpdateExpression, ExpressionAttributeValues, ExpressionAttributeNames)
    """
    if exclude_keys is None:
        exclude_keys = ["PK", "SK", "TYPE"]

    update_parts = []
    expression_values = {}
    expression_names = {}

    for key, value in attributes.items():
        if key not in exclude_keys and value is not None:
            placeholder = f"#{key}"
            value_placeholder = f":{key}"

            update_parts.append(f"{placeholder} = {value_placeholder}")
            expression_values[value_placeholder] = value
            expression_names[placeholder] = key

    update_expression = "SET " + ", ".join(update_parts)

    return update_expression, expression_values, expression_names

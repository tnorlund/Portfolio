from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
else:
    # Runtime fallback
    DynamoDBClient = object


class DynamoClientProtocol(Protocol):
    """Protocol defining attributes shared by DynamoDB mixin classes."""

    table_name: str
    _client: DynamoDBClient

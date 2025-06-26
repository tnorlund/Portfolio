from typing import Protocol

from mypy_boto3_dynamodb import DynamoDBClient


class DynamoClientProtocol(Protocol):
    """Protocol defining attributes shared by DynamoDB mixin classes."""

    table_name: str
    _client: DynamoDBClient

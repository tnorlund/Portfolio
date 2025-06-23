from typing import Protocol

import boto3


class DynamoClientProtocol(Protocol):
    """Protocol defining attributes shared by DynamoDB mixin classes."""

    table_name: str
    _client: boto3.client

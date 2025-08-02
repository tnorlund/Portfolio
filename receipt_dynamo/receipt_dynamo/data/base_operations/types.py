"""
Type definitions for DynamoDB operations.

This module provides type imports from boto3-stubs for type checking
during development while maintaining runtime compatibility.
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import (
        AttributeValueTypeDef,
        BatchGetItemInputTypeDef,
        BatchWriteItemInputTypeDef,
        ConditionCheckTypeDef,
        DeleteItemInputTypeDef,
        DeleteRequestTypeDef,
        DeleteTypeDef,
        GetItemInputTypeDef,
        KeysAndAttributesTypeDef,
        PutItemInputTypeDef,
        PutRequestTypeDef,
        PutTypeDef,
        QueryInputTypeDef,
        ScanInputTypeDef,
        TransactWriteItemsInputTypeDef,
        TransactWriteItemTypeDef,
        UpdateItemInputTypeDef,
        UpdateTypeDef,
        WriteRequestTypeDef,
    )
else:
    # Runtime fallback when dev dependencies aren't installed
    DynamoDBClient = object
    QueryInputTypeDef = dict
    GetItemInputTypeDef = dict
    PutItemInputTypeDef = dict
    DeleteItemInputTypeDef = dict
    BatchWriteItemInputTypeDef = dict
    TransactWriteItemsInputTypeDef = dict
    UpdateItemInputTypeDef = dict
    BatchGetItemInputTypeDef = dict
    ScanInputTypeDef = dict
    WriteRequestTypeDef = dict
    PutRequestTypeDef = dict
    DeleteRequestTypeDef = dict
    TransactWriteItemTypeDef = dict
    KeysAndAttributesTypeDef = dict
    AttributeValueTypeDef = dict
    PutTypeDef = dict
    DeleteTypeDef = dict
    UpdateTypeDef = dict
    ConditionCheckTypeDef = dict


class DynamoClientProtocol(Protocol):
    """Protocol defining the interface for DynamoDB client implementations."""

    table_name: str
    _client: DynamoDBClient


# Re-export all types for convenience
__all__ = [
    # Client types
    "DynamoDBClient",
    "DynamoClientProtocol",
    # Input types
    "QueryInputTypeDef",
    "GetItemInputTypeDef",
    "PutItemInputTypeDef",
    "DeleteItemInputTypeDef",
    "BatchWriteItemInputTypeDef",
    "TransactWriteItemsInputTypeDef",
    "UpdateItemInputTypeDef",
    "BatchGetItemInputTypeDef",
    "ScanInputTypeDef",
    # Request types
    "WriteRequestTypeDef",
    "PutRequestTypeDef",
    "DeleteRequestTypeDef",
    "TransactWriteItemTypeDef",
    "KeysAndAttributesTypeDef",
    "AttributeValueTypeDef",
    # Operation types
    "PutTypeDef",
    "DeleteTypeDef",
    "UpdateTypeDef",
    "ConditionCheckTypeDef",
]

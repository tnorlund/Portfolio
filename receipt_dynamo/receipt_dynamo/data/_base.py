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
    # Runtime fallback
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
    """Protocol defining attributes shared by DynamoDB mixin classes."""

    table_name: str
    _client: DynamoDBClient

"""
TypedDict definitions for DynamoDB stream processing.

Provides type-safe structures for DynamoDB stream events, records,
and Lambda responses to reduce usage of `Any` throughout the codebase.
"""

from typing import Literal, Mapping, Protocol, TypedDict

# =============================================================================
# DynamoDB Attribute Value Types
# =============================================================================


class AttributeValueS(TypedDict):
    """DynamoDB String attribute value."""

    S: str


class AttributeValueN(TypedDict):
    """DynamoDB Number attribute value (stored as string)."""

    N: str


class AttributeValueB(TypedDict):
    """DynamoDB Binary attribute value (base64 encoded)."""

    B: str


class AttributeValueSS(TypedDict):
    """DynamoDB String Set attribute value."""

    SS: list[str]


class AttributeValueNS(TypedDict):
    """DynamoDB Number Set attribute value."""

    NS: list[str]


class AttributeValueBS(TypedDict):
    """DynamoDB Binary Set attribute value."""

    BS: list[str]


class AttributeValueBOOL(TypedDict):
    """DynamoDB Boolean attribute value."""

    BOOL: bool


class AttributeValueNULL(TypedDict):
    """DynamoDB Null attribute value."""

    NULL: bool


class AttributeValueL(TypedDict):
    """DynamoDB List attribute value."""

    L: list["AttributeValue"]


class AttributeValueM(TypedDict):
    """DynamoDB Map attribute value."""

    M: dict[str, "AttributeValue"]


# Union of all possible DynamoDB attribute value types
AttributeValue = (
    AttributeValueS
    | AttributeValueN
    | AttributeValueB
    | AttributeValueSS
    | AttributeValueNS
    | AttributeValueBS
    | AttributeValueBOOL
    | AttributeValueNULL
    | AttributeValueL
    | AttributeValueM
)

# A DynamoDB item is a mapping of attribute names to attribute values.
# Using dict[str, dict[str, object]] to support the common
# .get("field", {}).get("S") pattern.
DynamoDBItem = dict[str, dict[str, object]]


# =============================================================================
# DynamoDB Stream Record Types
# =============================================================================


class DynamoDBKeys(TypedDict):
    """Primary key attributes from a DynamoDB stream record."""

    PK: AttributeValueS
    SK: AttributeValueS


class StreamRecordDynamoDB(TypedDict, total=False):
    """The 'dynamodb' portion of a DynamoDB stream record."""

    Keys: DynamoDBKeys
    NewImage: DynamoDBItem
    OldImage: DynamoDBItem
    SequenceNumber: str
    SizeBytes: int
    StreamViewType: Literal[
        "KEYS_ONLY", "NEW_IMAGE", "OLD_IMAGE", "NEW_AND_OLD_IMAGES"
    ]
    ApproximateCreationDateTime: int


class DynamoDBStreamRecord(TypedDict, total=False):
    """A single record from a DynamoDB stream event."""

    eventID: str
    eventName: Literal["INSERT", "MODIFY", "REMOVE"]
    eventVersion: str
    eventSource: Literal["aws:dynamodb"]
    awsRegion: str
    dynamodb: StreamRecordDynamoDB
    eventSourceARN: str
    userIdentity: dict[str, str]


class DynamoDBStreamEvent(TypedDict):
    """DynamoDB stream event passed to Lambda handlers."""

    Records: list[DynamoDBStreamRecord]


# =============================================================================
# Lambda Response Types
# =============================================================================


class StreamProcessorResponseData(TypedDict, total=False):
    """Response data from the stream processor Lambda."""

    statusCode: int
    processed_records: int
    queued_messages: int
    error: str


class APIGatewayHeaders(TypedDict, total=False):
    """HTTP headers for API Gateway responses."""

    # Using NotRequired for optional headers
    # Standard headers
    Content__Type: str  # Note: Actual key is "Content-Type"


class APIGatewayResponse(TypedDict):
    """API Gateway-compatible Lambda response."""

    statusCode: int
    body: str
    headers: dict[str, str]


# =============================================================================
# Lambda Context Protocol (for type hints)
# =============================================================================


class LambdaContext(Protocol):  # pylint: disable=too-few-public-methods
    """
    Protocol for AWS Lambda context object.

    Note: This is a simplified version. The actual context has more
    attributes, but these are the commonly used ones.
    """

    function_name: str
    function_version: str
    invoked_function_arn: str
    memory_limit_in_mb: int
    aws_request_id: str
    log_group_name: str
    log_stream_name: str

    def get_remaining_time_in_millis(self) -> int:
        """Get remaining execution time in milliseconds."""


# =============================================================================
# Metrics Protocol
# =============================================================================


class MetricsRecorder(Protocol):  # pylint: disable=too-few-public-methods
    """Minimal protocol for metrics clients."""

    def count(
        self,
        name: str,
        value: int,
        dimensions: Mapping[str, str] | None = None,
    ) -> object:
        """Record a count metric."""
        return None


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Attribute value types
    "AttributeValue",
    "AttributeValueS",
    "AttributeValueN",
    "AttributeValueB",
    "AttributeValueSS",
    "AttributeValueNS",
    "AttributeValueBS",
    "AttributeValueBOOL",
    "AttributeValueNULL",
    "AttributeValueL",
    "AttributeValueM",
    "DynamoDBItem",
    # Stream record types
    "DynamoDBKeys",
    "StreamRecordDynamoDB",
    "DynamoDBStreamRecord",
    "DynamoDBStreamEvent",
    # Response types
    "StreamProcessorResponseData",
    "APIGatewayResponse",
    # Context
    "LambdaContext",
    # Metrics
    "MetricsRecorder",
]

"""Expected SQS message outputs for contract testing."""

# Expected structure for RECEIPT_METADATA messages
EXPECTED_METADATA_MESSAGE_SCHEMA = {
    "source": "dynamodb_stream",
    "entity_type": "RECEIPT_METADATA",
    "entity_data": {
        "entity_type": "RECEIPT_METADATA",
        "image_id": str,  # UUID string
        "receipt_id": int,
    },
    "changes": {
        # Field changes with old/new structure
        # Example: "canonical_merchant_name": {"old": "value1", "new": "value2"}
    },
    "event_name": str,  # "MODIFY" or "REMOVE"
    "timestamp": str,  # ISO format datetime
    "stream_record_id": str,
    "aws_region": str,
}

# Expected structure for RECEIPT_WORD_LABEL messages
EXPECTED_WORD_LABEL_MESSAGE_SCHEMA = {
    "source": "dynamodb_stream",
    "entity_type": "RECEIPT_WORD_LABEL",
    "entity_data": {
        "entity_type": "RECEIPT_WORD_LABEL",
        "image_id": str,
        "receipt_id": int,
        "line_id": int,
        "word_id": int,
        "label": str,
    },
    "changes": {
        # Field changes for word labels
        # Example: "validation_status": {"old": "PENDING", "new": "VALID"}
    },
    "event_name": str,
    "timestamp": str,
    "stream_record_id": str,
    "aws_region": str,
}

# Expected structure for COMPACTION_RUN messages
EXPECTED_COMPACTION_RUN_MESSAGE_SCHEMA = {
    "source": "dynamodb_stream",
    "entity_type": "COMPACTION_RUN",
    "entity_data": {
        "run_id": str,
        "image_id": str,
        "receipt_id": int,
        "lines_delta_prefix": str,  # or words_delta_prefix
        "words_delta_prefix": str,  # or lines_delta_prefix
        "delta_s3_prefix": str,  # Collection-specific prefix
    },
    "changes": {},  # Empty for INSERT events
    "event_name": "INSERT",
    "timestamp": str,
    "stream_record_id": str,
    "aws_region": str,
}

# Required fields for all messages
REQUIRED_MESSAGE_FIELDS = [
    "source",
    "entity_type",
    "entity_data",
    "changes",
    "event_name",
    "timestamp",
    "stream_record_id",
    "aws_region",
]

# Required SQS message attributes
REQUIRED_MESSAGE_ATTRIBUTES = {
    "source": {"StringValue": "dynamodb_stream", "DataType": "String"},
    "entity_type": {"StringValue": str, "DataType": "String"},
    "event_name": {"StringValue": str, "DataType": "String"},
    "collection": {
        "StringValue": str,
        "DataType": "String",
    },  # "lines" or "words"
}

# Collection targeting rules
COLLECTION_TARGETING_RULES = {
    "RECEIPT_METADATA": ["lines", "words"],  # Affects both collections
    "RECEIPT_WORD_LABEL": ["words"],  # Only affects words
    "COMPACTION_RUN": ["lines", "words"],  # Separate message per collection
}

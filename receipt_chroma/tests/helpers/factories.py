"""Factory functions for creating test data."""

from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, Optional
from unittest.mock import MagicMock
from uuid import uuid4

from receipt_dynamo_stream.models import FieldChange, StreamMessage

from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel


def create_place_message(
    image_id: str = "test-image-id",
    receipt_id: int = 1,
    event_name: str = "MODIFY",
    changes: Optional[Dict[str, FieldChange]] = None,
    collections: tuple = (ChromaDBCollection.LINES, ChromaDBCollection.WORDS),
) -> StreamMessage:
    """Create a RECEIPT_PLACE StreamMessage for testing.

    Args:
        image_id: Image identifier
        receipt_id: Receipt identifier
        event_name: Event type (INSERT, MODIFY, REMOVE)
        changes: Dictionary of field changes
        collections: Target collections for the message

    Returns:
        StreamMessage for RECEIPT_PLACE entity
    """
    if changes is None:
        changes = {
            "merchant_name": FieldChange(
                old="Old Merchant", new="New Merchant"
            ),
        }

    return StreamMessage(
        entity_type="RECEIPT_PLACE",
        entity_data={
            "image_id": image_id,
            "receipt_id": receipt_id,
        },
        changes=changes,
        event_name=event_name,
        collections=collections,
        timestamp=datetime.now().isoformat(),
        stream_record_id=f"record-{image_id}-{receipt_id}",
        aws_region="us-east-1",
    )


def create_label_message(
    image_id: str = "test-image-id",
    receipt_id: int = 1,
    line_id: int = 1,
    word_id: int = 1,
    label: str = "TOTAL",
    event_name: str = "MODIFY",
    changes: Optional[Dict[str, FieldChange]] = None,
    record_snapshot: Optional[Dict[str, Any]] = None,
) -> StreamMessage:
    """Create a RECEIPT_WORD_LABEL StreamMessage for testing.

    Args:
        image_id: Image identifier
        receipt_id: Receipt identifier
        line_id: Line identifier
        word_id: Word identifier
        label: Label text
        event_name: Event type (INSERT, MODIFY, REMOVE)
        changes: Dictionary of field changes
        record_snapshot: Optional snapshot of the entity state from DynamoDB

    Returns:
        StreamMessage for RECEIPT_WORD_LABEL entity
    """
    if changes is None:
        changes = {
            "validation_status": FieldChange(old="PENDING", new="VALID"),
        }

    # Create a record snapshot with the label entity if not provided
    if record_snapshot is None:
        # Use a valid UUID if the image_id is a test string
        valid_image_id = image_id if image_id.count("-") == 4 else str(uuid4())
        label_entity = ReceiptWordLabel(
            image_id=valid_image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            label=label,
            reasoning=None,
            timestamp_added=datetime.now().isoformat(),
            validation_status="VALID" if event_name == "MODIFY" else None,
        )
        record_snapshot = asdict(label_entity)

    return StreamMessage(
        entity_type="RECEIPT_WORD_LABEL",
        entity_data={
            "image_id": image_id,
            "receipt_id": receipt_id,
            "line_id": line_id,
            "word_id": word_id,
            "label": label,
        },
        changes=changes,
        event_name=event_name,
        collections=(ChromaDBCollection.WORDS,),
        timestamp=datetime.now().isoformat(),
        stream_record_id=f"record-{image_id}-{receipt_id}-{line_id}-{word_id}",
        aws_region="us-east-1",
        record_snapshot=record_snapshot,
    )


def create_compaction_run_message(
    image_id: str = "test-image-id",
    receipt_id: int = 1,
    run_id: str = "run-123",
    delta_s3_prefix: str = "s3://test-bucket/deltas/test-prefix/",
    event_name: str = "INSERT",
    collection: ChromaDBCollection = ChromaDBCollection.LINES,
) -> StreamMessage:
    """Create a COMPACTION_RUN StreamMessage for testing.

    Args:
        image_id: Image identifier
        receipt_id: Receipt identifier
        run_id: Compaction run identifier
        delta_s3_prefix: S3 prefix for delta files
        event_name: Event type (INSERT, MODIFY, REMOVE)
        collection: Target collection

    Returns:
        StreamMessage for COMPACTION_RUN entity
    """
    return StreamMessage(
        entity_type="COMPACTION_RUN",
        entity_data={
            "image_id": image_id,
            "receipt_id": receipt_id,
            "run_id": run_id,
            "delta_s3_prefix": delta_s3_prefix,
        },
        changes={},
        event_name=event_name,
        collections=(collection,),
        timestamp=datetime.now().isoformat(),
        stream_record_id=f"record-{image_id}-{receipt_id}-{run_id}",
        aws_region="us-east-1",
    )


def create_mock_logger() -> MagicMock:
    """Create a mock logger for testing.

    Returns:
        MagicMock logger with standard logging methods
    """
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def create_mock_metrics() -> MagicMock:
    """Create a mock metrics collector for testing.

    Returns:
        MagicMock metrics object with standard metric methods
    """
    metrics = MagicMock()
    metrics.count = MagicMock()
    metrics.gauge = MagicMock()
    metrics.timer = MagicMock()
    return metrics


def create_receipt_lines_in_dynamodb(
    dynamo_client: Any, image_id: str, receipt_id: int, num_lines: int = 2
) -> None:
    """Create receipt lines in DynamoDB for testing.

    Args:
        dynamo_client: DynamoDB client instance
        image_id: Image ID
        receipt_id: Receipt ID
        num_lines: Number of lines to create
    """
    from receipt_dynamo import ReceiptLine

    for line_id in range(1, num_lines + 1):
        line = ReceiptLine(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            text=f"Line {line_id}",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.2},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )
        dynamo_client.add_receipt_line(line)


def create_receipt_words_in_dynamodb(
    dynamo_client: Any,
    image_id: str,
    receipt_id: int,
    line_id: int,
    num_words: int = 2,
) -> None:
    """Create receipt words in DynamoDB for testing.

    Args:
        dynamo_client: DynamoDB client instance
        image_id: Image ID
        receipt_id: Receipt ID
        line_id: Line ID
        num_words: Number of words to create
    """
    from receipt_dynamo import ReceiptWord

    for word_id in range(1, num_words + 1):
        word = ReceiptWord(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            text=f"Word{word_id}",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.4, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.3},
            bottom_right={"x": 0.4, "y": 0.3},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )
        dynamo_client.add_receipt_word(word)

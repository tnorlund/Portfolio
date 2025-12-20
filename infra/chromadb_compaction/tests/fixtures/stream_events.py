"""Sample DynamoDB stream events for testing stream processor."""

import copy
from datetime import datetime

from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.compaction_run import CompactionRun
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

# ============================================================================
# RECEIPT_PLACE EVENTS
# ============================================================================

old_place = ReceiptPlace(
    formatted_address="30740 Russell Ranch Rd, Westlake Village, CA 91362, USA",
    reasoning="Target store at Russell Ranch location with matching phone number.",
    merchant_name="Target",
    canonical_phone_number="818-661-2631",
    merchant_category="Retail",
    matched_fields=["phone_number"],
    validated_by="TEXT_SEARCH",
    canonical_address="30740 Russell Ranch Rd, Westlake Village, CA 91362",
    canonical_place_id="ChIJbcsNQ2wk6IARlmoF-6DlBuE",
    phone_number="818-661-2631",
    validation_status="UNSURE",
    canonical_merchant_name="30740 Russell Ranch Rd (Westlake Village)",
    image_id="7e2bd911-7afb-4e0a-84de-57f51ce4daff",
    receipt_id=1,
    place_id="ChIJbcsNQ2wk6IARlmoF-6DlBuE",
    timestamp=datetime.fromisoformat("2025-08-08T03:53:53.200541+00:00"),
)

new_place = copy.deepcopy(old_place)
new_place.canonical_merchant_name = "Target"

TARGET_PLACE_UPDATE_EVENT = {
    "Records": [
        {
            "eventID": "16a9dd5e2f0213450b6df980241160f0",
            "eventName": "MODIFY",
            "eventVersion": "1.1",
            "eventSource": "aws:dynamodb",
            "awsRegion": "us-east-1",
            "dynamodb": {
                "ApproximateCreationDateTime": 1755754670.0,
                "Keys": {
                    "SK": {"S": "RECEIPT#00001#PLACE"},
                    "PK": {"S": "IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff"},
                },
                "NewImage": new_place.to_item(),
                "OldImage": old_place.to_item(),
                "SequenceNumber": "977119800000398701107540001",
                "SizeBytes": 1786,
                "StreamViewType": "NEW_AND_OLD_IMAGES",
            },
            "eventSourceARN": (
                "arn:aws:dynamodb:us-east-1:681647709217:table/"
                "ReceiptsTable-dc5be22/stream/2025-08-20T22:56:02.645"
            ),
        }
    ]
}

# Keep old name for backward compatibility
TARGET_METADATA_UPDATE_EVENT = TARGET_PLACE_UPDATE_EVENT


def get_target_event_variation(canonical_merchant_name: str = "Target"):
    """Get a variation of the Target event with different canonical_merchant_name."""
    event = copy.deepcopy(TARGET_PLACE_UPDATE_EVENT)
    if "NewImage" in event["Records"][0]["dynamodb"]:
        event["Records"][0]["dynamodb"]["NewImage"]["canonical_merchant_name"][
            "S"
        ] = canonical_merchant_name
    return event


# ============================================================================
# RECEIPT_WORD_LABEL EVENTS
# ============================================================================

old_word_label = ReceiptWordLabel(
    image_id="7e2bd911-7afb-4e0a-84de-57f51ce4daff",
    receipt_id=1,
    line_id=5,
    word_id=10,
    label="PRODUCT",
    reasoning="Appears to be a product name based on position",
    timestamp_added=datetime.fromisoformat("2025-08-08T03:53:53.200541+00:00"),
    validation_status="PENDING",
)

new_word_label = ReceiptWordLabel(
    image_id="7e2bd911-7afb-4e0a-84de-57f51ce4daff",
    receipt_id=1,
    line_id=5,
    word_id=10,
    label="MERCHANT_NAME",
    reasoning="Updated - this is actually the merchant name",
    timestamp_added=datetime.fromisoformat("2025-08-08T03:53:53.200541+00:00"),
    validation_status="VALID",
)

WORD_LABEL_UPDATE_EVENT = {
    "Records": [
        {
            "eventID": "word-label-update-1",
            "eventName": "MODIFY",
            "eventVersion": "1.1",
            "eventSource": "aws:dynamodb",
            "awsRegion": "us-east-1",
            "dynamodb": {
                "ApproximateCreationDateTime": 1755754670.0,
                "Keys": {
                    "PK": {"S": "IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff"},
                    "SK": {
                        "S": "RECEIPT#00001#LINE#00005#WORD#00010#LABEL#MERCHANT_NAME"
                    },
                },
                "OldImage": old_word_label.to_item(),
                "NewImage": new_word_label.to_item(),
                "SequenceNumber": "977119800000398701107540002",
                "SizeBytes": 512,
                "StreamViewType": "NEW_AND_OLD_IMAGES",
            },
            "eventSourceARN": (
                "arn:aws:dynamodb:us-east-1:681647709217:table/"
                "ReceiptsTable-dc5be22/stream/2025-08-20T22:56:02.645"
            ),
        }
    ]
}

WORD_LABEL_REMOVE_EVENT = {
    "Records": [
        {
            "eventID": "word-label-remove-1",
            "eventName": "REMOVE",
            "eventVersion": "1.1",
            "eventSource": "aws:dynamodb",
            "awsRegion": "us-east-1",
            "dynamodb": {
                "ApproximateCreationDateTime": 1755754670.0,
                "Keys": {
                    "PK": {"S": "IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff"},
                    "SK": {
                        "S": "RECEIPT#00001#LINE#00005#WORD#00010#LABEL#PRODUCT"
                    },
                },
                "OldImage": old_word_label.to_item(),
                "SequenceNumber": "977119800000398701107540003",
                "SizeBytes": 256,
                "StreamViewType": "NEW_AND_OLD_IMAGES",
            },
            "eventSourceARN": (
                "arn:aws:dynamodb:us-east-1:681647709217:table/"
                "ReceiptsTable-dc5be22/stream/2025-08-20T22:56:02.645"
            ),
        }
    ]
}


def get_word_label_event_variation(
    label: str = "TOTAL",
    validation_status: str = "VALID",
    event_name: str = "MODIFY",
) -> dict:
    """Get a variation of the word label event with different values."""
    old_label = ReceiptWordLabel(
        image_id="7e2bd911-7afb-4e0a-84de-57f51ce4daff",
        receipt_id=1,
        line_id=5,
        word_id=10,
        label="PRODUCT",
        reasoning="Original classification",
        timestamp_added=datetime.fromisoformat(
            "2025-08-08T03:53:53.200541+00:00"
        ),
        validation_status="PENDING",
    )

    new_label = ReceiptWordLabel(
        image_id="7e2bd911-7afb-4e0a-84de-57f51ce4daff",
        receipt_id=1,
        line_id=5,
        word_id=10,
        label=label,
        reasoning="Updated classification",
        timestamp_added=datetime.fromisoformat(
            "2025-08-08T03:53:53.200541+00:00"
        ),
        validation_status=validation_status,
    )

    event = {
        "Records": [
            {
                "eventID": f"word-label-{event_name.lower()}-custom",
                "eventName": event_name,
                "eventVersion": "1.1",
                "eventSource": "aws:dynamodb",
                "awsRegion": "us-east-1",
                "dynamodb": {
                    "ApproximateCreationDateTime": 1755754670.0,
                    "Keys": {
                        "PK": {
                            "S": "IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff"
                        },
                        "SK": {
                            "S": f"RECEIPT#00001#LINE#00005#WORD#00010#LABEL#{label}"
                        },
                    },
                    "SequenceNumber": "977119800000398701107540999",
                    "SizeBytes": 512,
                    "StreamViewType": "NEW_AND_OLD_IMAGES",
                },
                "eventSourceARN": (
                    "arn:aws:dynamodb:us-east-1:681647709217:table/"
                    "ReceiptsTable-dc5be22/stream/2025-08-20T22:56:02.645"
                ),
            }
        ]
    }

    if event_name == "MODIFY":
        event["Records"][0]["dynamodb"]["OldImage"] = old_label.to_item()
        event["Records"][0]["dynamodb"]["NewImage"] = new_label.to_item()
    elif event_name == "REMOVE":
        event["Records"][0]["dynamodb"]["OldImage"] = old_label.to_item()

    return event


# ============================================================================
# COMPACTION_RUN EVENTS
# ============================================================================

compaction_run = CompactionRun(
    run_id="550e8400-e29b-41d4-a716-446655440001",  # Valid UUID
    image_id="7e2bd911-7afb-4e0a-84de-57f51ce4daff",
    receipt_id=1,
    lines_delta_prefix="s3://test-bucket/deltas/lines/550e8400-e29b-41d4-a716-446655440001",
    words_delta_prefix="s3://test-bucket/deltas/words/550e8400-e29b-41d4-a716-446655440001",
    lines_state="PENDING",
    words_state="PENDING",
    lines_merged_vectors=0,
    words_merged_vectors=0,
    created_at="2025-08-08T03:53:53.200541+00:00",
)

COMPACTION_RUN_INSERT_EVENT = {
    "Records": [
        {
            "eventID": "compaction-run-insert-1",
            "eventName": "INSERT",
            "eventVersion": "1.1",
            "eventSource": "aws:dynamodb",
            "awsRegion": "us-east-1",
            "dynamodb": {
                "ApproximateCreationDateTime": 1755754670.0,
                "Keys": {
                    "PK": {"S": "IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff"},
                    "SK": {
                        "S": "RECEIPT#00001#COMPACTION_RUN#550e8400-e29b-41d4-a716-446655440001"
                    },
                },
                "NewImage": compaction_run.to_item(),
                "SequenceNumber": "977119800000398701107540004",
                "SizeBytes": 768,
                "StreamViewType": "NEW_AND_OLD_IMAGES",
            },
            "eventSourceARN": (
                "arn:aws:dynamodb:us-east-1:681647709217:table/"
                "ReceiptsTable-dc5be22/stream/2025-08-20T22:56:02.645"
            ),
        }
    ]
}


def get_compaction_run_event_variation(
    run_id: str = "550e8400-e29b-41d4-a716-446655440002",  # Valid UUID default
    image_id: str = "7e2bd911-7afb-4e0a-84de-57f51ce4daff",
    receipt_id: int = 1,
) -> dict:
    """Get a variation of the compaction run event with different values.

    Note: run_id must be a valid UUIDv4 string.
    """
    run = CompactionRun(
        run_id=run_id,
        image_id=image_id,
        receipt_id=receipt_id,
        lines_delta_prefix=f"s3://test-bucket/deltas/lines/{run_id}",
        words_delta_prefix=f"s3://test-bucket/deltas/words/{run_id}",
        lines_state="PENDING",
        words_state="PENDING",
        lines_merged_vectors=0,
        words_merged_vectors=0,
        created_at="2025-08-08T03:53:53.200541+00:00",
    )

    return {
        "Records": [
            {
                "eventID": f"compaction-run-insert-{run_id}",
                "eventName": "INSERT",
                "eventVersion": "1.1",
                "eventSource": "aws:dynamodb",
                "awsRegion": "us-east-1",
                "dynamodb": {
                    "ApproximateCreationDateTime": 1755754670.0,
                    "Keys": {
                        "PK": {"S": f"IMAGE#{image_id}"},
                        "SK": {
                            "S": f"RECEIPT#{receipt_id:05d}#COMPACTION_RUN#{run_id}"
                        },
                    },
                    "NewImage": run.to_item(),
                    "SequenceNumber": "977119800000398701107540999",
                    "SizeBytes": 768,
                    "StreamViewType": "NEW_AND_OLD_IMAGES",
                },
                "eventSourceARN": (
                    "arn:aws:dynamodb:us-east-1:681647709217:table/"
                    "ReceiptsTable-dc5be22/stream/2025-08-20T22:56:02.645"
                ),
            }
        ]
    }

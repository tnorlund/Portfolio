"""Test data for ChromaDB compaction tests."""

import copy
from datetime import datetime

from receipt_dynamo.entities import ReceiptMetadata

old_metadata = ReceiptMetadata(
    address="30740 Russell Ranch Rd, Westlake Village, CA 91362, USA",
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
    canonical_merchant_name="30740 Russell Ranch Rd (Westlake Village)",  # Wrong address as merchant name
    image_id="7e2bd911-7afb-4e0a-84de-57f51ce4daff",
    receipt_id=1,
    place_id="ChIJbcsNQ2wk6IARlmoF-6DlBuE",
    timestamp=datetime.fromisoformat("2025-08-08T03:53:53.200541+00:00"),
)

new_metadata = copy.deepcopy(old_metadata)
new_metadata.canonical_merchant_name = "Target"  # Corrected merchant name

# Your specific Target metadata update event
TARGET_METADATA_UPDATE_EVENT = {
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
                    "SK": {"S": "RECEIPT#00001#METADATA"},
                    "PK": {"S": "IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff"},
                },
                "NewImage": new_metadata.to_item(),
                "OldImage": old_metadata.to_item(),
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


# Helper function to get variations of your specific event
def get_target_event_variation(canonical_merchant_name: str = "Target"):
    """Get a variation of the Target event with different canonical_merchant_name.

    The base event already has:
    - OldImage: canonical_merchant_name = "30740 Russell Ranch Rd (Westlake Village)" (problematic)
    - NewImage: canonical_merchant_name = "Target" (correct)

    This function allows you to test different corrected values in NewImage.
    """
    event = copy.deepcopy(TARGET_METADATA_UPDATE_EVENT)

    # Only update NewImage with the corrected canonical_merchant_name
    if "NewImage" in event["Records"][0]["dynamodb"]:
        event["Records"][0]["dynamodb"]["NewImage"]["canonical_merchant_name"][
            "S"
        ] = canonical_merchant_name

    return event

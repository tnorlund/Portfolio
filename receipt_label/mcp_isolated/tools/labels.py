"""Label validation and management tools."""

from datetime import datetime
from core.client_manager import get_client_manager
from receipt_label.constants import CORE_LABELS
from receipt_dynamo.entities import item_to_receipt_word_label, ReceiptWordLabel


def validate_label(label: str) -> dict:
    """Check if a label is valid according to CORE_LABELS."""
    if label in CORE_LABELS:
        return {
            "valid": True,
            "label": label,
            "description": CORE_LABELS[label],
        }
    else:
        return {
            "valid": False,
            "label": label,
            "message": f"Invalid label. Valid labels are: {', '.join(sorted(CORE_LABELS.keys()))}",
        }


def get_receipt_labels(image_id: str, receipt_id: int) -> dict:
    """Get all labels for a receipt from DynamoDB."""
    manager = get_client_manager()

    try:
        response = manager.dynamo._client.query(
            TableName=manager.config.dynamo_table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            FilterExpression="attribute_exists(#type) AND #type = :type",
            ExpressionAttributeNames={"#type": "TYPE"},
            ExpressionAttributeValues={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk": {"S": f"RECEIPT#{receipt_id:05d}#"},
                ":type": {"S": "RECEIPT_WORD_LABEL"},
            },
        )

        labels = []
        for item in response.get("Items", []):
            try:
                label_entity = item_to_receipt_word_label(item)
                labels.append(
                    {
                        "word_id": label_entity.word_id,
                        "label": label_entity.label,
                        "line_id": label_entity.line_id,
                        "validation_status": label_entity.validation_status,
                    }
                )
            except:
                pass

        return {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "label_count": len(labels),
            "labels": labels,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "labels": []}


def save_label(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    label: str,
    reasoning: str = "Manual validation",
) -> dict:
    """Save a single label to DynamoDB."""
    manager = get_client_manager()

    try:
        label_entity = ReceiptWordLabel(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            label=label,
            reasoning=reasoning,
            timestamp_added=datetime.now(),
            validation_status="VALID",
            label_proposed_by="mcp_validation",
        )

        manager.dynamo._client.put_item(
            TableName=manager.config.dynamo_table,
            Item=label_entity.to_item(),
        )

        return {
            "success": True,
            "saved": {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "word_id": word_id,
                "label": label,
                "reasoning": reasoning,
            },
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
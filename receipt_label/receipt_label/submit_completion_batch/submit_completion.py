from receipt_dynamo.entities import ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus

from receipt_label.utils import get_clients

dynamo_client, openai_client, _ = get_clients()


def list_labels_that_need_validation() -> list[ReceiptWordLabel]:
    """
    List all receipt word labels that need validation.
    """
    labels, _ = dynamo_client.getReceiptWordLabelsByValidationStatus(
        validation_status=ValidationStatus.NONE.value
    )
    return labels


def chunk_into_completion_batches(
    labels: list[ReceiptWordLabel],
) -> dict[str, dict[int, list[ReceiptWordLabel]]]:
    """
    Chunk the labels into completion batches by image and receipt.
    """
    labels_by_image: dict[str, list[ReceiptWordLabel]] = {}
    for label in labels:
        image_dict = labels_by_image.setdefault(label.image_id, {})
        receipt_dict = image_dict.setdefault(label.receipt_id, [])
        # Use (line_id, word_id) as key to dedupe
        key = (label.line_id, label.word_id)
        receipt_dict[key] = label

    # Convert inner dicts back to lists
    result: dict[str, dict[int, list[ReceiptWordLabel]]] = {}
    for image_id, receipt_map in labels_by_image.items():
        result[image_id] = {}
    return result


def serialize_labels(
    label_receipt_dict: dict[str, dict[int, list[ReceiptWordLabel]]],
) -> list[dict]:
    """
    Serialize the labels into a list of dicts.
    """
    results: list[dict] = []
    for image_id, receipts in label_receipt_dict.items():
        for receipt_id, labels in receipts.items():
            # Serialize each label as JSON (using its __dict__)
            ndjson_lines = [json.dumps(label.__dict__) for label in labels]
            ndjson_content = "\n".join(ndjson_lines)
            # Write to a unique NDJSON file
            filepath = Path(f"/tmp/{image_id}_{receipt_id}_{uuid4()}.ndjson")
            with filepath.open("w") as f:
                f.write(ndjson_content)
            # Keep metadata about which receipt this file represents
            results.append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "ndjson_path": filepath,
                }
            )
    return results

import json
from pathlib import Path
from uuid import uuid4

from receipt_dynamo.entities import (
    ReceiptWordLabel,
    ReceiptWord,
    ReceiptLine,
    ReceiptMetadata,
    BatchSummary,
)
from openai.types import FileObject
from openai.resources.batches import Batch
from receipt_dynamo.constants import ValidationStatus
from datetime import datetime, timezone

from receipt_label.utils import get_clients

dynamo_client, openai_client, _ = get_clients()

CORE_LABELS = [
    # Merchant & store info
    "MERCHANT_NAME",
    "STORE_HOURS",
    "PHONE_NUMBER",
    "WEBSITE",
    "LOYALTY_ID",
    # Location/address (either as one line or broken out)
    "ADDRESS_LINE",  # or, for finer breakdown:
    # "ADDRESS_NUMBER",
    # "STREET_NAME",
    # "CITY",
    # "STATE",
    # "POSTAL_CODE",
    # Transaction info
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "DISCOUNT",  # if you want to distinguish coupons vs. generic discounts
    # Line‑item fields
    "PRODUCT_NAME",  # or ITEM_NAME
    "QUANTITY",  # or ITEM_QUANTITY
    "UNIT_PRICE",  # or ITEM_PRICE
    "LINE_TOTAL",  # or ITEM_TOTAL
    # Totals & taxes
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",  # or TOTAL
]


def generate_completion_batch_id() -> str:
    """Generate a unique batch ID."""
    return str(uuid4())


def get_receipt_details(image_id: str, receipt_id: int) -> tuple[
    list[ReceiptLine],
    list[ReceiptWord],
    ReceiptMetadata,
]:
    """Get the receipt details for an image and receipt."""
    (
        _,
        lines,
        words,
        _,
        _,
        _,
    ) = dynamo_client.getReceiptDetails(image_id, receipt_id)
    metadata = dynamo_client.getReceiptMetadata(image_id, receipt_id)
    return lines, words, metadata


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


def upload_serialized_labels(
    serialized_labels: list[dict], s3_bucket: str, prefix="labels"
) -> list[dict]:
    """Upload the serialized labels to S3."""
    s3 = boto3.client("s3")
    for receipt_dict in serialized_labels:
        key = f"{prefix}/{Path(receipt_dict['ndjson_path']).name}"
        s3.upload_file(
            str(receipt_dict["ndjson_path"]),
            s3_bucket,
            key,
        )
        receipt_dict["s3_key"] = key
        receipt_dict["s3_bucket"] = s3_bucket
    return serialized_labels


def download_serialized_labels(serialized_label: dict) -> Path:
    """Download the serialized label from S3."""
    s3 = boto3.client("s3")
    s3.download_file(
        serialized_label["s3_bucket"],
        serialized_label["s3_key"],
        serialized_label["ndjson_path"],
    )
    return Path(serialized_label["ndjson_path"])


def deserialize_labels(filepath: Path) -> list[ReceiptWordLabel]:
    """Deserialize an NDJSON file containing serialized ReceiptWordLabels."""
    with filepath.open("r") as f:
        return [ReceiptWordLabel(**json.loads(line)) for line in f]


def query_receipt_words(image_id: str, receipt_id: int) -> list[ReceiptWord]:
    """Query the ReceiptWords from DynamoDB."""
    (
        _,
        _,
        words,
        _,
        _,
        _,
    ) = dynamo_client.getReceiptDetails(image_id, receipt_id)
    return words


def _prompt_receipt_text(word: ReceiptWord, lines: list[ReceiptLine]) -> str:
    """Format the receipt text for the prompt."""
    if word.line_id == lines[0].line_id:
        prompt_receipt = lines[0].text
        prompt_receipt = prompt_receipt.replace(
            word.text, f"<TARGET>{word.text}</TARGET>"
        )
    else:
        prompt_receipt = lines[0].text

    for index in range(1, len(lines)):
        previous_line = lines[index - 1]
        current_line = lines[index]
        if current_line.line_id == word.line_id:
            # Replace the word in the line text with <TARGET>text</TARGET>
            line_text = current_line.text
            line_text = line_text.replace(
                word.text, f"<TARGET>{word.text}</TARGET>"
            )
        else:
            line_text = current_line.text
        current_line_centroid = current_line.calculate_centroid()
        if (
            current_line_centroid[1] < previous_line.top_left["y"]
            and current_line_centroid[1] > previous_line.bottom_left["y"]
        ):
            prompt_receipt += f" {line_text}"
        else:
            prompt_receipt += f"\n{line_text}"
    return prompt_receipt


def _validation_prompt(
    word: ReceiptWord,
    lines: list[ReceiptLine],
    label: ReceiptWordLabel,
    metadata: ReceiptMetadata,
) -> str:
    prompt = f'You are confirming the label for the word: {word.text} on the receipt from "{metadata.merchant_name}"'
    prompt += f'\nThis "{metadata.merchant_name}" location is located at {metadata.address}'
    prompt += f"\nI've marked the word with <TARGET>...</TARGET>"
    prompt += f"\nThe receipt is as follows:"
    prompt += f"\n--------------------------------"
    prompt += _prompt_receipt_text(word, lines)
    prompt += f"\n--------------------------------"
    prompt += f"\nThe label you are confirming is: {label.label}"
    prompt += f'\nThe allowed labels are: {", ".join(CORE_LABELS)}'
    prompt += f"\nIf the label is correct, return is_valid = true."
    prompt += f"\nIf it is incorrect and you are confident in a better label from the allowed list, return is_valid = false and return correct_label and rationale."
    prompt += f"\nIf you are not confident in any better label from the allowed list, return is_valid = false only. Do not guess. Leave correct_label and rationale blank."
    return prompt


def format_batch_completion_file(
    lines: list[ReceiptLine],
    words: list[ReceiptWord],
    labels: list[ReceiptWordLabel],
    metadata: ReceiptMetadata,
) -> Path:
    """Format the batch completion file name."""
    filepath = Path(
        f"/tmp/{metadata.image_id}_{metadata.receipt_id}_{uuid4()}.ndjson"
    )
    batch_lines: list[dict] = []
    for label in labels:
        word = next(
            w
            for w in words
            if w.line_id == label.line_id and w.word_id == label.word_id
        )
        if word is None:
            raise ValueError(f"Word not found for label: {label}")
        prompt = _validation_prompt(word, lines, label, metadata)
        batch_line = {
            "method": "POST",
            "custom_id": (
                f"IMAGE#{metadata.image_id}#"
                f"RECEIPT#{metadata.receipt_id:05d}#"
                f"LINE#{label.line_id:05d}#"
                f"WORD#{label.word_id:05d}#"
                f"LABEL#{label.label}#"
                f"VALIDATION_STATUS#{label.validation_status}"
            ),
            "url": "/v1/chat/completions",
            "body": {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are validating OCR labels from receipts.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "functions": [
                    {
                        "name": "validate_label",
                        "description": (
                            "Decide whether a token's proposed label is "
                            "correct, and if not, suggest the correct label "
                            "with a brief rationale."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "is_valid": {
                                    "type": "boolean",
                                    "description": (
                                        "True if the proposed label is "
                                        "correct for the token, else False."
                                    ),
                                },
                                "correct_label": {
                                    "type": "string",
                                    "description": (
                                        "Only return if is_valid is false and "
                                        "you are confident the word should be "
                                        "labeled with one of the allowed labels. "
                                        "Do not return this if unsure."
                                    ),
                                    "enum": CORE_LABELS,
                                },
                                "rationale": {
                                    "type": "string",
                                    "description": (
                                        "Only return if is_valid is false and "
                                        "you are confident in the correct label. "
                                        "Explain briefly why the word fits the "
                                        "suggested label."
                                    ),
                                },
                            },
                            "required": ["is_valid"],
                        },
                    }
                ],
            },
        }
        batch_lines.append(batch_line)
    with filepath.open("w") as f:
        for batch_line in batch_lines:
            f.write(json.dumps(batch_line) + "\n")
    return filepath


def upload_to_openai(filepath: Path) -> FileObject:
    """Upload the NDJSON file to OpenAI."""
    return openai_client.files.create(
        file=filepath.open("rb"), purpose="batch"
    )


def submit_openai_batch(file_id: str) -> Batch:
    """Submit a batch completion job to OpenAI using the uploaded file."""
    return openai_client.batches.create(
        input_file_id=file_id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )


def create_batch_summary(
    batch_id: str, open_ai_batch_id: str, file_path: str
) -> BatchSummary:
    """Create a summary of the batch."""
    # 1) Initialize counters and refs
    receipt_refs: set[tuple[str, int]] = set()
    word_count = 0

    # 2) Read and parse each line of the NDJSON file
    with open(file_path, "r") as f:
        for line in f:
            word_count += 1
            try:
                obj = json.loads(line)
                custom_id = obj.get("custom_id", "")
                parts = custom_id.split("#")
                # parts: ["IMAGE", image_id, "RECEIPT", receipt_id, ...]
                image_id = parts[1]
                receipt_id = int(parts[3])
                receipt_refs.add((image_id, receipt_id))
            except Exception:
                continue

    # 3) Build and return the BatchSummary
    return BatchSummary(
        batch_id=batch_id,
        batch_type="VALIDATION",
        openai_batch_id=open_ai_batch_id,
        submitted_at=datetime.now(timezone.utc),
        status="PENDING",
        word_count=word_count,
        result_file_id="N/A",
        receipt_refs=list(receipt_refs),
    )


def add_batch_summary(summary: BatchSummary) -> None:
    """Write the BatchSummary entity to DynamoDB."""
    dynamo_client.addBatchSummary(summary)


def update_label_validation_status(labels: list[ReceiptWordLabel]) -> None:
    """Update the validation status of the labels."""
    for label in labels:
        label.validation_status = ValidationStatus.PENDING.value
    dynamo_client.updateReceiptWordLabels(labels)

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple
from uuid import uuid4

import boto3
from openai.resources.batches import Batch
from openai.types import FileObject
from receipt_dynamo.constants import BatchStatus, BatchType, ValidationStatus
from receipt_dynamo.entities import (
    BatchSummary,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)

from receipt_label.completion._format_prompt import _format_prompt, functions
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager


def generate_completion_batch_id() -> str:
    """Generate a unique batch ID."""
    return str(uuid4())


def get_receipt_details(
    image_id: str, receipt_id: int, client_manager: ClientManager = None
) -> tuple[
    list[ReceiptLine],
    list[ReceiptWord],
    ReceiptMetadata,
    list[ReceiptWordLabel],
]:
    """Get the receipt details for an image and receipt."""
    if client_manager is None:
        client_manager = get_client_manager()
    (
        _,
        lines,
        words,
        _,
        _,
        labels,
    ) = client_manager.dynamo.getReceiptDetails(
        image_id, receipt_id
    )  # type: ignore
    metadata = client_manager.dynamo.getReceiptMetadata(image_id, receipt_id)
    return lines, words, metadata, labels  # type: ignore


def list_labels_that_need_validation(
    client_manager: ClientManager = None,
) -> list[ReceiptWordLabel]:
    """
    List all receipt word labels that need validation.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    labels, _ = client_manager.dynamo.getReceiptWordLabelsByValidationStatus(
        validation_status=ValidationStatus.NONE.value
    )
    return labels


def chunk_into_completion_batches(
    labels: list[ReceiptWordLabel],
) -> dict[str, dict[int, list[ReceiptWordLabel]]]:
    """
    Group labels by ``image_id`` -> ``receipt_id`` while **deduplicating**
    on the triple ``(line_id, word_id, label)`` for each receipt.

    Returned structure::

        {
            "IMAGE_UUID": {
                1: [ReceiptWordLabel, ReceiptWordLabel, ...],
                2: [...],
            },
            ...
        }
    """
    # First pass – nest dictionaries so we can easily dedupe
    nested: dict[
        str, dict[int, dict[tuple[int, int, str], ReceiptWordLabel]]
    ] = {}

    for label in labels:
        image_map = nested.setdefault(label.image_id, {})
        receipt_map = image_map.setdefault(label.receipt_id, {})
        # Use (line_id, word_id, label) as key to keep each label per word
        deduplicate_key = (label.line_id, label.word_id, label.label)
        receipt_map[deduplicate_key] = (
            label  # later entries overwrite duplicates
        )

    grouped: dict[str, dict[int, list[ReceiptWordLabel]]] = {}
    for image_id, receipt_dict in nested.items():
        grouped[image_id] = {
            receipt_id: list(label_map.values())
            for receipt_id, label_map in receipt_dict.items()
        }

    return grouped


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
            with filepath.open("w", encoding="utf-8") as f:
                f.write(ndjson_content)
            # Keep metadata about which receipt this file represents
            results.append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "ndjson_path": str(filepath),
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
    s3_bucket = serialized_label["s3_bucket"]
    s3_key = serialized_label["s3_key"]
    filepath = serialized_label["ndjson_path"]
    s3.download_file(
        s3_bucket,
        s3_key,
        str(filepath),
    )
    return Path(filepath)


def deserialize_labels(filepath: Path) -> list[ReceiptWordLabel]:
    """Deserialize an NDJSON file containing serialized ReceiptWordLabels."""
    with filepath.open("r") as f:
        return [ReceiptWordLabel(**json.loads(line)) for line in f]


def query_receipt_words(
    image_id: str, receipt_id: int, client_manager: ClientManager = None
) -> list[ReceiptWord]:
    """Query the ReceiptWords from DynamoDB."""
    if client_manager is None:
        client_manager = get_client_manager()
    (
        _,
        _,
        words,
        _,
        _,
        _,
    ) = client_manager.dynamo.getReceiptDetails(
        image_id, receipt_id
    )  # type: ignore
    return words  # type: ignore


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


def split_first_and_second_pass(
    invalid_labels: list[ReceiptWordLabel], all_labels: list[ReceiptWordLabel]
) -> tuple[list[ReceiptWordLabel], list[ReceiptWordLabel]]:
    """
    Split the labels into first and second pass labels

    The first pass labels are those that have all labels with a
    validation_status of NONE.

    The second pass labels are those that have at least one label with a
    validation_status of INVALID.
    """
    first_pass_labels = []
    second_pass_labels = []
    for invalid_label in invalid_labels:
        other_labels = [
            label
            for label in all_labels
            if label.word_id == invalid_label.word_id
            and label.line_id == invalid_label.line_id
        ]
        if all(
            label.validation_status == ValidationStatus.NONE.value
            for label in other_labels
        ):
            first_pass_labels.append(invalid_label)
        else:
            second_pass_labels.append(invalid_label)
    return first_pass_labels, second_pass_labels


def format_batch_completion_file(
    lines: list[ReceiptLine],
    words: list[ReceiptWord],
    labels: list[ReceiptWordLabel],
    first_pass_labels: list[ReceiptWordLabel],
    second_pass_labels: list[ReceiptWordLabel],
    metadata: ReceiptMetadata,  # pylint: disable=too-many-positional-arguments
) -> Path:
    """Format the batch completion file name."""
    filepath = Path(
        f"/tmp/{metadata.image_id}_{metadata.receipt_id}_{uuid4()}.ndjson"
    )
    batch_lines: list[dict] = []
    if len(first_pass_labels) + len(second_pass_labels) > 0:
        batch_lines.append(
            {
                "method": "POST",
                "custom_id": (
                    f"IMAGE#{metadata.image_id}#"
                    f"RECEIPT#{metadata.receipt_id:05d}"
                ),
                "url": "/v1/chat/completions",
                "body": {
                    "model": "gpt-4.1-mini",
                    "messages": [
                        {
                            "role": "system",
                            "content": _format_prompt(
                                first_pass_labels,
                                second_pass_labels,
                                words,
                                lines,
                                labels,
                                metadata,
                            ),
                        },
                    ],
                    "functions": functions,
                },
            }
        )
    with filepath.open("w", encoding="utf-8") as f:
        for batch_line in batch_lines:
            f.write(json.dumps(batch_line) + "\n")
    return filepath


def upload_completion_batch_file(
    filepath: Path, s3_bucket: str, prefix: str = "completion_batches"
) -> str:
    """Upload the completion batch file to S3 and return its S3 key."""
    s3 = boto3.client("s3")
    s3_key = f"{prefix}/{filepath.name}"
    s3.upload_file(str(filepath), s3_bucket, s3_key)
    return s3_key


def upload_to_openai(
    filepath: Path, client_manager: ClientManager = None
) -> FileObject:
    """Upload the NDJSON file to OpenAI."""
    if client_manager is None:
        client_manager = get_client_manager()
    return client_manager.openai.files.create(
        file=filepath.open("rb"), purpose="batch"
    )


def submit_openai_batch(
    file_id: str, client_manager: ClientManager = None
) -> Batch:
    """Submit a batch completion job to OpenAI using the uploaded file."""
    if client_manager is None:
        client_manager = get_client_manager()
    return client_manager.openai.batches.create(
        input_file_id=file_id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )


def create_batch_summary(
    batch_id: str, open_ai_batch_id: str, receipt_refs: list[tuple[str, int]]
) -> BatchSummary:
    """Create a summary of the batch."""
    return BatchSummary(
        batch_id=batch_id,
        batch_type=BatchType.COMPLETION.value,
        openai_batch_id=open_ai_batch_id,
        submitted_at=datetime.now(timezone.utc),
        status=BatchStatus.PENDING.value,
        result_file_id="N/A",
        receipt_refs=receipt_refs,
    )


def add_batch_summary(
    summary: BatchSummary, client_manager: ClientManager = None
) -> None:
    """Write the BatchSummary entity to DynamoDB."""
    if client_manager is None:
        client_manager = get_client_manager()
    client_manager.dynamo.addBatchSummary(summary)


def update_label_validation_status(
    labels: list[ReceiptWordLabel], client_manager: ClientManager = None
) -> None:
    """Update the validation status of the labels."""
    if client_manager is None:
        client_manager = get_client_manager()
    for label in labels:
        label.validation_status = ValidationStatus.PENDING.value
    client_manager.dynamo.updateReceiptWordLabels(labels)


def merge_ndjsons(
    s3_bucket: str,
    s3_keys: list[str],
    max_lines: int = 50_000,
    max_size_bytes: int = 100 * 1024 * 1024,
) -> list[tuple[Path, list[str]]]:
    """
    Merge multiple NDJSON files from S3 into one or more temporary NDJSON
    files. Stops adding lines when max_lines or max_size_bytes is reached.
    Returns a list of (merged_path, consumed_keys) for each merged file.
    """
    s3 = boto3.client("s3")
    merged_files: list[tuple[Path, list[str]]] = []

    # Helper to start a new temp file
    def start_file():
        tmp = tempfile.NamedTemporaryFile(
            prefix="merged_", suffix=".ndjson", delete=False
        )
        return Path(tmp.name), tmp

    merged_path, tmp_file = start_file()
    total_lines = 0
    total_bytes = 0
    consumed_keys: List[str] = []

    for key in s3_keys:
        stream = s3.get_object(Bucket=s3_bucket, Key=key)["Body"].iter_lines()
        key_line_count = 0
        key_byte_count = 0

        # First pass through stream to decide if we need a rollover
        for line in stream:
            key_line_count += 1
            key_byte_count += len(line) + 1  # newline

            if (
                total_lines + key_line_count > max_lines
                or total_bytes + key_byte_count > max_size_bytes
            ):
                # rollover: finalize current file
                tmp_file.close()
                merged_files.append((merged_path, consumed_keys.copy()))
                # start a new merged file
                merged_path, tmp_file = start_file()
                total_lines = 0
                total_bytes = 0
                consumed_keys.clear()
                break

        # re-open the stream and write lines
        stream = s3.get_object(Bucket=s3_bucket, Key=key)["Body"].iter_lines()
        for line in stream:
            line_bytes = line + b"\n"
            if (
                total_lines + 1 > max_lines
                or total_bytes + len(line_bytes) > max_size_bytes
            ):
                break
            tmp_file.write(line_bytes)
            total_lines += 1
            total_bytes += len(line_bytes)
        consumed_keys.append(key)

    # finalize last file
    tmp_file.close()
    if consumed_keys:
        merged_files.append((merged_path, consumed_keys.copy()))

    return merged_files


def get_labels_from_ndjson(
    filepath: Path,
) -> Tuple[list[ReceiptWordLabel], list[tuple[str, int]]]:
    """
    Get the labels from an NDJSON file by parsing custom_id key-value pairs,
    and return unique (image_id, receipt_id) pairs.
    """
    label_indices = []
    ids = []
    with filepath.open("r") as f:
        for line in f:
            data = json.loads(line)
            messages = data["body"]["messages"]
            if len(messages) != 1:
                raise ValueError(f"Expected 1 message, got {len(messages)}")
            message = messages[0]
            match = re.search(
                r"### Targets\s*\n(\[.*?\])\s*(?:\n###|\Z)",
                message["content"],
                flags=re.DOTALL,
            )
            if not match:
                raise ValueError("Could not find ### Targets array in prompt")
            array_text = match.group(1)
            try:
                targets = json.loads(array_text)
                for target in targets:
                    ids.append(target["id"])
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Failed to parse Targets JSON: {e.msg}"
                ) from e
    for custom_id in ids:
        parts = custom_id.split("#")
        kv = {parts[i]: parts[i + 1] for i in range(0, len(parts) - 1, 2)}
        required_keys = {"IMAGE", "RECEIPT", "LINE", "WORD", "LABEL"}
        if not required_keys.issubset(kv):
            raise ValueError(f"Invalid custom_id format: {custom_id}")
        image_id = kv["IMAGE"]
        receipt_id = int(kv["RECEIPT"])
        line_id = int(kv["LINE"])
        word_id = int(kv["WORD"])
        label_str = kv["LABEL"]
        label_indices.append(
            (image_id, receipt_id, line_id, word_id, label_str)
        )
    # Collect unique (image_id, receipt_id) pairs
    receipt_refs = list(
        {
            (image_id, receipt_id)
            for image_id, receipt_id, _, _, _ in label_indices
        }
    )
    # Call Dynamo to fetch labels
    client_manager = get_client_manager()
    labels = client_manager.dynamo.getReceiptWordLabelsByIndices(label_indices)
    # Return both labels and receipt_refs
    return labels, receipt_refs

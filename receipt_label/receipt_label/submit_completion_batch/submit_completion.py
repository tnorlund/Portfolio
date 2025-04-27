import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4
import boto3
from typing import List, Tuple


from receipt_dynamo.entities import (
    ReceiptWordLabel,
    ReceiptWord,
    ReceiptLine,
    ReceiptMetadata,
    BatchSummary,
)
from openai.types import FileObject
from openai.resources.batches import Batch
from receipt_dynamo.constants import (
    ValidationStatus,
    PassNumber,
    BatchType,
    BatchStatus,
)
from receipt_label.submit_completion_batch._format_prompt import (
    _format_first_pass_prompt,
    _format_second_pass_prompt,
    functions,
)
from datetime import datetime, timezone

from receipt_label.utils import get_clients
from receipt_label.constants import CORE_LABELS

dynamo_client, openai_client = get_clients()[:2]


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
        labels,
    ) = dynamo_client.getReceiptDetails(image_id, receipt_id)
    metadata = dynamo_client.getReceiptMetadata(image_id, receipt_id)
    return lines, words, metadata, labels


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
        dedupe_key = (label.line_id, label.word_id, label.label)
        receipt_map[dedupe_key] = label  # later entries overwrite duplicates

    # Second pass – convert inner dicts (where values are ReceiptWordLabel) to lists
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
            with filepath.open("w") as f:
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


def split_first_and_second_pass(
    invalid_labels: list[ReceiptWordLabel], all_labels: list[ReceiptWordLabel]
) -> tuple[list[ReceiptWordLabel], list[ReceiptWordLabel]]:
    """
    Split the labels into first and second pass labels

    The first pass labels are those that have all labels with a validation_status of NONE
    The second pass labels are those that have at least one label with a validation_status of INVALID
    """
    first_pass_labels = []
    second_pass_labels = []
    for label in invalid_labels:
        other_labels = [
            l
            for l in all_labels
            if l.word_id == label.word_id and l.line_id == label.line_id
        ]
        if all(
            l.validation_status == ValidationStatus.NONE.value
            for l in other_labels
        ):
            first_pass_labels.append(label)
        else:
            second_pass_labels.append(label)
    return first_pass_labels, second_pass_labels


def format_batch_completion_file(
    lines: list[ReceiptLine],
    words: list[ReceiptWord],
    first_pass_labels: list[ReceiptWordLabel],
    second_pass_labels: list[ReceiptWordLabel],
    metadata: ReceiptMetadata,
) -> Path:
    """Format the batch completion file name."""
    filepath = Path(
        f"/tmp/{metadata.image_id}_{metadata.receipt_id}_{uuid4()}.ndjson"
    )
    batch_lines: list[dict] = []
    for label in first_pass_labels:
        word = next(
            w
            for w in words
            if w.line_id == label.line_id and w.word_id == label.word_id
        )
        if word is None:
            raise ValueError(f"Word not found for label: {label}")
        custom_id = (
            f"IMAGE#{metadata.image_id}#"
            f"RECEIPT#{metadata.receipt_id:05d}#"
            f"LINE#{label.line_id:05d}#"
            f"WORD#{label.word_id:05d}#"
            f"LABEL#{label.label}#"
            f"VALIDATION_STATUS#{label.validation_status}#"
            f"PASS#{PassNumber.FIRST.value}"
        )
        messages = [
            {
                "role": "system",
                "content": _format_first_pass_prompt(
                    word, label, lines, metadata
                ),
            },
            {"role": "user", "content": "Here is the receipt text:"},
            # {
            #     "role": "user",
            #     "content": _prompt_receipt_text(
            #         word,
            #         lines,
            #     ),
            # },
        ]
        batch_line = {
            "method": "POST",
            "custom_id": custom_id,
            "url": "/v1/chat/completions",
            "body": {
                "model": "gpt-4.1-nano",
                "messages": messages,
                "functions": functions,
            },
        }
        batch_lines.append(batch_line)

    for invalid_label in second_pass_labels:
        word = next(
            w
            for w in words
            if w.line_id == invalid_label.line_id
            and w.word_id == invalid_label.word_id
        )
        if word is None:
            raise ValueError(f"Word not found for label: {invalid_label}")

        similar_labels, _ = dynamo_client.getReceiptWordLabelsByLabel(
            label=invalid_label.label,
            limit=100,
        )
        similar_words = dynamo_client.getReceiptWordsByIndices(
            indices=[
                (
                    similar_label.image_id,
                    similar_label.receipt_id,
                    similar_label.line_id,
                    similar_label.word_id,
                )
                for similar_label in similar_labels
            ]
        )
        custom_id = (
            f"IMAGE#{metadata.image_id}#"
            f"RECEIPT#{metadata.receipt_id:05d}#"
            f"LINE#{invalid_label.line_id:05d}#"
            f"WORD#{invalid_label.word_id:05d}#"
            f"LABEL#{invalid_label.label}#"
            f"VALIDATION_STATUS#{invalid_label.validation_status}#"
            f"PASS#{PassNumber.SECOND.value}"
        )
        messages = [
            {
                "role": "system",
                "content": _format_second_pass_prompt(
                    word,
                    invalid_label,
                    similar_words,
                    similar_labels,
                    metadata,
                ),
            },
            {"role": "user", "content": "Here is the receipt text:"},
            # {
            #     "role": "user",
            #     "content": _prompt_receipt_text(
            #         word,
            #         lines,
            #     ),
            # },
        ]
        batch_line = {
            "method": "POST",
            "custom_id": custom_id,
            "url": "/v1/chat/completions",
            "body": {
                "model": "gpt-4.1-nano",
                "messages": messages,
                "functions": functions,
            },
        }
        batch_lines.append(batch_line)
    with filepath.open("w") as f:
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
        batch_type=BatchType.COMPLETION.value,
        openai_batch_id=open_ai_batch_id,
        submitted_at=datetime.now(timezone.utc),
        status=BatchStatus.PENDING.value,
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


def merge_ndjsons(
    s3_bucket: str,
    s3_keys: list[str],
    max_lines: int = 50_000,
    max_size_bytes: int = 100 * 1024 * 1024,
) -> list[tuple[Path, list[str]]]:
    """
    Merge multiple NDJSON files from S3 into one or more temporary NDJSON files.
    Stops adding lines when max_lines or max_size_bytes is reached.
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


def get_labels_from_ndjson(filepath: Path) -> list[ReceiptWordLabel]:
    """Get the labels from an NDJSON file."""
    label_indices = []
    with filepath.open("r") as f:
        for line in f:
            data = json.loads(line)
            custom_id = data["custom_id"]
            split = custom_id.split("#")
            image_id = split[1]
            receipt_id = int(split[3])
            line_id = int(split[5])
            word_id = int(split[7])
            label = split[9]
            label_indices.append(
                (image_id, receipt_id, line_id, word_id, label)
            )

    return dynamo_client.getReceiptWordLabelsByIndices(label_indices)

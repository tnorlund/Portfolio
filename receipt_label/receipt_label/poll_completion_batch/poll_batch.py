from pathlib import Path
import json
from dataclasses import dataclass, asdict
from receipt_label.utils import get_clients
from receipt_dynamo.constants import BatchStatus, BatchType, ValidationStatus
from receipt_dynamo.entities import (
    CompletionBatchResult,
    BatchSummary,
    ReceiptWordLabel,
)
from datetime import datetime, timezone
from itertools import islice


def _chunk(iterable, n):
    it = iter(iterable)
    while True:
        chunk = list(islice(it, n))
        if not chunk:
            return
        yield chunk


dynamo_client, openai_client, pinecone_index = get_clients()

# ---- Pinecone namespace used by this pipeline ------------------------------
PINECONE_NS = "words"


@dataclass
class ParsedResult:
    """
    A parsed result from an OpenAI completion batch.
    """

    batch_id: str
    custom_id: str
    is_valid: bool
    correct_label: str | None
    rationale: str | None
    image_id: str
    receipt_id: str
    line_id: str
    word_id: str
    label: str
    pass_number: str
    vector_id: str


def list_pending_completion_batches() -> list[BatchSummary]:
    """
    List all pending completion batches that need to be processed.
    """
    pending_completion_batches, _ = dynamo_client.getBatchSummariesByStatus(
        status=BatchStatus.PENDING, batch_type=BatchType.COMPLETION
    )
    return pending_completion_batches


def get_openai_batch_status(openai_batch_id: str) -> str:
    """
    Retrieve the status of an OpenAI embedding batch job.
    Args:
        batch_summary (BatchSummary): The batch summary.
    Returns the current status of the batch.
    """
    return openai_client.batches.retrieve(openai_batch_id).status


def download_openai_batch_result(
    batch_summary: BatchSummary,
) -> list[ParsedResult]:
    """
    Download and parse the results of an OpenAI embedding batch job.
    """
    batch = openai_client.batches.retrieve(batch_summary.openai_batch_id)
    output_file_id = batch.output_file_id
    response = openai_client.files.content(output_file_id)

    # If the content is raw bytes, decode it:
    if hasattr(response, "read"):
        lines = response.read().decode("utf-8").splitlines()
    elif isinstance(response, bytes):
        lines = response.decode("utf-8").splitlines()
    elif isinstance(response, str):
        lines = response.splitlines()
    else:
        raise ValueError("Unexpected OpenAI file content type")

    parsed_results: list[ParsedResult] = []
    for line in lines:
        try:
            data = json.loads(line)
            custom_id = data["custom_id"]
            split_custom_id = custom_id.split("#")
            image_id = split_custom_id[1]
            receipt_id = int(split_custom_id[3])
            line_id = int(split_custom_id[5])
            word_id = int(split_custom_id[7])
            label = split_custom_id[9]
            validation_status = split_custom_id[11]
            pass_number = split_custom_id[13]

            # --- Navigate into the nested `body` field that OpenAI returns -----
            response_payload = data.get("response", {})
            body = response_payload.get(
                "body", {}
            )  # <-- real content lives here

            # Skip rows that returned an error
            if "error" in body:
                continue

            choices = body.get("choices", [])
            if not choices:
                # Some items (e.g. file‑upload errors) genuinely have no choices
                continue

            message = choices[0].get("message", {})
            function_call = message.get("function_call")

            if function_call:
                # Tool/function‑call style response – arguments is JSON string
                raw_response = function_call.get("arguments", "")
                try:
                    response_obj = json.loads(raw_response)
                except json.JSONDecodeError:
                    response_obj = {}
            else:
                # Standard assistant message
                raw_response = message.get("content", "")
                try:
                    response_obj = (
                        json.loads(raw_response) if raw_response else {}
                    )
                except json.JSONDecodeError:
                    response_obj = {}

            is_valid = response_obj.get("is_valid")
            correct_label = response_obj.get("correct_label")
            rationale = response_obj.get("rationale")
            if isinstance(rationale, str):
                rationale = rationale.strip()

            vector_id = (
                f"IMAGE#{image_id}#"
                f"RECEIPT#{receipt_id:05d}#"
                f"LINE#{line_id:05d}#"
                f"WORD#{word_id:05d}"
            )
            parsed_results.append(
                ParsedResult(
                    batch_id=batch_summary.batch_id,
                    custom_id=custom_id,
                    is_valid=is_valid,
                    correct_label=correct_label,
                    rationale=rationale,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=line_id,
                    word_id=word_id,
                    label=label,
                    pass_number=pass_number,
                    vector_id=vector_id,
                )
            )
        except (KeyError, json.JSONDecodeError) as e:
            raise Exception(f"Error parsing line: {line}") from e

    return parsed_results


def update_valid_labels(parsed_results: list[ParsedResult]) -> None:
    """
    Update the valid labels in the database and Pinecone index.
    """
    # 1. Build mapping: vector_id -> valid label
    valid_labels_by_vector_id = {}

    for result in parsed_results:
        label_to_add = None
        if result.is_valid:
            label_to_add = result.label
        elif result.correct_label:
            label_to_add = result.correct_label

        if label_to_add:
            valid_labels_by_vector_id.setdefault(result.vector_id, []).append(
                label_to_add
            )

    vector_ids = list(valid_labels_by_vector_id.keys())

    if not vector_ids:
        return

    # 2 & 3. Fetch in chunks (Pinecone limit = 100) and update metadata
    for id_batch in _chunk(vector_ids, 100):
        fetched = pinecone_index.fetch(
            ids=id_batch, namespace=PINECONE_NS
        ).vectors
        for vid in id_batch:
            meta = (fetched[vid].metadata if vid in fetched else {}) or {}
            existing = meta.get("valid_labels", [])
            if isinstance(existing, str):
                existing = [existing]
            new_labels = valid_labels_by_vector_id.get(vid, [])
            meta["valid_labels"] = list(set(existing + new_labels))

            # Pinecone update is one‑vector‑at‑a‑time
            pinecone_index.update(
                id=vid, set_metadata=meta, namespace=PINECONE_NS
            )

    # 4. Update DynamoDB ReceiptWordLabels too (optional but usually important)
    indices = [
        (
            result.image_id,
            result.receipt_id,
            result.line_id,
            result.word_id,
            result.label,
        )
        for result in parsed_results
        if result.is_valid  # Only updating records that were validated as correct
    ]

    if indices:
        labels = dynamo_client.getReceiptWordLabelsByIndices(indices)
        for label in labels:
            label.validation_status = ValidationStatus.VALID.value
            label.label_proposed_by = "COMPLETION_BATCH"
        dynamo_client.updateReceiptWordLabels(labels)


def update_invalid_labels(parsed_results: list[ParsedResult]) -> None:
    """
    Update invalid labels in DynamoDB and Pinecone index based on batch parsing results.
    """
    invalid_results = [r for r in parsed_results if not r.is_valid]

    if not invalid_results:
        return

    # Update original ReceiptWordLabels to INVALID
    indices = [
        (r.image_id, r.receipt_id, r.line_id, r.word_id, r.label)
        for r in invalid_results
    ]
    labels = dynamo_client.getReceiptWordLabelsByIndices(indices)
    labels_to_update = []
    new_labels_to_create = []
    # ------------------------------------------------------------------
    # Guard against duplicate “new label” insertions for the *same*
    # (image, receipt, line, word, proposed_label) key.
    # We’ll keep only the first instance that appears in `invalid_results`.
    seen_new_label_keys: set[tuple] = set()
    # ------------------------------------------------------------------

    for result in invalid_results:
        label = next(
            (
                l
                for l in labels
                if l.image_id == result.image_id
                and l.receipt_id == result.receipt_id
                and l.line_id == result.line_id
                and l.word_id == result.word_id
                and l.label == result.label
            ),
            None,
        )
        if label:
            label.validation_status = ValidationStatus.INVALID.value
            label.label_proposed_by = "COMPLETION_BATCH"
            labels_to_update.append(label)

        if result.correct_label:
            key = (
                result.image_id,
                result.receipt_id,
                result.line_id,
                result.word_id,
                result.correct_label,
            )
            if key in seen_new_label_keys:
                # Duplicate – skip to avoid writing the same label twice
                continue
            seen_new_label_keys.add(key)

            new_label = ReceiptWordLabel(
                image_id=result.image_id,
                receipt_id=result.receipt_id,
                line_id=result.line_id,
                word_id=result.word_id,
                label=result.correct_label,
                validation_status=ValidationStatus.NONE.value,
                reasoning=None,
                label_proposed_by="COMPLETION_BATCH",
                label_consolidated_from=result.label,
                timestamp_added=datetime.now(timezone.utc),
            )
            new_labels_to_create.append(new_label)

    if labels_to_update:
        dynamo_client.updateReceiptWordLabels(labels_to_update)

    if new_labels_to_create:
        dynamo_client.addReceiptWordLabels(new_labels_to_create)

    vector_ids = list({r.vector_id for r in invalid_results})

    for id_batch in _chunk(vector_ids, 100):
        fetched = pinecone_index.fetch(
            ids=id_batch, namespace=PINECONE_NS
        ).vectors

        for vid in id_batch:
            meta = (fetched[vid].metadata if vid in fetched else {}) or {}
            related = [r for r in invalid_results if r.vector_id == vid]

            existing_invalid = meta.get("invalid_labels", [])
            if isinstance(existing_invalid, str):
                existing_invalid = [existing_invalid]

            new_invalid = [r.label for r in related]
            meta["invalid_labels"] = list(set(existing_invalid + new_invalid))

            # If any have a proposed label, keep the last one
            proposed = next(
                (
                    r.correct_label
                    for r in reversed(related)
                    if r.correct_label
                ),
                None,
            )
            if proposed:
                meta["proposed_label"] = proposed

            pinecone_index.update(
                id=vid, set_metadata=meta, namespace=PINECONE_NS
            )


def write_completion_batch_results(parsed_results: list[ParsedResult]) -> None:
    """
    Write the completion batch results to DynamoDB.
    """
    completion_batch_results = []
    for result in parsed_results:
        completion_batch_results.append(
            CompletionBatchResult(
                batch_id=result.batch_id,
                image_id=result.image_id,
                receipt_id=result.receipt_id,
                line_id=result.line_id,
                word_id=result.word_id,
                original_label=result.label,
                gpt_suggested_label=(
                    result.correct_label if result.correct_label else None
                ),
                status=BatchStatus.COMPLETED,
                validated_at=datetime.now(timezone.utc),
                reasoning=(result.rationale if result.rationale else None),
                is_valid=result.is_valid,
                vector_id=result.vector_id,
                pass_number=result.pass_number,
            )
        )
    dynamo_client.addCompletionBatchResults(completion_batch_results)


def update_batch_summary(batch_summary: BatchSummary) -> None:
    """
    Update the batch summary in DynamoDB.
    """
    batch_summary.status = BatchStatus.COMPLETED.value
    dynamo_client.updateBatchSummary(batch_summary)

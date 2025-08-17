import json
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice

from receipt_dynamo.constants import BatchStatus, BatchType, ValidationStatus
from receipt_dynamo.entities import (
    BatchSummary,
    CompletionBatchResult,
    ReceiptWordLabel,
)

from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager


def _chunk(iterable, n):
    it = iter(iterable)
    while True:
        chunk = list(islice(it, n))
        if not chunk:
            return
        yield chunk


def _build_vector_id(label: ReceiptWordLabel) -> str:
    return (
        f"IMAGE#{label.image_id}#"
        f"RECEIPT#{label.receipt_id:05d}#"
        f"LINE#{label.line_id:05d}#"
        f"WORD#{label.word_id:05d}"
    )


# ---- Pinecone namespace used by this pipeline ------------------------------
PINECONE_NS = "words"


@dataclass
class LabelResult:
    label_from_dynamo: ReceiptWordLabel
    result: dict
    other_labels: list[ReceiptWordLabel]


def list_pending_completion_batches(
    client_manager: ClientManager = None,
) -> list[BatchSummary]:
    """
    List all pending completion batches that need to be processed.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    pending_completion_batches, _ = (
        client_manager.dynamo.getBatchSummariesByStatus(
            status=BatchStatus.PENDING, batch_type=BatchType.COMPLETION
        )
    )
    return pending_completion_batches


def get_openai_batch_status(
    openai_batch_id: str, client_manager: ClientManager = None
) -> str:
    """
    Retrieve the status of an OpenAI embedding batch job.
    Args:
        batch_summary (BatchSummary): The batch summary.
    Returns the current status of the batch.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    return client_manager.openai.batches.retrieve(openai_batch_id).status


def _parse_result_id(result_id: str) -> tuple[str, int, int, int, str]:
    """
    Parse the result ID from the OpenAI batch result.
    """
    id_split = result_id.split("#")
    if len(id_split) != 12:
        raise ValueError(f"Invalid ID: {result_id}")
    image_id = id_split[1]
    receipt_id = int(id_split[3])
    line_id = int(id_split[5])
    word_id = int(id_split[7])
    label = id_split[9]
    return image_id, receipt_id, line_id, word_id, label


def _extract_results(data: dict) -> list[dict]:
    """
    Extract the results from the OpenAI batch result.
    """
    response = data["response"]
    body = response["body"]
    choices = body["choices"]
    if len(choices) != 1:
        raise ValueError(f"Expected 1 choice, got {len(choices)}")
    message = choices[0]["message"]
    if "function_call" in message:
        arguments = message["function_call"].get("arguments")
        if not arguments:
            raise ValueError(f"Missing arguments in function_call: {message}")
        try:
            return json.loads(arguments)["results"]
        except (json.JSONDecodeError, KeyError) as e:
            raise ValueError(
                f"Could not parse function_call arguments: {e}"
            ) from e
    elif message["content"] is not None:
        return json.loads(message["content"])["results"]
    else:
        raise ValueError(f"No usable result found in message: {message}")


def download_openai_batch_result(  # pylint: disable=too-many-locals,too-many-statements
    batch_summary: BatchSummary,
    client_manager: ClientManager = None,
) -> tuple[list[ReceiptWordLabel], list[LabelResult], list[LabelResult]]:
    """
    Returns:
        pending_labels_to_update: ReceiptWordLabel objects to set back to NONE
        valid_labels:   List[LabelResult] with result.is_valid == True
        invalid_labels: List[LabelResult] with result.is_valid == False
    """
    if client_manager is None:
        client_manager = get_client_manager()
    batch = client_manager.openai.batches.retrieve(
        batch_summary.openai_batch_id
    )
    output_file_id = batch.output_file_id
    response = client_manager.openai.files.content(output_file_id)
    if hasattr(response, "read"):
        lines = response.read().decode("utf-8").splitlines()
    elif isinstance(response, bytes):
        lines = response.decode("utf-8").splitlines()
    elif isinstance(response, str):
        lines = response.splitlines()
    else:
        raise ValueError("Unexpected OpenAI file content type")

    # --- Caching getReceiptDetails per (image_id, receipt_id) ---
    receipt_details_cache = {}
    pending_labels_to_update: list[ReceiptWordLabel] = []
    valid_labels: list[LabelResult] = []
    invalid_labels: list[LabelResult] = []

    for line in lines:
        try:
            data = json.loads(line)
            custom_id = data["custom_id"]
            image_id = custom_id.split("#")[1]
            receipt_id = int(custom_id.split("#")[3])
            cache_key = (image_id, receipt_id)
            if cache_key not in receipt_details_cache:
                (
                    _,
                    _,
                    _,
                    _,
                    _,
                    all_labels_from_receipt,
                ) = client_manager.dynamo.getReceiptDetails(
                    image_id=image_id, receipt_id=receipt_id
                )
                receipt_details_cache[cache_key] = all_labels_from_receipt
            else:
                all_labels_from_receipt = receipt_details_cache[cache_key]

            # Build pending_labels for this receipt
            pending_labels = [
                label
                for label in all_labels_from_receipt
                if label.validation_status == ValidationStatus.PENDING.value
            ]
            try:
                results = _extract_results(data)
            except Exception:  # pylint: disable=broad-exception-caught
                continue
            if results is None:

                continue
            for result in results:
                (
                    result_image_id,
                    result_receipt_id,
                    result_line_id,
                    result_word_id,
                    result_label,
                ) = _parse_result_id(result["id"])
                label_from_dynamo = next(
                    (
                        label
                        for label in all_labels_from_receipt
                        if label.image_id == result_image_id
                        and label.receipt_id == result_receipt_id
                        and label.line_id == result_line_id
                        and label.word_id == result_word_id
                        and label.label == result_label
                    ),
                    None,
                )
                if label_from_dynamo is None:
                    continue
                # Remove from pending_labels if present
                pending_labels = [
                    label
                    for label in pending_labels
                    if not (
                        label.image_id == label_from_dynamo.image_id
                        and label.receipt_id == label_from_dynamo.receipt_id
                        and label.line_id == label_from_dynamo.line_id
                        and label.word_id == label_from_dynamo.word_id
                        and label.label == label_from_dynamo.label
                    )
                ]
                if (
                    label_from_dynamo.validation_status
                    == ValidationStatus.VALID.value
                ):
                    continue
                # Build other_labels for this word/line except this label
                other_labels = [
                    label
                    for label in all_labels_from_receipt
                    if label.image_id == label_from_dynamo.image_id
                    and label.receipt_id == label_from_dynamo.receipt_id
                    and label.line_id == label_from_dynamo.line_id
                    and label.word_id == label_from_dynamo.word_id
                    and label.label != label_from_dynamo.label
                ]
                if result.get("is_valid"):
                    valid_labels.append(
                        LabelResult(
                            label_from_dynamo=label_from_dynamo,
                            result=result,
                            other_labels=other_labels,
                        )
                    )
                else:
                    invalid_labels.append(
                        LabelResult(
                            label_from_dynamo=label_from_dynamo,
                            result=result,
                            other_labels=other_labels,
                        )
                    )
            # After all results, any leftover pending_labels for this receipt
            # should be reset to NONE
            if pending_labels:
                for label in pending_labels:
                    label.validation_status = ValidationStatus.NONE.value
                    pending_labels_to_update.append(label)
        except Exception:  # pylint: disable=broad-exception-caught
            # Could not parse line as JSON, skip
            continue

    return pending_labels_to_update, valid_labels, invalid_labels


def update_pending_labels(
    pending_labels_to_update: list[ReceiptWordLabel],
    client_manager: ClientManager = None,
) -> None:
    """
    Update the pending labels in the database.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    for pending_label in pending_labels_to_update:
        pending_label.validation_status = ValidationStatus.NONE.value
        pending_label.label_proposed_by = "COMPLETION_BATCH"

    # Chunk into 25 items and update
    for chunk in _chunk(pending_labels_to_update, 25):
        client_manager.dynamo.updateReceiptWordLabels(chunk)


def update_valid_labels(
    valid_labels_results: list[LabelResult],
    client_manager: ClientManager = None,
) -> None:
    """
    Update the valid labels in the database and Pinecone index.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    # ------------------------------------------------------------------ #
    # 1. Build mapping:  vector_id -> list[str] (new valid labels)       #
    # ------------------------------------------------------------------ #
    valid_by_vector: dict[str, list[str]] = {}
    for res in valid_labels_results:
        res.label_from_dynamo.validation_status = ValidationStatus.VALID.value
        res.label_from_dynamo.label_proposed_by = "COMPLETION_BATCH"
        vid = _build_vector_id(res.label_from_dynamo)
        valid_by_vector.setdefault(vid, []).append(res.label_from_dynamo.label)

    # ------------------------------------------------------------------ #
    # 2. Fetch existing metadata in chunks, decide which ones need write #
    # ------------------------------------------------------------------ #
    vectors_needing_update: list[tuple[str, dict]] = []  # (id, merged_meta)

    for id_batch in _chunk(valid_by_vector.keys(), 100):
        # Get vectors from ChromaDB
        results = client_manager.chroma.get_by_ids(
            PINECONE_NS, id_batch, include=["metadatas"]
        )
        fetched = {}
        if results and "ids" in results:
            for i, id_ in enumerate(results["ids"]):
                fetched[id_] = type(
                    "Vector",
                    (),
                    {
                        "metadata": (
                            results["metadatas"][i]
                            if "metadatas" in results
                            else {}
                        )
                    },
                )
        for vid in id_batch:
            meta = (fetched[vid].metadata if vid in fetched else {}) or {}
            existing = meta.get("valid_labels", [])
            if isinstance(existing, str):
                existing = [existing]

            new_labels = valid_by_vector[vid]
            merged = list(set(existing + new_labels))

            # Skip if nothing would change
            if set(merged) == set(existing):
                continue

            meta["valid_labels"] = merged
            vectors_needing_update.append((vid, meta))

    # ------------------------------------------------------------------ #
    # 3. Write only vectors whose metadata changed                       #
    # ------------------------------------------------------------------ #
    for vid, meta in vectors_needing_update:
        # Update metadata in ChromaDB
        collection = client_manager.chroma.get_collection(PINECONE_NS)
        collection.update(ids=[vid], metadatas=[meta])

    # Chunk into 25 items and update
    for chunk in _chunk(
        [r.label_from_dynamo for r in valid_labels_results], 25
    ):
        client_manager.dynamo.updateReceiptWordLabels(chunk)


def update_invalid_labels(
    invalid_labels_results: list[LabelResult],
    client_manager: ClientManager = None,
) -> None:
    """
    Update invalid labels in DynamoDB and Pinecone index based on batch
    parsing results.
    """
    if client_manager is None:
        client_manager = get_client_manager()

    labels_to_update: list[ReceiptWordLabel] = []
    labels_to_add: list[ReceiptWordLabel] = []
    seen_new_label_keys: set[tuple] = set()

    # Maps for Pinecone merge
    invalid_by_vector: dict[str, list[str]] = {}
    proposed_by_vector: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # 1.  Walk through each invalid result                                #
    # ------------------------------------------------------------------ #
    for res in invalid_labels_results:

        # Decide INVALID vs NEEDS_REVIEW
        if any(
            other.validation_status == ValidationStatus.INVALID.value
            for other in res.other_labels
        ):
            res.label_from_dynamo.validation_status = (
                ValidationStatus.NEEDS_REVIEW.value
            )
        else:
            res.label_from_dynamo.validation_status = (
                ValidationStatus.INVALID.value
            )

        res.label_from_dynamo.label_proposed_by = "COMPLETION_BATCH"
        labels_to_update.append(res.label_from_dynamo)

        # Track metadata for Pinecone
        vid = _build_vector_id(res.label_from_dynamo)
        invalid_by_vector.setdefault(vid, []).append(
            res.label_from_dynamo.label
        )

        # Handle a correct_label suggestion
        correct = res.result.get("correct_label")
        if correct:
            proposed_by_vector[vid] = correct  # last one wins ― fine

            key = (
                res.label_from_dynamo.image_id,
                res.label_from_dynamo.receipt_id,
                res.label_from_dynamo.line_id,
                res.label_from_dynamo.word_id,
                correct,
            )
            if key not in seen_new_label_keys:
                seen_new_label_keys.add(key)
                labels_to_add.append(
                    ReceiptWordLabel(
                        image_id=res.label_from_dynamo.image_id,
                        receipt_id=res.label_from_dynamo.receipt_id,
                        line_id=res.label_from_dynamo.line_id,
                        word_id=res.label_from_dynamo.word_id,
                        label=correct,
                        reasoning=None,
                        timestamp_added=datetime.now(timezone.utc),
                        validation_status=ValidationStatus.NONE.value,
                        label_proposed_by="COMPLETION_BATCH",
                        label_consolidated_from=res.label_from_dynamo.label,
                    )
                )

    # ------------------------------------------------------------------ #
    # 2.  Merge metadata in Pinecone, skipping no‑op writes               #
    # ------------------------------------------------------------------ #
    vectors_needing_update: list[tuple[str, dict]] = []

    for id_batch in _chunk(invalid_by_vector.keys(), 100):
        # Get vectors from ChromaDB
        results = client_manager.chroma.get_by_ids(
            PINECONE_NS, id_batch, include=["metadatas"]
        )
        fetched = {}
        if results and "ids" in results:
            for i, id_ in enumerate(results["ids"]):
                fetched[id_] = type(
                    "Vector",
                    (),
                    {
                        "metadata": (
                            results["metadatas"][i]
                            if "metadatas" in results
                            else {}
                        )
                    },
                )
        for vid in id_batch:
            meta = (fetched[vid].metadata if vid in fetched else {}) or {}
            existing_invalid = meta.get("invalid_labels", [])
            if isinstance(existing_invalid, str):
                existing_invalid = [existing_invalid]

            merged_invalid = list(
                set(existing_invalid + invalid_by_vector.get(vid, []))
            )
            changed = False
            if set(merged_invalid) != set(existing_invalid):
                meta["invalid_labels"] = merged_invalid
                changed = True

            if vid in proposed_by_vector:
                if meta.get("proposed_label") != proposed_by_vector[vid]:
                    meta["proposed_label"] = proposed_by_vector[vid]
                    changed = True

            if changed:
                vectors_needing_update.append((vid, meta))

    for vid, meta in vectors_needing_update:
        # Update metadata in ChromaDB
        collection = client_manager.chroma.get_collection(PINECONE_NS)
        collection.update(ids=[vid], metadatas=[meta])

    # ------------------------------------------------------------------ #
    # 3.  DynamoDB writes                                                 #
    # ------------------------------------------------------------------ #
    unique_labels_by_key = {}
    for label in labels_to_update:
        key = (label.key()["PK"]["S"], label.key()["SK"]["S"])
        unique_labels_by_key[key] = label  # Last one wins, that's fine

    labels_to_update = list(unique_labels_by_key.values())
    for chunk in _chunk(labels_to_update, 25):
        client_manager.dynamo.updateReceiptWordLabels(chunk)

    if labels_to_add:
        for chunk in _chunk(labels_to_add, 25):
            client_manager.dynamo.addReceiptWordLabels(chunk)


def write_completion_batch_results(
    batch_summary: BatchSummary,
    valid_results: list[LabelResult],
    invalid_results: list[LabelResult],
    client_manager: ClientManager = None,
) -> None:
    """
    Persist the OpenAI batch outcomes to the CompletionBatchResult table.

    Args:
        batch_summary:  The BatchSummary object for this batch (provides batch_id).
        valid_results:   Results where `is_valid` is True.
        invalid_results: Results where `is_valid` is False.

    Note: We recompute receipt/line/word IDs from result["id"] rather than
    storing a separate ParsedResult dataclass.
    """
    all_results = valid_results + invalid_results
    if not all_results:
        return

    # ------------------------------------------------------------------
    # De-duplicate using the DynamoDB key itself (PK+SK) so we never send
    # two items with the same composite key in the same batch.
    # ------------------------------------------------------------------
    unique_by_key: dict[tuple[str, str], CompletionBatchResult] = {}

    for res in all_results:
        res_id = res.result["id"]
        (
            image_id,
            receipt_id,
            line_id,
            word_id,
            original_label,
        ) = _parse_result_id(res_id)

        correct_label = res.result.get("correct_label")

        item = CompletionBatchResult(
            batch_id=batch_summary.batch_id,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            original_label=original_label,
            gpt_suggested_label=correct_label if correct_label else None,
            status=BatchStatus.COMPLETED.value,
            validated_at=datetime.now(timezone.utc),
        )

        key_dict = item.key()  # {'PK':{'S':...}, 'SK':{'S':...}}
        pk = key_dict["PK"]["S"]
        sk = key_dict["SK"]["S"]
        unique_by_key[(pk, sk)] = item  # last one wins – that’s fine

    completion_records = list(unique_by_key.values())

    # Dynamo batch‑write (25 items per call)
    for chunk in _chunk(completion_records, 25):
        if client_manager is None:
            client_manager = get_client_manager()
        client_manager.dynamo.addCompletionBatchResults(chunk)


def update_batch_summary(
    batch_summary: BatchSummary, client_manager: ClientManager = None
) -> None:
    """
    Update the batch summary in DynamoDB.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    batch_summary.status = BatchStatus.COMPLETED.value
    client_manager.dynamo.updateBatchSummary(batch_summary)

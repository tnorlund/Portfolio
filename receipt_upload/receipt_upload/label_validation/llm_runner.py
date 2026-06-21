"""Shared LLM (grok) label-validation runner.

The grok validation of pending labels is the slowest single step on the upload
critical path (~10s synchronous LLM call). This module extracts that step into
reusable pieces so it can run in TWO places from ONE implementation:

* **Synchronous** (``run_llm_validation_sync``) — inside the words pipeline
  worker, exactly as before.
* **Asynchronous** — the worker builds a self-contained JSON payload
  (``build_async_payload``), drops it on S3 + an SQS queue, and returns without
  waiting; a separate consumer Lambda calls ``apply_async_payload`` to run grok
  and persist the results to DynamoDB. The payload carries the pre-computed
  similar-evidence and word context, so the consumer needs **no** Chroma access
  (which sidesteps the embedding-handoff problem: per-word embeddings are far too
  large for SQS, and re-reading them from Chroma Cloud would race async
  compaction).

Deferred grok writes land in DynamoDB (the source of truth) within seconds of
upload; the Chroma copy converges on the next compaction. The apply logic is a
verbatim port of the original inline block — same decisions, same audit trail.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel

# NOTE: ``receipt_agent.constants`` (CORE_LABELS) and the LLM validator pull in
# heavy, slow-importing dependencies (chromadb, OpenRouter client). They are
# imported lazily inside the functions that need them so this module imports
# cheaply — both for OCR-only Lambda invocations (no grok) and for unit-testing
# the JSON serialization without standing up those deps.


# --------------------------------------------------------------------------- #
# Local label-list helpers (shared with the embedding processor).
# --------------------------------------------------------------------------- #
def _remove_label_from_list(
    word_labels: List[ReceiptWordLabel],
    target: ReceiptWordLabel,
) -> None:
    """Remove a label entity from the mutable local label payload list."""
    for index, label in enumerate(word_labels):
        if (
            label.image_id == target.image_id
            and label.receipt_id == target.receipt_id
            and label.line_id == target.line_id
            and label.word_id == target.word_id
            and label.label == target.label
        ):
            word_labels.pop(index)
            return


def _delete_non_core_label(
    dynamo: Any,
    word_labels: List[ReceiptWordLabel],
    label: ReceiptWordLabel,
) -> None:
    """Delete a transient non-core label from Dynamo and local payload state."""
    from receipt_agent.constants import CORE_LABELS

    if label.label in CORE_LABELS:
        return
    dynamo.delete_receipt_word_label(label)
    _remove_label_from_list(word_labels, label)


# --------------------------------------------------------------------------- #
# Payload builders (synchronous — need Chroma / embeddings).
# --------------------------------------------------------------------------- #
def build_words_context(words: List[Any]) -> List[Dict[str, Any]]:
    """Build the lightweight word-context list passed to the LLM validator."""
    out: List[Dict[str, Any]] = []
    for w in words:
        x_center = (w.x_min + w.x_max) / 2 if hasattr(w, "x_min") else 0
        y_center = (w.y_min + w.y_max) / 2 if hasattr(w, "y_min") else 0
        out.append(
            {
                "text": w.text,
                "line_id": w.line_id,
                "word_id": w.word_id,
                "x": x_center,
                "y": y_center,
            }
        )
    return out


def build_pending_and_evidence(
    llm_needed: List[Tuple[Any, ReceiptWordLabel]],
    image_id: str,
    receipt_id: int,
    lightweight_validator: Any,
    word_embedding_cache: Dict[Tuple[int, int], List[float]],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict]]]:
    """Build ``pending_labels_data`` and per-word ``similar_evidence``.

    This is the only part that touches Chroma (via the lightweight validator's
    similarity query); doing it here lets the async consumer skip Chroma
    entirely by carrying the result in its payload.
    """
    pending_labels_data: List[Dict[str, Any]] = []
    similar_evidence: Dict[str, List[Dict]] = {}

    for word, label in llm_needed:
        word_id_str = f"{label.line_id}_{label.word_id}"
        pending_labels_data.append(
            {
                "line_id": label.line_id,
                "word_id": label.word_id,
                "label": label.label,
                "word_text": word.text,
            }
        )
        try:
            chroma_id = (
                f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
                f"#LINE#{label.line_id:05d}#WORD#{label.word_id:05d}"
            )
            embedding = word_embedding_cache.get((label.line_id, label.word_id))
            if embedding:
                similar_evidence[word_id_str] = (
                    lightweight_validator._query_similar_for_label(
                        embedding=embedding,
                        exclude_id=chroma_id,
                        predicted_label=label.label,
                        n_results_per_query=5,
                    )
                )
            else:
                similar_evidence[word_id_str] = []
        except Exception:
            similar_evidence[word_id_str] = []

    return pending_labels_data, similar_evidence


# --------------------------------------------------------------------------- #
# Apply step (no Chroma — runs grok + persists results).
# --------------------------------------------------------------------------- #
def apply_llm_results(
    *,
    needed_labels: List[ReceiptWordLabel],
    llm_words_context: List[Dict[str, Any]],
    pending_labels_data: List[Dict[str, Any]],
    similar_evidence: Dict[str, List[Dict]],
    image_id: str,
    receipt_id: int,
    dynamo: Any,
    word_labels: List[ReceiptWordLabel],
    merchant_name: Optional[str] = None,
    raise_on_failure: bool = False,
) -> int:
    """Run the LLM validator over the pending labels and persist the decisions.

    Returns the number of labels the LLM resolved.

    ``raise_on_failure`` controls what happens when the LLM call itself fails:

    * ``False`` (sync upload path): swallow the error and clean up the transient
      (non-core) labels — the upload must still succeed; identical to the
      original inline behavior.
    * ``True`` (async consumer): re-raise so the caller can report a batch-item
      failure and SQS redrives the message. The pending labels are left intact
      (NOT cleaned up) so the retry can validate them — never silently dropped.
    """
    from receipt_agent.constants import CORE_LABELS
    from receipt_dynamo.data.shared_exceptions import (
        EntityAlreadyExistsError,
    )

    from receipt_upload.label_validation.llm_validator import (
        LLMBatchValidator,
    )

    llm_validated = 0
    if not needed_labels:
        return 0

    try:
        llm_validator = LLMBatchValidator(temperature=0.0, timeout=120)
        llm_results = llm_validator.validate_receipt_labels(
            pending_labels=pending_labels_data,
            words=llm_words_context,
            similar_evidence=similar_evidence,
            merchant_name=merchant_name,
        )

        result_lookup = {r.word_id: r for r in llm_results}
        for label in needed_labels:
            word_id_str = f"{label.line_id}_{label.word_id}"
            llm_result = result_lookup.get(word_id_str)
            if llm_result and llm_result.decision in (
                "VALID",
                "INVALID",
                "NEEDS_REVIEW",
            ):
                if llm_result.decision == "VALID":
                    if label.label not in CORE_LABELS:
                        _delete_non_core_label(
                            dynamo=dynamo,
                            word_labels=word_labels,
                            label=label,
                        )
                        llm_validated += 1
                        continue
                    label.validation_status = ValidationStatus.VALID.value
                    label.label_proposed_by = "llm_valid"
                    if llm_result.reasoning:
                        label.reasoning = llm_result.reasoning
                    dynamo.update_receipt_word_label(label)
                    llm_validated += 1
                elif llm_result.decision == "NEEDS_REVIEW":
                    if label.label not in CORE_LABELS:
                        _delete_non_core_label(
                            dynamo=dynamo,
                            word_labels=word_labels,
                            label=label,
                        )
                        llm_validated += 1
                        continue
                    label.validation_status = ValidationStatus.NEEDS_REVIEW.value
                    label.label_proposed_by = "llm_needs_review"
                    if llm_result.reasoning:
                        label.reasoning = llm_result.reasoning
                    dynamo.update_receipt_word_label(label)
                    llm_validated += 1
                elif llm_result.label != label.label:
                    # INVALID with correction: label value is part of the SK, so
                    # mark the old label INVALID (audit trail) and create a new one.
                    if llm_result.label in CORE_LABELS:
                        if label.label in CORE_LABELS:
                            label.validation_status = ValidationStatus.INVALID.value
                            label.label_proposed_by = "llm_invalid"
                            label.reasoning = (
                                f"Corrected to {llm_result.label}. "
                                f"{llm_result.reasoning or ''}"
                            )
                            dynamo.update_receipt_word_label(label)
                        else:
                            _delete_non_core_label(
                                dynamo=dynamo,
                                word_labels=word_labels,
                                label=label,
                            )

                        new_label = ReceiptWordLabel(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=label.line_id,
                            word_id=label.word_id,
                            label=llm_result.label,
                            reasoning=llm_result.reasoning,
                            timestamp_added=datetime.now(timezone.utc),
                            validation_status=ValidationStatus.VALID.value,
                            label_proposed_by=f"llm_corrected:{label.label}",
                            label_consolidated_from=label.label,
                        )
                        try:
                            dynamo.add_receipt_word_label(new_label)
                        except EntityAlreadyExistsError:
                            # Idempotent on redelivery: a prior attempt already
                            # created this corrected label. Treat as done.
                            pass
                        word_labels.append(new_label)
                        llm_validated += 1
                    else:
                        if label.label not in CORE_LABELS:
                            _delete_non_core_label(
                                dynamo=dynamo,
                                word_labels=word_labels,
                                label=label,
                            )
                            llm_validated += 1
                            continue
                        label.validation_status = ValidationStatus.NEEDS_REVIEW.value
                        label.label_proposed_by = "llm_invalid_label"
                        label.reasoning = (
                            f"LLM suggested '{llm_result.label}' but it's not "
                            f"a valid CORE_LABEL. {llm_result.reasoning or ''}"
                        )
                        dynamo.update_receipt_word_label(label)
                        llm_validated += 1
                else:
                    if label.label not in CORE_LABELS:
                        _delete_non_core_label(
                            dynamo=dynamo,
                            word_labels=word_labels,
                            label=label,
                        )
                        llm_validated += 1
                        continue
                    label.validation_status = ValidationStatus.INVALID.value
                    label.label_proposed_by = "llm_invalid"
                    if llm_result.reasoning:
                        label.reasoning = llm_result.reasoning
                    dynamo.update_receipt_word_label(label)
                    llm_validated += 1
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning("LLM validation failed: %s", e)
        if raise_on_failure:
            # Async consumer: don't clean up — leave the pending labels intact
            # and propagate so SQS redrives (and eventually DLQs) the message.
            raise
        for label in needed_labels:
            _delete_non_core_label(
                dynamo=dynamo,
                word_labels=word_labels,
                label=label,
            )

    return llm_validated


# --------------------------------------------------------------------------- #
# Synchronous entry point (used by the words pipeline worker).
# --------------------------------------------------------------------------- #
def run_llm_validation_sync(
    *,
    llm_needed: List[Tuple[Any, ReceiptWordLabel]],
    words: List[Any],
    image_id: str,
    receipt_id: int,
    dynamo: Any,
    word_labels: List[ReceiptWordLabel],
    lightweight_validator: Any,
    word_embedding_cache: Dict[Tuple[int, int], List[float]],
    merchant_name: Optional[str] = None,
) -> int:
    """Build the grok payload and apply it inline (original behavior)."""
    if not llm_needed:
        return 0
    llm_words_context = build_words_context(words)
    pending_labels_data, similar_evidence = build_pending_and_evidence(
        llm_needed=llm_needed,
        image_id=image_id,
        receipt_id=receipt_id,
        lightweight_validator=lightweight_validator,
        word_embedding_cache=word_embedding_cache,
    )
    return apply_llm_results(
        needed_labels=[label for _word, label in llm_needed],
        llm_words_context=llm_words_context,
        pending_labels_data=pending_labels_data,
        similar_evidence=similar_evidence,
        image_id=image_id,
        receipt_id=receipt_id,
        dynamo=dynamo,
        word_labels=word_labels,
        merchant_name=merchant_name,
    )


# --------------------------------------------------------------------------- #
# Async hand-off: JSON-serializable payload <-> apply.
# --------------------------------------------------------------------------- #
def _label_to_jsonable(label: ReceiptWordLabel) -> Dict[str, Any]:
    d = asdict(label)
    ts = d.get("timestamp_added")
    if isinstance(ts, datetime):
        d["timestamp_added"] = ts.isoformat()
    return d


def _label_from_jsonable(d: Dict[str, Any]) -> ReceiptWordLabel:
    return ReceiptWordLabel(**d)


def build_async_payload(
    *,
    llm_needed: List[Tuple[Any, ReceiptWordLabel]],
    words: List[Any],
    image_id: str,
    receipt_id: int,
    table_name: str,
    lightweight_validator: Any,
    word_embedding_cache: Dict[Tuple[int, int], List[float]],
    merchant_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a self-contained, JSON-safe payload for the async consumer.

    Carries everything ``apply_llm_results`` needs (no Chroma on the consumer
    side for the grok step), PLUS the grok-affected words + their embeddings so
    the consumer can emit a corrective Chroma delta for its decisions (see
    ``resync_corrected_labels_to_chroma``). The embeddings are large but go via
    S3, not SQS.
    """
    from dataclasses import asdict

    llm_words_context = build_words_context(words)
    pending_labels_data, similar_evidence = build_pending_and_evidence(
        llm_needed=llm_needed,
        image_id=image_id,
        receipt_id=receipt_id,
        lightweight_validator=lightweight_validator,
        word_embedding_cache=word_embedding_cache,
    )
    # Affected words + aligned embeddings (only those we actually have a vector
    # for) so the consumer can rebuild a words payload with the FINAL labels.
    affected_words: List[Dict[str, Any]] = []
    affected_embeddings: List[List[float]] = []
    for word, _label in llm_needed:
        emb = word_embedding_cache.get((word.line_id, word.word_id))
        if emb is None:
            continue
        affected_words.append(asdict(word))
        affected_embeddings.append(emb)
    return {
        "version": 2,
        "image_id": image_id,
        "receipt_id": receipt_id,
        "table_name": table_name,
        "merchant_name": merchant_name,
        "llm_words_context": llm_words_context,
        "pending_labels_data": pending_labels_data,
        "similar_evidence": similar_evidence,
        "needed_labels": [_label_to_jsonable(label) for _word, label in llm_needed],
        # Filled in by the producer's main process before enqueue (Phase 3b):
        "lines_prefix": None,
        "chromadb_bucket": None,
        "affected_words": affected_words,
        "affected_embeddings": affected_embeddings,
    }


def apply_async_payload(payload: Dict[str, Any], dynamo: Any) -> int:
    """Run grok + persist for a payload produced by ``build_async_payload``.

    Raises on LLM failure so the consumer Lambda can report a batch-item failure
    (SQS redrives, then DLQs) instead of silently dropping the validation and
    leaving labels at PENDING.
    """
    needed_labels = [_label_from_jsonable(d) for d in payload.get("needed_labels", [])]
    # A local working list so _delete_non_core_label has something to mutate;
    # the authoritative writes go straight to DynamoDB inside apply_llm_results.
    word_labels = list(needed_labels)
    return apply_llm_results(
        needed_labels=needed_labels,
        llm_words_context=payload.get("llm_words_context", []),
        pending_labels_data=payload.get("pending_labels_data", []),
        similar_evidence=payload.get("similar_evidence", {}),
        image_id=payload["image_id"],
        receipt_id=payload["receipt_id"],
        dynamo=dynamo,
        word_labels=word_labels,
        merchant_name=payload.get("merchant_name"),
        raise_on_failure=True,
    )


def resync_corrected_labels_to_chroma(
    payload: Dict[str, Any], dynamo: Any
) -> Optional[str]:
    """Propagate the consumer's grok decisions into Chroma via a corrective delta.

    ``apply_async_payload`` writes the corrected labels to DynamoDB (the source of
    truth), but the producer's initial words delta carried the PRE-grok PENDING
    labels and the DynamoDB-stream label updater no-ops when the word embedding
    isn't in Chroma yet. So we build a small words delta for ONLY the grok-affected
    words — with their now-final labels re-read from DynamoDB and the embeddings
    carried in the payload — and create a compaction run that merges it. The
    affected words' label metadata in Chroma is upserted to the corrected state;
    the producer's still-present lines delta is re-merged idempotently.

    Best-effort: returns the new compaction run_id, or None when the payload lacks
    the resync data (older payloads) or there is nothing to resync. Raises only on
    an unexpected error so the caller can decide whether to swallow it (the grok
    corrections are already durable in DynamoDB regardless).
    """
    lines_prefix = payload.get("lines_prefix")
    chromadb_bucket = payload.get("chromadb_bucket")
    affected_words_data = payload.get("affected_words") or []
    affected_embeddings = payload.get("affected_embeddings") or []
    if not (lines_prefix and chromadb_bucket and affected_words_data):
        return None  # nothing to resync (e.g. a v1 payload or no embeddings)

    # Heavy imports only on the path that actually resyncs.
    import uuid

    import boto3
    from receipt_chroma import (
        build_words_payload,
        create_compaction_run,
        upload_words_delta,
    )
    from receipt_dynamo.entities import ReceiptWord

    image_id = payload["image_id"]
    receipt_id = payload["receipt_id"]
    words = [ReceiptWord(**d) for d in affected_words_data]
    affected_keys = {(w.line_id, w.word_id) for w in words}

    # Re-read the FINAL label state for the affected words (post-grok) so the
    # delta reflects corrections/invalidations, not the stale payload snapshot.
    all_labels, _ = dynamo.list_receipt_word_labels_for_receipt(image_id, receipt_id)
    final_labels = [
        lab for lab in all_labels if (lab.line_id, lab.word_id) in affected_keys
    ]

    word_payload, _ = build_words_payload(
        receipt_words=words,
        word_embeddings_list=affected_embeddings,
        word_labels=final_labels,
        merchant_name=payload.get("merchant_name"),
    )
    run_id = str(uuid.uuid4())
    words_prefix = upload_words_delta(
        word_payload=word_payload,
        run_id=run_id,
        chromadb_bucket=chromadb_bucket,
        s3_client=boto3.client("s3"),
    )
    create_compaction_run(
        run_id=run_id,
        image_id=image_id,
        receipt_id=receipt_id,
        lines_prefix=lines_prefix,
        words_prefix=words_prefix,
        dynamo_client=dynamo,
    )
    return run_id

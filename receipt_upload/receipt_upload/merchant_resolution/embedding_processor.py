"""
Merchant-resolving embedding processor for unified upload container.

Chroma-free (teardown PR #4): embeddings are computed once per receipt and
persisted as native DynamoDB ``*_EMBEDDING`` items; every similarity read
goes through the ``receipt_embeddings`` vector-search seam (DynamoDB
SearchVectors). The snapshot/delta/CompactionRun machinery is gone.

Phase 1: Embed (one batched OpenAI call for visual rows + words)
Phase 1b: Write native DynamoDB embedding items (THE persistence step —
          a failure here fails the receipt so ingest can retry)
Phase 2: Parallel pipelines
- Lines Pipeline: merchant resolution → section assignment/verification
- Words Pipeline: label hygiene → validation (abstains to LLM) → LLM

Phase 3: Enqueue deferred LLM validation (async mode only)
Phase 4: Enrich receipt place

Tracing:
- The process_embeddings method creates a parent trace per receipt
- Child traces for each phase nest under the parent
"""

import json
import logging
import os
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

import boto3
from receipt_agent.constants import CORE_LABELS
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptLine, ReceiptWord, ReceiptWordLabel

from receipt_upload.label_validation import (
    LightweightLabelValidator,
)
from receipt_upload.label_validation.amount_classifier import (
    classify_amount_labels,
)
from receipt_upload.label_validation.label_normalization import (
    normalize_label_alias,
)
from receipt_upload.label_validation.langsmith_logging import (
    log_merchant_resolution,
)
from receipt_upload.label_validation.llm_runner import (
    apply_async_payload,
    build_async_payload,
    run_llm_validation_sync,
)
from receipt_upload.merchant_resolution.dynamo_embedding_write import (
    write_precomputed_embeddings,
)
from receipt_upload.merchant_resolution.resolver import (
    MerchantResolver,
    MerchantResult,
)
from receipt_upload.merchant_resolution.resolver import (
    redact_pii as _redact_pii,
)

logger = logging.getLogger(__name__)


def _llm_validation_async_enabled() -> bool:
    """True when grok validation should be deferred to the async consumer.

    Off by default — the words worker validates inline, exactly as before. When
    ``LLM_VALIDATION_ASYNC=true`` the worker hands the (slow) LLM step to a
    separate Lambda and returns ~10s sooner. Gated so the default path is
    unchanged and the async lambda can be enabled per-stack once deployed.
    """
    return os.environ.get("LLM_VALIDATION_ASYNC", "").strip().lower() == "true"


def _enqueue_async_llm_validation(
    *,
    payload: Dict[str, Any],
    image_id: str,
    receipt_id: int,
    run_id: str,
    staging_bucket: str,
) -> None:
    """Stage the grok payload on S3 and enqueue a pointer for the consumer.

    The payload (word context + pre-computed similar-evidence + the pending
    label entities) is too large to recompute downstream, so it goes to S3;
    the SQS message is just a small pointer. Raises on any failure so the
    caller can fall back to inline validation (labels are never left
    dangling PENDING).
    """
    queue_url = (os.environ.get("LLM_VALIDATION_QUEUE_URL") or "").strip()
    if not queue_url:
        raise RuntimeError("LLM_VALIDATION_QUEUE_URL is not set")
    if not staging_bucket:
        raise RuntimeError("no staging bucket configured for async LLM")

    key = f"llm-validation/{run_id}/{image_id}_{receipt_id:05d}.json"
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=staging_bucket,
        Key=key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )
    sqs = boto3.client("sqs")
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(
            {
                "s3_bucket": staging_bucket,
                "s3_key": key,
                "image_id": image_id,
                "receipt_id": receipt_id,
            }
        ),
    )


# Currency-column roles that the deterministic geometry pass assigns by POSITION,
# not by word text: which column a price sits in is what makes it a line total vs
# a per-unit price vs a quantity. The text-only LLM validator cannot see columns
# and demonstrably flips these (a right-column LINE_TOTAL re-tagged UNIT_PRICE on
# the Trader Joe's June21 receipt). We trust geometry for these and keep them off
# the LLM hand-off — which also shrinks grok's payload and the upload critical
# path.
_GEOMETRY_SPATIAL_ROLES = {"LINE_TOTAL", "UNIT_PRICE", "QUANTITY"}
_GEOMETRY_PROPOSER = "geometry_line_items"

# Resolver logs are already PII-redacted at the source (resolver._log); the
# replay loop re-applies redact_pii (imported above) as defense-in-depth.


def _embed_receipt_vectors(
    lines: List[ReceiptLine],
    words: List[ReceiptWord],
    openai_client: Any = None,
    model: Optional[str] = None,
    openai_api_key: Optional[str] = None,
) -> Tuple[List[List[float]], List[List[int]], List[List[float]]]:
    """Embed a receipt's visual rows and words in one batched OpenAI call.

    Returns ``(row_embeddings, row_line_ids_list, word_embeddings_list)``
    with ``word_embeddings_list[i]`` aligned to ``words[i]`` and the row
    lists aligned to each other. Uses the SAME ingest formatting as the
    native corpus (visual-row context for lines, spatial context for
    words), so query vectors and stored vectors share one representation.
    """
    # pylint: disable=import-outside-toplevel
    from receipt_embeddings.formatting import (
        format_word_context_embedding_input,
        get_row_embedding_inputs,
    )
    from receipt_embeddings.openai.realtime import embed_texts

    if openai_client is None:
        from openai import OpenAI

        openai_client = (
            OpenAI(api_key=openai_api_key) if openai_api_key else OpenAI()
        )

    model = model or os.environ.get(
        "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
    )

    row_inputs: List[str] = []
    row_line_ids_list: List[List[int]] = []
    for embedding_input, line_ids in get_row_embedding_inputs(lines):
        row_inputs.append(embedding_input)
        row_line_ids_list.append([int(v) for v in line_ids])

    word_inputs = [
        format_word_context_embedding_input(word, words) for word in words
    ]

    inputs = row_inputs + word_inputs
    vectors = embed_texts(openai_client, inputs, model) if inputs else []
    if len(vectors) != len(inputs):
        raise RuntimeError(
            f"embedding batch returned {len(vectors)} vectors for "
            f"{len(inputs)} inputs"
        )
    row_embeddings = vectors[: len(row_inputs)]
    word_embeddings_list = vectors[len(row_inputs) :]
    return row_embeddings, row_line_ids_list, word_embeddings_list


def _prepare_pending_core_labels(
    dynamo: Any,
    word_labels: List[ReceiptWordLabel],
    label_proposed_by: str,
    words: Optional[List[ReceiptWord]] = None,
) -> List[ReceiptWordLabel]:
    """Normalize pending labels before validation starts."""
    existing_keys = {
        (label.line_id, label.word_id, label.label) for label in word_labels
    }
    pending_core_labels: List[ReceiptWordLabel] = []
    allow_amount_llm_fallback = words is not None
    amount_classifications = (
        classify_amount_labels(words, word_labels) if words is not None else {}
    )

    for label in list(word_labels):
        if label.validation_status != ValidationStatus.PENDING.value:
            continue
        if label.label == "O":
            continue
        if label.label in CORE_LABELS:
            pending_core_labels.append(label)
            continue

        if label.label == "AMOUNT":
            amount_decision = amount_classifications.get(
                (label.line_id, label.word_id)
            )
            if amount_decision is None:
                # Keep AMOUNT only as transient LLM input. Later write paths
                # delete it unless the LLM replaces it with a CORE_LABEL.
                if allow_amount_llm_fallback:
                    pending_core_labels.append(label)
                    continue
                dynamo.delete_receipt_word_label(label)
                word_labels.remove(label)
                continue

            original_label = label.label
            dynamo.delete_receipt_word_label(label)
            word_labels.remove(label)

            mapped_key = (label.line_id, label.word_id, amount_decision.label)
            if mapped_key in existing_keys:
                continue

            new_label = ReceiptWordLabel(
                image_id=label.image_id,
                receipt_id=label.receipt_id,
                line_id=label.line_id,
                word_id=label.word_id,
                label=amount_decision.label,
                reasoning=amount_decision.reason,
                timestamp_added=datetime.now(timezone.utc),
                validation_status=ValidationStatus.VALID.value,
                label_proposed_by=f"{label_proposed_by}:{original_label}:deterministic",
                label_consolidated_from=original_label,
            )
            dynamo.add_receipt_word_label(new_label)
            word_labels.append(new_label)
            existing_keys.add(mapped_key)
            continue

        mapped_label = normalize_label_alias(label.label)
        original_label = label.label
        dynamo.delete_receipt_word_label(label)
        word_labels.remove(label)

        if mapped_label is None:
            continue

        mapped_key = (label.line_id, label.word_id, mapped_label)
        if mapped_key in existing_keys:
            continue

        new_label = ReceiptWordLabel(
            image_id=label.image_id,
            receipt_id=label.receipt_id,
            line_id=label.line_id,
            word_id=label.word_id,
            label=mapped_label,
            reasoning=(
                f"Mapped from non-core label '{original_label}' before "
                "validation."
            ),
            timestamp_added=datetime.now(timezone.utc),
            validation_status=ValidationStatus.PENDING.value,
            label_proposed_by=f"{label_proposed_by}:{original_label}",
            label_consolidated_from=original_label,
        )
        dynamo.add_receipt_word_label(new_label)
        word_labels.append(new_label)
        existing_keys.add(mapped_key)
        pending_core_labels.append(new_label)

    return pending_core_labels


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


def _get_traceable():
    """Get the traceable decorator if langsmith is available."""
    try:
        from langsmith.run_helpers import traceable

        return traceable
    except ImportError:

        # Return a no-op decorator if langsmith not installed
        def noop_decorator(*args, **kwargs):
            def wrapper(fn):
                return fn

            return wrapper

        return noop_decorator


def _get_label_validation_project() -> str:
    """Get the Langsmith project name for label validation from env var."""
    return os.environ.get("LANGCHAIN_PROJECT", "receipt-label-validation")


def _get_phase2_executor_class() -> Type:
    """Select executor for Phase 2 pipelines.

    Lambda environments frequently fail to start ProcessPoolExecutor due to
    missing shared memory and sandbox constraints. Prefer threads in Lambda.
    """
    is_lambda = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
    if not is_lambda:
        return ProcessPoolExecutor

    try:
        from langsmith.utils import ContextThreadPoolExecutor

        return ContextThreadPoolExecutor
    except ImportError:
        from concurrent.futures import ThreadPoolExecutor

        return ThreadPoolExecutor


def _log(msg: str) -> None:
    """Log message with immediate flush for CloudWatch visibility."""
    print(f"[MERCHANT_EMBEDDING_PROCESSOR] {msg}", flush=True)
    logger.info(msg)


# =============================================================================
# Module-level worker functions for ProcessPoolExecutor
# These must be at module level to be picklable for multiprocessing
# =============================================================================


def _run_lines_pipeline_worker(
    lines_data: List[Dict[str, Any]],
    words_data: List[Dict[str, Any]],
    word_labels_data: List[Dict[str, Any]],
    row_embeddings: List[List[float]],
    row_line_ids_list: List[List[int]],
    image_id: str,
    receipt_id: int,
    table_name: str,
    google_places_api_key: Optional[str],
    langsmith_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Worker function for lines pipeline (runs in separate process).

    Runs merchant resolution and section assignment/verification against
    the DynamoDB vector-search seam. Returns serializable dict with results.

    Args:
        row_embeddings: Embeddings for each visual row
        row_line_ids_list: List of line_ids for each visual row
        langsmith_headers: Optional headers from parent RunTree for trace context
    """
    # Import inside worker to avoid pickling issues
    from receipt_dynamo import DynamoClient
    from receipt_dynamo.entities import (
        ReceiptLine,
        ReceiptWord,
        ReceiptWordLabel,
    )
    from receipt_embeddings.formatting import build_receipt_rows

    from receipt_upload.merchant_resolution.resolver import (
        MerchantResolver,
        merchant_name_matches_receipt,
    )
    from receipt_upload.section_assignment import (
        VERIFIABLE_MODEL_SOURCES,
        assign_and_persist_sections,
    )
    from receipt_upload.section_verifier import verify_receipt_sections
    from receipt_upload.vector_search import vector_search_client

    def _do_lines_work() -> Dict[str, Any]:
        """Run the lines pipeline: merchant resolution + section pipeline."""
        # Reconstruct entities from dicts using **unpacking
        lines = [ReceiptLine(**d) for d in lines_data]
        words = [ReceiptWord(**d) for d in words_data]
        word_labels = [ReceiptWordLabel(**d) for d in word_labels_data]

        # Build embedding cache: all lines in a row share the same embedding
        line_embedding_cache: Dict[int, List[float]] = {}
        for row_line_ids, emb in zip(
            row_line_ids_list, row_embeddings, strict=True
        ):
            for line_id in row_line_ids:
                line_embedding_cache[line_id] = emb

        # Create resolver and run merchant resolution
        dynamo = DynamoClient(table_name)

        # One vector-search backend for the whole worker, bound to the SAME
        # table as the rest of the session (never the from_env fallback).
        vector_client = vector_search_client(
            None,
            dynamodb_client=dynamo._client,  # pylint: disable=protected-access
            table_name=table_name,
        )

        # Create places client if API key provided
        places_client = None
        if google_places_api_key:
            try:
                from receipt_places import PlacesClient

                places_client = PlacesClient(api_key=google_places_api_key)
            except ImportError:
                pass

        resolver = MerchantResolver(
            dynamo_client=dynamo,
            places_client=places_client,
            vector_client=vector_client,
        )

        # Capture the resolver's stdout (its `_log` print()s, incl. the
        # Tier attempts) so the main process can re-emit it — but ONLY when
        # running in a real subprocess (ProcessPoolExecutor child), whose
        # stdout does NOT reach CloudWatch on its own. In Lambda, Phase 2 uses
        # a THREAD executor, where redirect_stdout mutates process-global
        # sys.stdout and would swallow concurrent prints from the words
        # pipeline. In that case skip the capture: the resolver's prints reach
        # CloudWatch directly (same process).
        import contextlib
        import io as _io
        import multiprocessing

        in_subprocess = multiprocessing.current_process().name != "MainProcess"
        resolver_log_buf = _io.StringIO()
        capture_cm = (
            contextlib.redirect_stdout(resolver_log_buf)
            if in_subprocess
            else contextlib.nullcontext()
        )
        with capture_cm:
            merchant_result = resolver.resolve(
                lines_client=None,
                lines=lines,
                words=words,
                image_id=image_id,
                receipt_id=receipt_id,
                line_embeddings=line_embedding_cache,
                word_labels=word_labels,
            )

        # Write-time validation: verify merchant_name against
        # receipt OCR text before persisting it. This prevents
        # poisoned names from propagating.
        validated_merchant_name = merchant_result.merchant_name
        if validated_merchant_name and not merchant_name_matches_receipt(
            validated_merchant_name, lines
        ):
            logging.getLogger(__name__).warning(
                "Write-time validation: merchant_name %r rejected "
                "— no token overlap with receipt OCR text for %s#%d",
                validated_merchant_name,
                image_id,
                receipt_id,
            )
            validated_merchant_name = None

        # D2: assign persisted visual rows synchronously so section
        # metadata is deterministic. D1 guarantees rows at ingest;
        # reconstruction keeps legacy/dev replays compatible.
        persisted_rows = dynamo.get_receipt_rows_from_receipt(
            image_id, receipt_id
        )
        row_source = "persisted"
        if not persisted_rows:
            persisted_rows = build_receipt_rows(lines, words)
            row_source = "reconstructed"
        # Structured provenance: reconstructed rows mean D1 ingest did not
        # persist rows for this receipt (legacy/dev replay) — observable,
        # never behavior-changing.
        logger.info(
            "[ROW_PROVENANCE] image_id=%s receipt_id=%s row_source=%s "
            "row_count=%d",
            image_id,
            receipt_id,
            row_source,
            len(persisted_rows),
        )
        created_sections, section_by_line = assign_and_persist_sections(
            dynamo,
            persisted_rows,
            lines,
            validated_merchant_name,
        )

        rows_by_id = {row.row_id: row for row in persisted_rows}
        embedding_rows = [
            rows_by_id[row_line_ids[0]] for row_line_ids in row_line_ids_list
        ]
        verification_stats: Dict[str, Any] = {}
        try:
            verified = verify_receipt_sections(
                None,
                dynamo,
                embedding_rows,
                row_embeddings,
                vector_client=vector_client,
            )
            from collections import Counter as _Counter

            # NOTE: exact model_source match against the deterministic
            # producer set (also in
            # section_verifier._record_verification) -- the cloud
            # assigner and the Mac worker running the same assigner on
            # device. Provenance must ship in a separate additive field,
            # never a model_source suffix.
            status_counts = _Counter(
                section.verification_status
                for section in dynamo.get_receipt_sections_from_receipt(
                    image_id, receipt_id
                )
                if section.model_source in VERIFIABLE_MODEL_SOURCES
                and section.verification_status
            )
            verification_stats = {
                "verified_row_count": len(verified),
                "verification_agreed_count": status_counts.get("AGREED", 0),
                "verification_disagreement_count": status_counts.get(
                    "DISAGREED", 0
                ),
                "verification_abstained_count": status_counts.get(
                    "ABSTAINED", 0
                ),
            }
        # Verification is independent evidence; an unavailable neighbor
        # index must not discard the deterministic section proposal.
        except Exception as error:
            logging.getLogger(__name__).exception(
                "Section KNN verification failed for %s#%d: %s",
                image_id,
                receipt_id,
                error,
            )
            verification_stats = {"verification_error": str(error)}

        # Return serializable result
        return {
            "success": True,
            # Metrics-only observability (no behavior change): visual-row
            # provenance and deterministic section-proposal stats.
            "row_count": len(persisted_rows),
            "row_source": row_source,
            "section_proposed_count": len(created_sections),
            "section_mean_confidence": (
                sum(s.confidence or 0.0 for s in created_sections)
                / len(created_sections)
                if created_sections
                else None
            ),
            "merchant_name": validated_merchant_name,
            "place_id": merchant_result.place_id,
            "resolution_tier": merchant_result.resolution_tier,
            "confidence": merchant_result.confidence,
            "phone": merchant_result.phone,
            "address": merchant_result.address,
            "source_image_id": merchant_result.source_image_id,
            "source_receipt_id": merchant_result.source_receipt_id,
            **verification_stats,
            "similarity_matches": [
                {
                    "image_id": m.image_id,
                    "receipt_id": m.receipt_id,
                    "merchant_name": m.merchant_name,
                    "embedding_similarity": m.embedding_similarity,
                    "metadata_boost": m.metadata_boost,
                    "total_confidence": m.total_confidence,
                }
                for m in (merchant_result.similarity_matches or [])[:5]
            ],
            "resolver_logs": resolver_log_buf.getvalue(),
        }

    # Execute with LangSmith tracing context if headers provided
    # tracing_context(parent=...) can accept headers directly for distributed tracing
    # CRITICAL: Must flush traces before process exits - each process has its own
    # background thread for sending traces to LangSmith
    if langsmith_headers:
        try:
            import logging
            import os

            from langsmith import Client, tracing_context

            log = logging.getLogger(__name__)

            # Get project name to ensure child traces go to same project
            project = os.environ.get(
                "LANGCHAIN_PROJECT", "receipt-label-validation"
            )
            log.info(
                "[LINES_WORKER] Setting up tracing: project=%s, headers=%s",
                project,
                list(langsmith_headers.keys()),
            )

            # Pass headers directly to tracing_context with explicit project and enabled
            with tracing_context(
                parent=langsmith_headers,
                project_name=project,
                enabled=True,
            ):
                traced_result = _do_lines_work()

            # CRITICAL: Flush traces before process exits
            # Each child process has its own LangSmith client and background thread
            log.info("[LINES_WORKER] Flushing traces before process exit")
            Client().flush()
            return traced_result
        except Exception as e:
            import logging

            logging.getLogger(__name__).exception(
                "[LINES_WORKER] ERROR in tracing: %s", e
            )
    return _do_lines_work()


def _run_words_pipeline_worker(
    words_data: List[Dict[str, Any]],
    word_labels_data: List[Dict[str, Any]],
    word_embeddings_list: List[List[float]],
    image_id: str,
    receipt_id: int,
    table_name: str,
    langsmith_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Worker function for words pipeline (runs in separate process).

    Runs label hygiene + validation. The lightweight similarity validator
    abstains (its Chroma surface is retired), so pending labels route to
    the LLM validator exactly as they did in production before teardown.
    """
    # Import inside worker to avoid pickling issues
    from receipt_dynamo import DynamoClient
    from receipt_dynamo.constants import ValidationStatus
    from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

    from receipt_upload.vector_search import vector_search_client

    def _do_words_work() -> Dict[str, Any]:
        # Reconstruct entities from dicts using **unpacking
        words = [ReceiptWord(**d) for d in words_data]
        word_labels = [ReceiptWordLabel(**d) for d in word_labels_data]

        # Build embedding cache
        word_embedding_cache: Dict[Tuple[int, int], List[float]] = {
            (w.line_id, w.word_id): emb
            for w, emb in zip(words, word_embeddings_list, strict=True)
        }

        # Run label validation
        dynamo = DynamoClient(table_name)
        vector_client = vector_search_client(
            None,
            dynamodb_client=dynamo._client,  # pylint: disable=protected-access
            table_name=table_name,
        )
        validation_stats: Dict[str, Any] = {}
        # Built when LLM_VALIDATION_ASYNC is on; enqueued by the caller AFTER
        # the native embeddings are durable. None otherwise.
        async_llm_payload: Optional[Dict[str, Any]] = None

        pending_labels = _prepare_pending_core_labels(
            dynamo=dynamo,
            word_labels=word_labels,
            label_proposed_by="non_core_label_guard",
            words=words,
        )

        # Deterministic geometry line-item proposals (PRODUCT_NAME / LINE_TOTAL
        # / UNIT_PRICE). The first-pass model doesn't emit these — geometry
        # bounds the line-item region by the receipt's own header/totals anchor
        # labels and labels by column. Emitted as PENDING so the validators
        # below confirm them, same as any other proposed label.
        from receipt_upload.line_items import (
            dedupe_grand_total,
            propose_line_item_labels,
            propose_product_names,
            reclassify_mislabeled_totals,
        )

        # Receipts restate the grand total several times (balance / total /
        # tendered amount); the first-pass model tags every copy GRAND_TOTAL.
        # Keep one canonical copy and invalidate the equal-valued duplicates
        # BEFORE validation, so they neither corrupt arithmetic nor inflate the
        # LLM validator's workload. Conservative: only exact-value duplicates.
        #
        # The section layer (swift-worker-v1) has usually already decided
        # which row is the printed TOTAL_LINE vs the PAYMENT (tender)
        # restatement, so hand dedupe the sections as its primary tiebreak
        # — keyword/lowest-y alone kept the tender-row copy and invalidated
        # the printed "TOTAL" row. Sections are advisory: any read failure
        # (or a receipt with none yet) falls back to the old election.
        try:
            receipt_sections = dynamo.get_receipt_sections_from_receipt(
                image_id, receipt_id
            )
        except Exception:  # pylint: disable=broad-except
            receipt_sections = []
        for dup in dedupe_grand_total(
            words, word_labels, sections=receipt_sections
        ):
            dup.validation_status = ValidationStatus.INVALID.value
            dup.label_proposed_by = "dedupe_grand_total"
            dup.reasoning = (
                "Redundant GRAND_TOTAL: the receipt restates the final total "
                "on multiple rows; the canonical copy (TOTAL_LINE section / "
                "keyword-anchored / lowest) is kept."
            )
            dynamo.update_receipt_word_label(dup)
            _remove_label_from_list(pending_labels, dup)

        # First-pass models emit SUBTOTAL/TAX when line totals coincidentally
        # sum to the grand total and no Subtotal/Tax keyword anchors a real
        # totals block (the Trader Joe's IMG_2826 case). Reclassify those
        # PENDING labels to LINE_TOTAL — but ONLY when arithmetic proves it
        # (Σ line totals == GRAND_TOTAL only with them counted as line items).
        # Human VALID/INVALID labels are never touched.
        reclassifications, locked_line_totals = reclassify_mislabeled_totals(
            words, word_labels
        )
        for old_label, new_label in reclassifications:
            # Invalidate (don't delete) the mislabeled total — preserves the
            # audit trail and is consistent with "INVALID currency labels are
            # deliberate" — then add the arithmetic-confirmed LINE_TOTAL.
            old_label.validation_status = ValidationStatus.INVALID.value
            old_label.reasoning = (
                f"Reclassified to LINE_TOTAL by {new_label.label_proposed_by}: "
                "this price is a line-item total, not a receipt total "
                "(arithmetic reconciliation)."
            )
            dynamo.update_receipt_word_label(old_label)
            # Drop the invalidated total from the pending set so the
            # validators don't re-validate it back to SUBTOTAL/TAX.
            _remove_label_from_list(pending_labels, old_label)
            dynamo.add_receipt_word_label(new_label)
            word_labels.append(new_label)
        for lt_label in locked_line_totals:
            # Arithmetic confirms these are line totals; lock them VALID and
            # pull them from pending so the LLM can't "correct" them to TAX.
            lt_label.validation_status = ValidationStatus.VALID.value
            lt_label.label_proposed_by = "arithmetic_totals_reclass"
            lt_label.reasoning = "Arithmetic-confirmed line total (Σ line totals == GRAND_TOTAL)."
            dynamo.update_receipt_word_label(lt_label)
            _remove_label_from_list(pending_labels, lt_label)

        for li_label in propose_line_item_labels(words, word_labels):
            dynamo.add_receipt_word_label(li_label)
            word_labels.append(li_label)
            # Arithmetic-verified line items (Σ line_total = receipt total) are
            # already VALID; only route the unverified PENDING ones through the
            # validators.
            if li_label.validation_status == ValidationStatus.PENDING.value:
                pending_labels.append(li_label)

        # Semantic recovery: the model emits no PRODUCT_NAME and geometry only
        # catches product names that share an OCR row with a price. A kNN over
        # validated product words (UNSCOPED — merchant-scoping hurts recall)
        # proposes the rest as PENDING for the validators to confirm.
        for pn_label in propose_product_names(
            words,
            word_labels,
            None,
            word_embedding_cache,
            vector_client=vector_client,
        ):
            dynamo.add_receipt_word_label(pn_label)
            word_labels.append(pn_label)
            pending_labels.append(pn_label)

        if pending_labels:
            from receipt_upload.label_validation import ValidationDecision

            # No similarity backend: the validator abstains on every label
            # (KEEP_PENDING), which routes them to the LLM — identical to
            # the retired words collection whose filter surface matched
            # nothing in production. The cache still serves the LLM
            # evidence path's embedding lookups.
            lightweight_validator = LightweightLabelValidator(
                words_client=None,
                word_embeddings=word_embedding_cache,
            )

            similarity_validated = 0
            llm_needed = []

            def _run_similarity_validation_loop():
                """Run similarity validation for all pending labels."""
                nonlocal similarity_validated
                for label in pending_labels:
                    word = next(
                        (
                            w
                            for w in words
                            if w.line_id == label.line_id
                            and w.word_id == label.word_id
                        ),
                        None,
                    )
                    if not word:
                        continue

                    if label.label == "AMOUNT":
                        llm_needed.append((word, label))
                        continue

                    result = lightweight_validator.validate_label(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=label.line_id,
                        word_id=label.word_id,
                        predicted_label=label.label,
                    )

                    if result.decision in (
                        ValidationDecision.AUTO_VALIDATE,
                        ValidationDecision.AUTO_INVALID,
                    ):
                        # Update the label object with validation results
                        label.validation_status = (
                            ValidationStatus.VALID.value
                            if result.decision
                            == ValidationDecision.AUTO_VALIDATE
                            else ValidationStatus.INVALID.value
                        )
                        label.label_proposed_by = (
                            f"chroma_{result.decision.value}"
                        )
                        dynamo.update_receipt_word_label(label)
                        similarity_validated += 1
                    else:
                        # Don't let the text-only LLM reassign a geometry
                        # column role (LINE_TOTAL/UNIT_PRICE/QUANTITY): the
                        # role is positional, grok flips them wrong, and
                        # skipping them keeps grok's payload (and latency)
                        # down. Commit a TERMINAL status (not PENDING) so an
                        # abstained geometry role isn't stuck forever —
                        # but as NEEDS_REVIEW, not VALID: neither arithmetic
                        # nor similarity confirmed it, so we don't assert it
                        # as validated, just remove it from the PENDING/LLM
                        # path.
                        if (
                            label.label in _GEOMETRY_SPATIAL_ROLES
                            and label.label_proposed_by == _GEOMETRY_PROPOSER
                        ):
                            label.validation_status = (
                                ValidationStatus.NEEDS_REVIEW.value
                            )
                            label.label_proposed_by = "geometry_trusted"
                            dynamo.update_receipt_word_label(label)
                            similarity_validated += 1
                            continue
                        llm_needed.append((word, label))

            # Apply traceable decorator if available
            try:
                import os

                from langsmith.run_helpers import traceable

                project = os.environ.get(
                    "LANGCHAIN_PROJECT", "receipt-label-validation"
                )
                traced_loop = traceable(
                    name="chroma_label_validation",
                    project_name=project,
                    metadata={
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "pending_count": len(pending_labels),
                    },
                )(_run_similarity_validation_loop)
                traced_loop()
            except ImportError:
                _run_similarity_validation_loop()

            # LLM (grok) validation for labels similarity couldn't
            # auto-resolve. This is the slowest single step on the upload
            # critical path (~10s synchronous LLM call). Default: validate
            # inline. When LLM_VALIDATION_ASYNC is on, BUILD the hand-off
            # payload here but do NOT enqueue yet — the caller enqueues only
            # after the native embeddings are durable, so the consumer can't
            # write label changes before the word embeddings exist
            # downstream (see Phase 3 in _process_embeddings_impl).
            llm_validated = 0
            llm_deferred = 0
            if llm_needed:
                if _llm_validation_async_enabled():
                    try:
                        async_llm_payload = build_async_payload(
                            llm_needed=llm_needed,
                            words=words,
                            image_id=image_id,
                            receipt_id=receipt_id,
                            table_name=table_name,
                            lightweight_validator=lightweight_validator,
                            word_embedding_cache=word_embedding_cache,
                            merchant_name=None,
                        )
                        llm_deferred = len(llm_needed)
                    except Exception as e:
                        # Never leave labels dangling PENDING: if the payload
                        # build fails, validate inline. Distinct marker so a
                        # log metric filter can alarm.
                        logger.warning(
                            "[LLM_ASYNC_FALLBACK] payload build failed for "
                            "%s#%s (%s); running validation inline",
                            image_id,
                            receipt_id,
                            e,
                        )
                        llm_validated = run_llm_validation_sync(
                            llm_needed=llm_needed,
                            words=words,
                            image_id=image_id,
                            receipt_id=receipt_id,
                            dynamo=dynamo,
                            word_labels=word_labels,
                            lightweight_validator=lightweight_validator,
                            word_embedding_cache=word_embedding_cache,
                        )
                else:
                    llm_validated = run_llm_validation_sync(
                        llm_needed=llm_needed,
                        words=words,
                        image_id=image_id,
                        receipt_id=receipt_id,
                        dynamo=dynamo,
                        word_labels=word_labels,
                        lightweight_validator=lightweight_validator,
                        word_embedding_cache=word_embedding_cache,
                    )

            validation_stats = {
                "pending_labels": len(pending_labels),
                "chroma_validated": similarity_validated,
                "llm_validated": llm_validated,
                "llm_deferred": llm_deferred,
            }

        return {
            "success": True,
            "async_llm_payload": async_llm_payload,
            **validation_stats,
        }

    # Execute with LangSmith tracing context if headers provided
    # tracing_context(parent=...) can accept headers directly for distributed tracing
    # CRITICAL: Must flush traces before process exits - each process has its own
    # background thread for sending traces to LangSmith
    if langsmith_headers:
        try:
            import logging
            import os

            from langsmith import Client, tracing_context

            log = logging.getLogger(__name__)

            # Get project name to ensure child traces go to same project
            project = os.environ.get(
                "LANGCHAIN_PROJECT", "receipt-label-validation"
            )
            log.info(
                "[WORDS_WORKER] Setting up tracing: project=%s, headers=%s",
                project,
                list(langsmith_headers.keys()),
            )

            # Pass headers directly to tracing_context with explicit project and enabled
            with tracing_context(
                parent=langsmith_headers,
                project_name=project,
                enabled=True,
            ):
                traced_result = _do_words_work()

            # CRITICAL: Flush traces before process exits
            # Each child process has its own LangSmith client and background thread
            log.info("[WORDS_WORKER] Flushing traces before process exit")
            Client().flush()
            return traced_result
        except Exception as e:
            import logging

            logging.getLogger(__name__).exception(
                "[WORDS_WORKER] ERROR in tracing: %s", e
            )
    return _do_words_work()


class MerchantResolvingEmbeddingProcessor:
    """
    Generate embeddings and resolve merchant information for a receipt.

    This processor:
    1. Generates embeddings with one batched OpenAI call
    2. Persists them as native DynamoDB embedding items (the vector corpus)
    3. Resolves merchant information via DynamoDB vector search
    4. Updates DynamoDB with merchant information
    """

    def __init__(
        self,
        table_name: str,
        chromadb_bucket: Optional[str] = None,
        google_places_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        llm_staging_bucket: Optional[str] = None,
    ):
        """
        Initialize the processor.

        Args:
            table_name: DynamoDB table name
            chromadb_bucket: Deprecated (Chroma teardown); accepted and
                ignored so existing callers keep working
            google_places_api_key: Google Places API key for Tier 2 resolution
            openai_api_key: OpenAI API key for embeddings
            llm_staging_bucket: S3 bucket for staging the async-LLM payload
                (defaults to the ``RAW_BUCKET`` env var)
        """
        del chromadb_bucket  # retired with the Chroma write path
        self.dynamo = DynamoClient(table_name)
        self.openai_api_key = openai_api_key
        self.google_places_api_key = google_places_api_key
        self.llm_staging_bucket = (
            llm_staging_bucket or os.environ.get("RAW_BUCKET") or ""
        )

        # Initialize Places client if API key provided
        self.places_client = None
        if google_places_api_key:
            try:
                from receipt_places import PlacesClient

                self.places_client = PlacesClient(
                    api_key=google_places_api_key
                )
            except ImportError:
                _log("WARNING: receipt_places not available")

        # Initialize merchant resolver
        self.merchant_resolver = MerchantResolver(
            dynamo_client=self.dynamo,
            places_client=self.places_client,
        )

    def process_embeddings(
        self,
        image_id: str,
        receipt_id: int,
        lines: Optional[List[ReceiptLine]] = None,
        words: Optional[List[ReceiptWord]] = None,
    ) -> Dict[str, Any]:
        """
        Generate embeddings, resolve merchant, and enrich receipt.

        This method creates a parent Langsmith trace for the entire receipt
        processing pipeline. Child traces for merchant resolution and label
        validation will nest under this parent.

        Args:
            image_id: Receipt's image_id
            receipt_id: Receipt's receipt_id
            lines: Optional list of ReceiptLine entities (fetched if not provided)
            words: Optional list of ReceiptWord entities (fetched if not provided)

        Returns:
            Dict with success status, merchant info, and native-write details
        """
        # Create traced wrapper for hierarchical tracing
        traceable = _get_traceable()

        @traceable(
            name="receipt_processing",
            project_name=_get_label_validation_project(),
            tags=["upload_lambda"],
            metadata={
                "image_id": image_id,
                "receipt_id": receipt_id,
            },
        )
        def _traced_process_embeddings(
            image_id: str,
            receipt_id: int,
            lines: Optional[List[ReceiptLine]],
            words: Optional[List[ReceiptWord]],
        ) -> Dict[str, Any]:
            return self._process_embeddings_impl(
                image_id=image_id,
                receipt_id=receipt_id,
                lines=lines,
                words=words,
            )

        return _traced_process_embeddings(
            image_id=image_id,
            receipt_id=receipt_id,
            lines=lines,
            words=words,
        )

    def _process_embeddings_impl(
        self,
        image_id: str,
        receipt_id: int,
        lines: Optional[List[ReceiptLine]] = None,
        words: Optional[List[ReceiptWord]] = None,
    ) -> Dict[str, Any]:
        """
        Implementation of process_embeddings using parallel pipelines.

        This implementation runs TWO PARALLEL PIPELINES:
        - Lines Pipeline: merchant resolution → sections
        - Words Pipeline: label validation

        This allows merchant resolution to complete as soon as lines are ready.
        """
        # Fetch lines/words if not provided
        if lines is None or words is None:
            lines = self.dynamo.list_receipt_lines_from_receipt(
                image_id, receipt_id
            )
            words = self.dynamo.list_receipt_words_from_receipt(
                image_id, receipt_id
            )
            _log(
                f"Fetched {len(lines)} lines and {len(words)} words from DynamoDB"
            )
        else:
            _log(f"Using provided {len(lines)} lines and {len(words)} words")

        # Get word labels for enrichment
        word_labels: List[ReceiptWordLabel] = []
        try:
            word_labels, _ = self.dynamo.list_receipt_word_labels_for_receipt(
                image_id, receipt_id
            )
        except Exception as e:
            _log(f"Could not fetch word labels: {e}")

        # Get existing receipt place for merchant context
        receipt_place = None
        try:
            receipt_place = self.dynamo.get_receipt_place(image_id, receipt_id)
        except Exception as e:
            _log(f"Could not fetch receipt place: {e}")

        # Generate run_id for this processing run
        run_id = str(uuid.uuid4())
        _log(
            f"Creating embeddings for {image_id}#{receipt_id} "
            f"({len(lines)} lines, {len(words)} words) run_id={run_id}"
        )

        # =====================================================================
        # PHASE 1: Embed (one batched OpenAI call)
        # =====================================================================
        try:
            (
                row_embeddings,
                row_line_ids_list,
                word_embeddings_list,
            ) = _embed_receipt_vectors(
                lines, words, openai_api_key=self.openai_api_key
            )
            _log(
                "Phase 1 complete: generated embeddings "
                f"(rows={len(row_embeddings)}, words={len(word_embeddings_list)})"
            )
        except Exception as e:
            _log(f"ERROR: Failed to embed: {e}")
            logger.exception("Embedding failed")
            return {
                "success": False,
                "error": str(e),
                "merchant_found": False,
            }

        # =====================================================================
        # PHASE 1b: Persist native DynamoDB embedding items
        # =====================================================================
        # This IS the vector corpus now (Chroma teardown): a failed or
        # partial write fails the receipt so ingest retries, instead of
        # leaving the receipt invisible to SearchVectors.
        native_report = write_precomputed_embeddings(
            dynamo=self.dynamo,
            image_id=image_id,
            receipt_id=receipt_id,
            lines=lines,
            words=words,
            word_labels=word_labels,
            receipt_place=receipt_place,
            row_embeddings=row_embeddings,
            row_line_ids_list=row_line_ids_list,
            word_embeddings_list=word_embeddings_list,
        )
        native_write_ok = not (
            native_report.get("error") or native_report.get("failed")
        )
        if not native_write_ok:
            _log(f"ERROR: Native embedding write incomplete: {native_report}")
        else:
            _log(f"Phase 1b complete: native embeddings {native_report}")

        # Track resources for cleanup
        merchant_result = MerchantResult()
        validation_stats: Dict[str, Any] = {}
        lines_stats: Dict[str, Any] = {}

        try:
            # =================================================================
            # PHASE 2: Run parallel pipelines using ProcessPoolExecutor
            # Lines: merchant_resolution → sections
            # Words: label_validation
            #
            # ProcessPoolExecutor provides TRUE parallelism by running each
            # pipeline in a separate process, avoiding Python GIL limitations.
            # =================================================================
            _log("Starting Phase 2: parallel pipelines (ProcessPoolExecutor)")

            # Convert entities to dicts for pickling (required for multiprocessing)
            from dataclasses import asdict

            lines_data = [asdict(ln) for ln in lines]
            words_data = [asdict(w) for w in words]
            word_labels_data = [asdict(wl) for wl in word_labels]

            _log(
                f"Serialized {len(lines_data)} lines, {len(words_data)} words, {len(word_labels_data)} labels"
            )

            # Get table name from dynamo client
            table_name = self.dynamo.table_name

            # Get LangSmith tracing headers to propagate to child processes
            langsmith_headers: Optional[Dict[str, str]] = None
            try:
                from langsmith import get_current_run_tree

                current_run = get_current_run_tree()
                if current_run:
                    langsmith_headers = current_run.to_headers()
                    _log(
                        f"LangSmith trace context captured: run_id={current_run.id}, headers={list(langsmith_headers.keys())}"
                    )
                else:
                    _log(
                        "WARNING: get_current_run_tree() returned None - no parent trace context"
                    )
            except Exception as e:
                _log(f"Could not capture LangSmith context: {e}")

            # Run both pipelines in parallel. Use processes outside Lambda, threads in Lambda.
            executor_class = _get_phase2_executor_class()
            executor_name = executor_class.__name__
            with executor_class(max_workers=2) as executor:
                _log(
                    f"Submitting lines and words pipelines to {executor_name}"
                )

                lines_future = executor.submit(
                    _run_lines_pipeline_worker,
                    lines_data=lines_data,
                    words_data=words_data,
                    word_labels_data=word_labels_data,
                    row_embeddings=row_embeddings,
                    row_line_ids_list=row_line_ids_list,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    table_name=table_name,
                    google_places_api_key=self.google_places_api_key,
                    langsmith_headers=langsmith_headers,
                )

                words_future = executor.submit(
                    _run_words_pipeline_worker,
                    words_data=words_data,
                    word_labels_data=word_labels_data,
                    word_embeddings_list=word_embeddings_list,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    table_name=table_name,
                    langsmith_headers=langsmith_headers,
                )

                # Wait for both to complete
                for future in as_completed([lines_future, words_future]):
                    try:
                        future.result()
                    except Exception as e:
                        _log(f"WARNING: Pipeline failed: {e}")
                        logger.exception("Pipeline failed")

                # Get results and reconstruct objects
                try:
                    lines_result = lines_future.result()

                    # Observability-only stats from the lines pipeline: row
                    # provenance, section proposals, verification outcomes.
                    lines_stats = {
                        key: lines_result[key]
                        for key in (
                            "row_count",
                            "row_source",
                            "section_proposed_count",
                            "section_mean_confidence",
                            "verified_row_count",
                            "verification_agreed_count",
                            "verification_disagreement_count",
                            "verification_abstained_count",
                            "verification_error",
                        )
                        if key in lines_result
                    }
                    if lines_stats:
                        _log(f"Lines pipeline stats: {lines_stats}")

                    # Surface the lines-pipeline subprocess's merchant-resolution
                    # logs (captured in the worker) — these don't reach CloudWatch
                    # on their own.
                    resolver_logs = lines_result.get("resolver_logs")
                    if resolver_logs:
                        for _ln in resolver_logs.splitlines():
                            if _ln.strip():
                                _log(f"[lines-pipeline] {_redact_pii(_ln)}")

                    # Reconstruct MerchantResult from serializable dict
                    if lines_result.get("success"):
                        # Import here to avoid circular import
                        from receipt_upload.merchant_resolution.resolver import (
                            SimilarityMatch,
                        )

                        similarity_matches = None
                        if lines_result.get("similarity_matches"):
                            similarity_matches = [
                                SimilarityMatch(
                                    image_id=m["image_id"],
                                    receipt_id=m["receipt_id"],
                                    merchant_name=m.get("merchant_name"),
                                    normalized_phone=m.get("normalized_phone"),
                                    normalized_address=m.get(
                                        "normalized_address"
                                    ),
                                    embedding_similarity=m[
                                        "embedding_similarity"
                                    ],
                                    metadata_boost=m.get(
                                        "metadata_boost", 0.0
                                    ),
                                    place_id=m.get("place_id"),
                                )
                                for m in lines_result["similarity_matches"]
                            ]

                        merchant_result = MerchantResult(
                            merchant_name=lines_result.get("merchant_name"),
                            place_id=lines_result.get("place_id"),
                            resolution_tier=lines_result.get(
                                "resolution_tier"
                            ),
                            confidence=lines_result.get("confidence"),
                            phone=lines_result.get("phone"),
                            address=lines_result.get("address"),
                            source_image_id=lines_result.get(
                                "source_image_id"
                            ),
                            source_receipt_id=lines_result.get(
                                "source_receipt_id"
                            ),
                            similarity_matches=similarity_matches,
                        )
                except Exception as e:
                    _log(f"WARNING: Lines pipeline failed: {e}")
                    logger.exception("Lines pipeline error")
                    merchant_result = MerchantResult()

                async_llm_payload = None
                try:
                    words_result = words_future.result()
                    async_llm_payload = words_result.get("async_llm_payload")
                    if words_result.get("success"):
                        validation_stats = {
                            k: v
                            for k, v in words_result.items()
                            if k
                            not in (
                                "success",
                                "async_llm_payload",
                            )
                        }
                except Exception as e:
                    _log(f"WARNING: Words pipeline failed: {e}")
                    logger.exception("Words pipeline error")
                    validation_stats = {}

            _log("Phase 2 complete: parallel pipelines finished")

            # =================================================================
            # PHASE 3: Enqueue deferred LLM validation (async mode only)
            # =================================================================
            # Enqueue ONLY now — after the native embeddings are durable — so
            # the consumer cannot write label changes before the corresponding
            # word embeddings exist downstream.
            if async_llm_payload:
                try:
                    # Give deferred grok the same merchant context the sync path
                    # has (it's resolved by now); falls back to None if unset.
                    async_llm_payload["merchant_name"] = (
                        merchant_result.merchant_name
                    )
                    _enqueue_async_llm_validation(
                        payload=async_llm_payload,
                        image_id=image_id,
                        receipt_id=receipt_id,
                        run_id=run_id,
                        staging_bucket=self.llm_staging_bucket,
                    )
                    _log("Phase 3 complete: enqueued deferred LLM validation")
                except Exception as e:
                    # Enqueue failed (systemic SQS/S3/IAM). Don't strand labels:
                    # validate inline from the same payload.
                    logger.warning(
                        "[LLM_ASYNC_FALLBACK] enqueue failed for %s#%s (%s); "
                        "validating inline",
                        image_id,
                        receipt_id,
                        e,
                    )
                    try:
                        # raise_on_failure=False so a grok failure here is
                        # swallowed + transient labels cleaned up (sync-path
                        # semantics) rather than re-raised and stranding labels.
                        apply_async_payload(
                            async_llm_payload,
                            self.dynamo,
                            raise_on_failure=False,
                        )
                    except Exception:
                        logger.exception(
                            "Inline LLM fallback also failed for %s#%s",
                            image_id,
                            receipt_id,
                        )

            # =================================================================
            # PHASE 4: Log merchant resolution + enrich receipt place
            # =================================================================
            similarity_matches_data = None
            if merchant_result.similarity_matches:
                similarity_matches_data = [
                    {
                        "image_id": m.image_id,
                        "receipt_id": m.receipt_id,
                        "merchant_name": m.merchant_name,
                        "embedding_similarity": m.embedding_similarity,
                        "metadata_boost": m.metadata_boost,
                        "total_confidence": m.total_confidence,
                    }
                    for m in merchant_result.similarity_matches[:5]
                ]

            log_merchant_resolution(
                image_id=image_id,
                receipt_id=receipt_id,
                resolution_tier=merchant_result.resolution_tier or "not_found",
                merchant_name=merchant_result.merchant_name,
                place_id=merchant_result.place_id,
                confidence=merchant_result.confidence,
                phone_extracted=merchant_result.phone,
                address_extracted=merchant_result.address,
                similarity_matches=similarity_matches_data,
                source_receipt=(
                    f"{merchant_result.source_image_id}#{merchant_result.source_receipt_id}"
                    if merchant_result.source_image_id
                    else None
                ),
            )

            # Enrich receipt place with the resolved merchant
            if merchant_result.place_id:
                _log(
                    f"Enriching receipt with merchant: {merchant_result.merchant_name} "
                    f"(place_id={merchant_result.place_id}, "
                    f"tier={merchant_result.resolution_tier})"
                )
                self._enrich_receipt_place(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    merchant_result=merchant_result,
                    existing_place=receipt_place,
                )
            else:
                _log("No merchant found - receipt will not be enriched")

            _log("Phase 4 complete: logged merchant + enriched receipt")

        except Exception as e:
            _log(f"WARNING: Processing failed: {e}")
            logger.exception("Processing failed")

        return {
            # A receipt is only fully processed once its native embedding
            # items are durable; without them it is invisible to
            # SearchVectors and nothing downstream will heal it.
            "success": native_write_ok,
            "native_embeddings": native_report,
            "run_id": run_id,
            "lines_count": len(lines),
            "words_count": len(words),
            "merchant_found": merchant_result.place_id is not None,
            "merchant_name": merchant_result.merchant_name,
            "merchant_place_id": merchant_result.place_id,
            "merchant_resolution_tier": merchant_result.resolution_tier,
            "merchant_confidence": merchant_result.confidence,
            **validation_stats,
            **lines_stats,
        }

    def _enrich_receipt_place(
        self,
        image_id: str,
        receipt_id: int,
        merchant_result: MerchantResult,
        existing_place: Optional[Any] = None,
    ) -> None:
        """
        Update receipt place in DynamoDB with merchant information.

        Args:
            image_id: Receipt's image_id
            receipt_id: Receipt's receipt_id
            merchant_result: Resolved merchant information
            existing_place: Optional pre-fetched place to avoid duplicate query
        """
        try:
            # Use provided place or fetch if not available
            place = existing_place
            if place is None:
                try:
                    place = self.dynamo.get_receipt_place(image_id, receipt_id)
                except Exception:
                    place = None

            if place:
                # Update existing place with merchant info
                updates = {}

                if merchant_result.place_id:
                    updates["place_id"] = merchant_result.place_id

                if merchant_result.merchant_name:
                    if not place.merchant_name:
                        updates["merchant_name"] = (
                            merchant_result.merchant_name
                        )

                if merchant_result.address:
                    if not place.formatted_address:
                        updates["formatted_address"] = merchant_result.address

                if merchant_result.phone:
                    if not place.phone_number:
                        updates["phone_number"] = merchant_result.phone

                if updates:
                    self.dynamo.update_receipt_place(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        **updates,
                    )
                    _log(f"Updated receipt place with: {list(updates.keys())}")
            else:
                # Create new receipt place if none exists
                # Only create if we have both place_id AND merchant_name
                # (ReceiptPlace requires merchant_name to be non-empty)
                if merchant_result.place_id and merchant_result.merchant_name:
                    from receipt_dynamo.entities import ReceiptPlace

                    # Persist the resolver's match-quality signals. These
                    # were historically dropped, so every similarity-resolved
                    # place was stored with confidence=0.0 / empty status,
                    # making low-confidence places impossible to filter or
                    # audit later.
                    confidence = merchant_result.confidence or 0.0
                    matched_fields = [
                        name
                        for name, value in (
                            ("merchant_name", merchant_result.merchant_name),
                            ("phone", merchant_result.phone),
                            ("address", merchant_result.address),
                        )
                        if value
                    ]
                    new_place = ReceiptPlace(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        place_id=merchant_result.place_id,
                        merchant_name=merchant_result.merchant_name,
                        formatted_address=merchant_result.address or "",
                        phone_number=merchant_result.phone or "",
                        confidence=confidence,
                        validation_status=(
                            "MATCHED" if confidence >= 0.8 else "UNSURE"
                        ),
                        matched_fields=matched_fields,
                    )
                    self.dynamo.add_receipt_place(new_place)
                    _log(
                        f"Created new receipt place for {image_id}#{receipt_id}"
                    )
                elif merchant_result.place_id:
                    # Have place_id but no merchant_name - log for debugging
                    _log(
                        f"Skipping receipt place creation - have place_id "
                        f"({merchant_result.place_id}) but no merchant_name"
                    )

        except Exception as e:
            _log(f"ERROR: Failed to enrich receipt place: {e}")
            logger.exception("Place enrichment failed")
            # Don't raise - this is best-effort metadata enrichment

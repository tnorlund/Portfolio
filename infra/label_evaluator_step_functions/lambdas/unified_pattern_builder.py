"""Unified pattern builder combining LLM discovery and geometric computation.

This handler consolidates LearnLineItemPatterns and BuildMerchantPatterns into
a single Lambda, reducing cold starts and simplifying the Step Function.

Phase 1 operations (sequential):
1. Discover line item patterns using LLM (from sample receipts)
2. Compute geometric patterns from training receipts

Both pattern types are saved to S3 with timing metadata for per-receipt tracing.
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import boto3

# Import utilities - works in both container and local environments
try:
    # Container environment
    from utils.s3_helpers import (
        download_chromadb_snapshot,
        get_merchant_hash,
        upload_json_to_s3,
    )
    from utils.tracing import (
        MerchantTraceInfo,
        TraceContext,
        child_trace,
        create_merchant_trace,
        end_merchant_trace,
        flush_langsmith_traces,
        state_trace,
    )
except ImportError:
    # Local/development environment
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lambdas",
            "utils",
        ),
    )
    from s3_helpers import (
        download_chromadb_snapshot,
        get_merchant_hash,
        upload_json_to_s3,
    )
    from tracing import (
        MerchantTraceInfo,
        TraceContext,
        child_trace,
        create_merchant_trace,
        end_merchant_trace,
        flush_langsmith_traces,
        state_trace,
    )

if TYPE_CHECKING:
    pass

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

FEATURED_LAYOUTLM_JOB_ID = os.environ.get(
    "FEATURED_LAYOUTLM_JOB_ID",
    os.environ.get(
        "FEATURED_JOB_ID",
        "9775a6f5-80f5-435b-b711-47b69ce174bc",
    ),
)
FALSEY_EVENT_VALUES = {"0", "false", "no", "off"}


def _event_flag_enabled(value: Any, *, default: bool = True) -> bool:
    """Parse Step Function/Lambda flag inputs that may arrive as strings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in FALSEY_EVENT_VALUES
    return bool(value)


def _llm_execution_metadata(config: Any) -> dict[str, Any]:
    """Describe LLM mode without creating a client or making an API call."""
    try:
        from receipt_agent.utils.llm_factory import (
            openrouter_model_catalog,
            paid_llm_calls_disabled,
        )

        catalog = openrouter_model_catalog()
        process_disabled = paid_llm_calls_disabled()
    except Exception:  # pragma: no cover - defensive Lambda metadata path
        catalog = {}
        process_disabled = False

    api_key = str(getattr(config, "openrouter_api_key", "") or "")
    disabled = bool(getattr(config, "disable_paid_llm", False)) or process_disabled
    api_call_allowed = bool(api_key) and not disabled
    profile = (
        os.environ.get("OPENROUTER_MODEL_PROFILE")
        or os.environ.get("RECEIPT_AGENT_OPENROUTER_MODEL_PROFILE")
        or "quality"
    )
    return {
        "mode": "llm_assisted" if api_call_allowed else "deterministic_fallback",
        "paid_llm_disabled": disabled,
        "api_call_allowed": api_call_allowed,
        "api_key_present": bool(api_key),
        "configured_model": getattr(config, "openrouter_model", None),
        "model_profile": profile,
        "latest_openai_model": catalog.get("latest_openai_model"),
        "latest_model_source": catalog.get("latest_model_source"),
        "latest_model_verified_at": catalog.get("latest_model_verified_at"),
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Unified pattern builder for a merchant.

    Combines LearnLineItemPatterns (LLM discovery) and BuildMerchantPatterns
    (geometric computation) into a single Lambda invocation.

    Input:
    {
        "execution_id": "abc123",
        "execution_arn": "arn:aws:states:...",
        "batch_bucket": "bucket-name",
        "merchant_name": "Home Depot",
        "max_training_receipts": 50,
        "langchain_project": "label-evaluator-{timestamp}"
    }

    Output:
    {
        "execution_id": "abc123",
        "merchant_name": "Home Depot",
        "line_item_patterns_s3_key": "line_item_patterns/{exec}/{hash}.json",
        "patterns_s3_key": "patterns/{exec}/{hash}.json",
        "receipt_count": 45,
        "pattern_stats": {...}
    }
    """
    from receipt_dynamo import DynamoClient

    # Allow runtime override of LangSmith project via Step Function input
    langchain_project = event.get("langchain_project")
    if langchain_project:
        os.environ["LANGCHAIN_PROJECT"] = langchain_project
        logger.info("LangSmith project set to: %s", langchain_project)

    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name")
    # Handle merchant_name from merchant object (Map state)
    if not merchant_name and "merchant" in event:
        merchant_name = event["merchant"].get("merchant_name")
    max_training_receipts = event.get("max_training_receipts", 50)
    training_job_id = (
        event.get("training_job_id")
        or event.get("layoutlm_job_id")
        or os.environ.get("LAYOUTLM_TRAINING_JOB_ID")
        or "featured"
    )
    include_confusion_targets = _event_flag_enabled(
        event.get("include_confusion_targets"),
        default=True,
    )
    include_similar_merchant_evidence = _event_flag_enabled(
        event.get("include_similar_merchant_evidence"),
        default=True,
    )
    execution_arn = event.get("execution_arn", "")

    if not merchant_name:
        raise ValueError("merchant_name is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    merchant_hash = get_merchant_hash(merchant_name)
    line_item_patterns_s3_key = (
        f"line_item_patterns/{execution_id}/{merchant_hash}.json"
    )
    patterns_s3_key = f"patterns/{execution_id}/{merchant_hash}.json"

    # Create root merchant trace for Phase 1 (pattern computation)
    merchant_trace = create_merchant_trace(
        execution_arn=execution_arn,
        merchant_name=merchant_name,
        name="UnifiedPatternBuilder",
        inputs={
            "merchant_name": merchant_name,
            "max_training_receipts": max_training_receipts,
        },
        metadata={"execution_id": execution_id, "phase": "pattern_learning"},
        tags=["phase-1", "per-merchant", "unified"],
        enable_tracing=True,
    )

    trace_ctx = TraceContext(
        run_tree=merchant_trace.run_tree,
        headers=(
            merchant_trace.run_tree.to_headers() if merchant_trace.run_tree else None
        ),
        trace_id=merchant_trace.trace_id,
        root_run_id=merchant_trace.root_run_id,
    )

    # Initialize DynamoDB client
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable")
    dynamo_client = DynamoClient(table_name=table_name)

    # Track overall timing
    total_start = time.time()

    # =========================================================================
    # Step 1: Discover Line Item Patterns (LLM)
    # =========================================================================
    discovery_start = time.time()
    discovery_start_time = datetime.now(timezone.utc).isoformat()
    line_item_patterns = None
    discovery_error = None

    try:
        with child_trace(
            "LearnLineItemPatterns",
            trace_ctx,
            run_type="chain",
            inputs={"merchant_name": merchant_name},
            metadata={"step": "discovery"},
        ):
            from receipt_agent.agents.label_evaluator.pattern_discovery import (
                PatternDiscoveryConfig,
                build_confusion_pattern_targets,
                build_discovery_prompt,
                build_receipt_structure,
                build_synthetic_receipt_plan,
                discover_patterns_with_llm,
                generate_synthetic_receipt_candidates,
                get_default_patterns,
            )

            config = PatternDiscoveryConfig.from_env()
            llm_execution = _llm_execution_metadata(config)

            # Load sample receipts for LLM discovery (typically 3)
            load_start = time.time()
            receipts_data = build_receipt_structure(
                dynamo_client, merchant_name, limit=3
            )
            load_duration = time.time() - load_start

            if not receipts_data:
                logger.warning("No receipt data found for %s", merchant_name)
                line_item_patterns = get_default_patterns(
                    merchant_name, reason="no_receipt_data"
                )
            else:
                confusion_target_context = None
                confusion_targets = []
                similar_merchant_examples = []
                similar_merchant_source = {
                    "status": "skipped",
                    "reason": "no_confusion_targets",
                }
                if include_confusion_targets:
                    try:
                        confusion_target_context = _load_confusion_target_context(
                            dynamo_client,
                            training_job_id=training_job_id,
                        )
                        if confusion_target_context:
                            cm_data = confusion_target_context["confusion_matrix"]
                            confusion_targets = build_confusion_pattern_targets(
                                cm_data.get("labels", []),
                                cm_data.get("matrix", []),
                                receipts_data=receipts_data,
                                limit=3,
                            )
                            logger.info(
                                "Built %d confusion pattern targets from "
                                "job=%s epoch=%s",
                                len(confusion_targets),
                                confusion_target_context.get("job_id"),
                                confusion_target_context.get("epoch"),
                            )
                    except Exception:
                        logger.exception(
                            "Failed to build confusion pattern targets; "
                            "continuing without them"
                        )
                        confusion_target_context = None
                        confusion_targets = []

                if confusion_targets:
                    similar_merchant_source = {
                        "status": "skipped",
                        "reason": "disabled",
                    }
                    if include_similar_merchant_evidence:
                        (
                            similar_merchant_examples,
                            similar_merchant_source,
                        ) = _load_similar_merchant_examples(
                            merchant_name,
                            confusion_targets,
                        )

                # Build prompt and call LLM
                prompt_start = time.time()
                prompt = build_discovery_prompt(
                    merchant_name,
                    receipts_data,
                    confusion_targets=confusion_targets,
                    similar_merchant_examples=similar_merchant_examples,
                )
                prompt_duration = time.time() - prompt_start

                llm_start = time.time()
                patterns = discover_patterns_with_llm(
                    prompt, config, trace_ctx=trace_ctx
                )
                llm_duration = time.time() - llm_start

                if not patterns:
                    logger.warning("LLM pattern discovery failed for %s", merchant_name)
                    line_item_patterns = get_default_patterns(
                        merchant_name, reason="llm_discovery_failed"
                    )
                else:
                    line_item_patterns = patterns
                    line_item_patterns["discovered_from_receipts"] = len(receipts_data)
                    line_item_patterns["auto_generated"] = False

                if confusion_targets:
                    synthetic_receipt_plan = build_synthetic_receipt_plan(
                        merchant_name,
                        confusion_targets,
                        receipts_data=receipts_data,
                        llm_patterns=line_item_patterns,
                        similar_merchant_examples=similar_merchant_examples,
                    )
                    synthetic_receipt_candidates = (
                        generate_synthetic_receipt_candidates(
                            synthetic_receipt_plan,
                            receipts_data=receipts_data,
                        )
                    )
                    line_item_patterns["confusion_pattern_targets"] = [
                        target.to_dict() for target in confusion_targets
                    ]
                    line_item_patterns["confusion_target_source"] = {
                        "training_job_id": confusion_target_context.get("job_id"),
                        "epoch": confusion_target_context.get("epoch"),
                        "requested_training_job_id": training_job_id,
                    }
                    line_item_patterns["similar_merchant_evidence_source"] = (
                        similar_merchant_source
                    )
                    line_item_patterns["synthetic_receipt_plan"] = (
                        synthetic_receipt_plan.to_dict()
                    )
                    line_item_patterns["synthetic_receipt_candidates"] = [
                        candidate.to_dict()
                        for candidate in synthetic_receipt_candidates
                    ]
                    try:
                        from receipt_agent.agents.label_evaluator.merchant_synthesis import (
                            build_merchant_synthesis_profile,
                            build_merchant_synthesis_readiness,
                        )
                        from receipt_agent.agents.label_evaluator.sprouts_parameterization import (
                            build_sprouts_receipt_parameters,
                            is_sprouts_merchant,
                        )

                        merchant_parameters = None
                        if is_sprouts_merchant(merchant_name):
                            sprouts_parameters = build_sprouts_receipt_parameters(
                                receipts_data
                            )
                            if sprouts_parameters:
                                merchant_parameters = sprouts_parameters.to_dict()
                        if merchant_parameters is None:
                            merchant_parameters = build_merchant_synthesis_profile(
                                merchant_name,
                                receipts_data,
                            )
                        merchant_readiness = build_merchant_synthesis_readiness(
                            merchant_name,
                            receipts_data,
                            plan=synthetic_receipt_plan.to_dict(),
                        )
                        if merchant_parameters and merchant_readiness:
                            merchant_parameters["synthesis_readiness"] = (
                                merchant_readiness
                            )
                        if merchant_parameters:
                            line_item_patterns["merchant_receipt_parameterization"] = (
                                merchant_parameters
                            )
                    except Exception:
                        logger.exception(
                            "Could not build merchant parameterization artifact"
                        )

    except Exception as e:
        from receipt_agent.utils.llm_factory import LLMRateLimitError

        if isinstance(e, LLMRateLimitError):
            logger.error("Rate limit error in discovery: %s", e)
            raise

        logger.error("Error in pattern discovery: %s", e, exc_info=True)
        discovery_error = str(e)
        # Use default patterns on error
        from receipt_agent.agents.label_evaluator.pattern_discovery import (
            get_default_patterns,
        )

        line_item_patterns = get_default_patterns(
            merchant_name, reason="discovery_error"
        )

    discovery_end_time = datetime.now(timezone.utc).isoformat()
    discovery_duration = time.time() - discovery_start

    # Add timing metadata to line item patterns
    if line_item_patterns:
        line_item_patterns["_trace_metadata"] = {
            "discovery_start_time": discovery_start_time,
            "discovery_end_time": discovery_end_time,
            "discovery_duration_seconds": round(discovery_duration, 3),
            "discovery_status": "error" if discovery_error else "success",
            "llm_execution": locals().get("llm_execution")
            or {
                "mode": "unknown",
                "paid_llm_disabled": None,
                "api_call_allowed": None,
            },
        }
        if line_item_patterns.get("confusion_pattern_targets"):
            line_item_patterns["_trace_metadata"]["confusion_target_count"] = len(
                line_item_patterns["confusion_pattern_targets"]
            )
            synthetic_plan = line_item_patterns.get("synthetic_receipt_plan") or {}
            line_item_patterns["_trace_metadata"]["synthetic_recipe_count"] = len(
                synthetic_plan.get("recipes") or []
            )
            line_item_patterns["_trace_metadata"]["synthetic_candidate_count"] = len(
                line_item_patterns.get("synthetic_receipt_candidates") or []
            )
            source = line_item_patterns.get("confusion_target_source") or {}
            line_item_patterns["_trace_metadata"]["confusion_training_job_id"] = (
                source.get("training_job_id")
            )
            line_item_patterns["_trace_metadata"]["confusion_epoch"] = source.get(
                "epoch"
            )
            similar_source = (
                line_item_patterns.get("similar_merchant_evidence_source") or {}
            )
            line_item_patterns["_trace_metadata"][
                "similar_merchant_evidence_status"
            ] = similar_source.get("status")
            line_item_patterns["_trace_metadata"]["similar_merchant_evidence_count"] = (
                similar_source.get("example_count", 0)
            )
        if discovery_error:
            line_item_patterns["_trace_metadata"]["error"] = discovery_error

    # Save line item patterns to S3
    upload_json_to_s3(s3, batch_bucket, line_item_patterns_s3_key, line_item_patterns)
    logger.info(
        "Saved line item patterns to s3://%s/%s (%.2fs)",
        batch_bucket,
        line_item_patterns_s3_key,
        discovery_duration,
    )

    # =========================================================================
    # Step 2: Compute Geometric Patterns
    # =========================================================================
    computation_start = time.time()
    computation_start_time = datetime.now(timezone.utc).isoformat()
    geometric_patterns = None
    pattern_stats = None
    receipt_count = 0

    try:
        with child_trace(
            "BuildMerchantPatterns",
            trace_ctx,
            run_type="chain",
            inputs={
                "merchant_name": merchant_name,
                "max_receipts": max_training_receipts,
            },
            metadata={"step": "geometric"},
        ):
            from receipt_agent.agents.label_evaluator.state import (
                OtherReceiptData,
            )

            # Load training receipts for geometric patterns
            other_receipt_data: list[OtherReceiptData] = []
            last_key = None
            load_start = time.time()

            while len(other_receipt_data) < max_training_receipts:
                places, last_key = dynamo_client.get_receipt_places_by_merchant(
                    merchant_name,
                    limit=min(
                        100,
                        max_training_receipts - len(other_receipt_data),
                    ),
                    last_evaluated_key=last_key,
                )

                if not places:
                    break

                for place in places:
                    if len(other_receipt_data) >= max_training_receipts:
                        break

                    try:
                        words = dynamo_client.list_receipt_words_from_receipt(
                            place.image_id, place.receipt_id
                        )
                        labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(
                            place.image_id, place.receipt_id
                        )

                        other_receipt_data.append(
                            OtherReceiptData(
                                place=place,
                                words=words,
                                labels=labels,
                            )
                        )
                    except Exception:
                        logger.exception(
                            "Error loading receipt %s#%s",
                            place.image_id,
                            place.receipt_id,
                        )
                        continue

                if not last_key:
                    break

            load_duration = time.time() - load_start
            receipt_count = len(other_receipt_data)
            logger.info(
                "Loaded %s training receipts in %.2fs",
                receipt_count,
                load_duration,
            )

            if other_receipt_data:
                # Compute geometric patterns
                compute_start = time.time()
                from receipt_agent.agents.label_evaluator.patterns import (
                    compute_merchant_patterns,
                )

                geometric_patterns = compute_merchant_patterns(
                    other_receipt_data,
                    merchant_name,
                    max_pair_patterns=4,
                    max_relationship_dimension=3,
                )
                compute_duration = time.time() - compute_start
                logger.info("Pattern computation completed in %.2fs", compute_duration)

                if geometric_patterns:
                    pattern_stats = {
                        "label_positions": len(geometric_patterns.label_positions),
                        "label_pairs": len(geometric_patterns.label_pair_geometry),
                        "observed_pairs": len(geometric_patterns.all_observed_pairs),
                        "receipt_count": geometric_patterns.receipt_count,
                    }

    except Exception as e:
        logger.error("Error in geometric pattern computation: %s", e, exc_info=True)
        # Continue without geometric patterns - not a fatal error

    computation_end_time = datetime.now(timezone.utc).isoformat()
    computation_duration = time.time() - computation_start

    # Serialize and save geometric patterns to S3
    patterns_data = _serialize_patterns(geometric_patterns, merchant_name)
    patterns_data["_trace_metadata"] = {
        "computation_start_time": computation_start_time,
        "computation_end_time": computation_end_time,
        "computation_duration_seconds": round(computation_duration, 3),
        "computation_status": "success" if geometric_patterns else "no_data",
        "training_receipt_count": receipt_count,
    }

    s3.put_object(
        Bucket=batch_bucket,
        Key=patterns_s3_key,
        Body=json.dumps(patterns_data, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(
        "Saved geometric patterns to s3://%s/%s (%.2fs)",
        batch_bucket,
        patterns_s3_key,
        computation_duration,
    )

    # =========================================================================
    # Finalize
    # =========================================================================
    total_duration = time.time() - total_start

    # End the merchant trace
    end_merchant_trace(
        merchant_trace,
        outputs={
            "line_item_patterns_discovered": line_item_patterns is not None,
            "geometric_patterns_computed": geometric_patterns is not None,
            "receipt_count": receipt_count,
            "total_duration_seconds": round(total_duration, 2),
        },
    )
    flush_langsmith_traces()

    return {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "line_item_patterns_s3_key": line_item_patterns_s3_key,
        "patterns_s3_key": patterns_s3_key,
        "receipt_count": receipt_count,
        "pattern_stats": pattern_stats,
        "discovery_duration_seconds": round(discovery_duration, 2),
        "computation_duration_seconds": round(computation_duration, 2),
        "total_duration_seconds": round(total_duration, 2),
    }


def _job_version_number(name: str | None) -> int:
    """Extract the layoutlm-vNN version number from a job name."""
    match = re.search(r"-v(\d+)", name or "")
    return int(match.group(1)) if match else -1


def _resolve_layoutlm_training_job(
    dynamo_client: Any,
    training_job_id: str | None,
) -> str | None:
    """Resolve the LayoutLM training job used for confusion targets."""
    if training_job_id and training_job_id != "featured":
        return training_job_id

    try:
        jobs, _ = dynamo_client.list_jobs(limit=500)
    except Exception:
        logger.exception(
            "list_jobs failed while resolving LayoutLM training job; "
            "falling back to FEATURED_LAYOUTLM_JOB_ID"
        )
        return FEATURED_LAYOUTLM_JOB_ID

    candidates = [
        job for job in jobs if _job_version_number(getattr(job, "name", "")) >= 0
    ]
    if not candidates:
        return FEATURED_LAYOUTLM_JOB_ID

    newest = max(
        candidates,
        key=lambda job: (
            _job_version_number(getattr(job, "name", "")),
            getattr(job, "created_at", "") or "",
        ),
    )
    return getattr(newest, "job_id", None) or FEATURED_LAYOUTLM_JOB_ID


def _list_all_job_metrics(
    dynamo_client: Any,
    job_id: str,
    metric_name: str,
) -> list[Any]:
    """List all metrics for one job/name, handling Dynamo pagination."""
    metrics: list[Any] = []
    last_key = None
    while True:
        page, last_key = dynamo_client.list_job_metrics(
            job_id,
            metric_name=metric_name,
            last_evaluated_key=last_key,
        )
        metrics.extend(page)
        if not last_key:
            break
    return metrics


def _collapse_confusion_matrix_labels(
    cm_data: dict[str, Any],
) -> dict[str, Any]:
    """Collapse BIO confusion labels to base entity labels."""
    labels = cm_data.get("labels", [])
    matrix = cm_data.get("matrix", [])
    if not labels or not matrix:
        return cm_data

    def base_label(label: str) -> str:
        label = str(label)
        if label.startswith("B-") or label.startswith("I-"):
            return label[2:]
        return label

    entity_indices: dict[str, int] = {}
    entity_map: dict[int, str] = {}
    new_labels: list[str] = []

    for idx, label in enumerate(labels):
        entity = base_label(label)
        entity_map[idx] = entity
        if entity not in entity_indices:
            entity_indices[entity] = len(new_labels)
            new_labels.append(entity)

    new_matrix = [[0 for _ in new_labels] for _ in new_labels]
    for row_idx, row in enumerate(matrix):
        if row_idx not in entity_map:
            continue
        for col_idx, value in enumerate(row):
            if col_idx not in entity_map:
                continue
            new_row = entity_indices[entity_map[row_idx]]
            new_col = entity_indices[entity_map[col_idx]]
            new_matrix[new_row][new_col] += value or 0

    return {"labels": new_labels, "matrix": new_matrix}


def _select_confusion_matrix_metric(
    confusion_metrics: list[Any],
    f1_metrics: list[Any],
) -> Any | None:
    """Select the confusion matrix from the best available validation epoch."""
    confusion_by_epoch = {
        metric.epoch: metric
        for metric in confusion_metrics
        if getattr(metric, "epoch", None) is not None
        and isinstance(getattr(metric, "value", None), dict)
    }
    if not confusion_by_epoch:
        return None

    f1_by_epoch = {
        metric.epoch: metric.value
        for metric in f1_metrics
        if getattr(metric, "epoch", None) in confusion_by_epoch
        and isinstance(getattr(metric, "value", None), (int, float))
    }

    if f1_by_epoch:
        selected_epoch = max(
            f1_by_epoch,
            key=lambda epoch: (f1_by_epoch[epoch], epoch),
        )
    else:
        selected_epoch = max(confusion_by_epoch)

    return confusion_by_epoch[selected_epoch]


def _load_confusion_target_context(
    dynamo_client: Any,
    *,
    training_job_id: str | None,
) -> dict[str, Any] | None:
    """Load the best confusion matrix for generating pattern targets."""
    resolved_job_id = _resolve_layoutlm_training_job(
        dynamo_client,
        training_job_id,
    )
    if not resolved_job_id:
        return None

    confusion_metrics = _list_all_job_metrics(
        dynamo_client,
        resolved_job_id,
        "confusion_matrix",
    )
    if not confusion_metrics:
        logger.info(
            "No confusion_matrix metrics found for training job %s",
            resolved_job_id,
        )
        return None

    f1_metrics = _list_all_job_metrics(
        dynamo_client,
        resolved_job_id,
        "val_f1",
    )
    selected = _select_confusion_matrix_metric(confusion_metrics, f1_metrics)
    if selected is None:
        return None

    return {
        "job_id": resolved_job_id,
        "epoch": selected.epoch,
        "confusion_matrix": _collapse_confusion_matrix_labels(selected.value),
    }


def _positive_int_env(name: str, default: int) -> int:
    """Parse a positive integer environment variable with a safe fallback."""
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _load_chroma_client_class() -> Any:
    """Import the Chroma client from either supported package surface."""
    try:
        from receipt_chroma.data.chroma_client import ChromaClient
    except ImportError:
        from receipt_chroma import ChromaClient

    return ChromaClient


def _create_pattern_chroma_client() -> tuple[Any | None, dict[str, Any]]:
    """Create an optional read-only Chroma client for pattern mining."""
    use_chroma_cloud = os.environ.get("CHROMA_CLOUD_ENABLED", "false").lower() == "true"
    cloud_api_key = os.environ.get("CHROMA_CLOUD_API_KEY", "").strip()
    cloud_tenant = (os.environ.get("CHROMA_CLOUD_TENANT") or "").strip()
    cloud_database = (os.environ.get("CHROMA_CLOUD_DATABASE") or "").strip()

    if use_chroma_cloud:
        if not cloud_api_key:
            return None, {
                "status": "unavailable",
                "source": "chroma_cloud",
                "reason": "missing_chroma_cloud_api_key",
            }
        if not cloud_tenant or not cloud_database:
            return None, {
                "status": "unavailable",
                "source": "chroma_cloud",
                "reason": "missing_chroma_cloud_tenant_or_database",
            }
        try:
            ChromaClient = _load_chroma_client_class()
            client = ChromaClient(
                cloud_api_key=cloud_api_key,
                cloud_tenant=cloud_tenant,
                cloud_database=cloud_database,
                mode="read",
            )
            return client, {
                "status": "available",
                "source": "chroma_cloud",
                "tenant": cloud_tenant,
                "database": cloud_database,
                "collection": "words",
            }
        except Exception as exc:
            logger.warning("Could not initialize Chroma Cloud client: %s", exc)
            return None, {
                "status": "unavailable",
                "source": "chroma_cloud",
                "reason": "client_init_failed",
                "message": str(exc),
            }

    chromadb_bucket = os.environ.get("CHROMADB_BUCKET")
    if not chromadb_bucket:
        return None, {
            "status": "skipped",
            "source": "none",
            "reason": "missing_chromadb_bucket",
        }

    chroma_root = os.environ.get(
        "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY",
        "/tmp/chromadb",
    )
    words_path = os.path.join(chroma_root, "words")
    try:
        download_chromadb_snapshot(s3, chromadb_bucket, "words", words_path)
        ChromaClient = _load_chroma_client_class()
        client = ChromaClient(persist_directory=words_path, mode="read")
        return client, {
            "status": "available",
            "source": "s3_snapshot",
            "bucket": chromadb_bucket,
            "collection": "words",
            "cache_path": words_path,
        }
    except Exception as exc:
        logger.warning("Could not initialize Chroma words snapshot: %s", exc)
        return None, {
            "status": "unavailable",
            "source": "s3_snapshot",
            "reason": "snapshot_init_failed",
            "message": str(exc),
        }


def _create_pattern_embed_fn() -> Any:
    """Create the embedding function used for Chroma similarity queries."""
    from receipt_agent.clients.factory import create_embed_fn

    return create_embed_fn()


def _query_similar_merchant_examples(
    chroma_client: Any,
    merchant_name: str,
    embed_fn: Any,
    confusion_targets: list[Any],
    *,
    max_per_target: int,
    n_results: int,
) -> list[Any]:
    """Query cross-merchant evidence for confusion-driven recipes."""
    from receipt_agent.agents.label_evaluator.pattern_discovery import (
        query_similar_merchant_examples_from_chroma,
    )

    return query_similar_merchant_examples_from_chroma(
        chroma_client,
        merchant_name,
        embed_fn,
        confusion_targets,
        max_per_target=max_per_target,
        n_results=n_results,
    )


def _load_similar_merchant_examples(
    merchant_name: str,
    confusion_targets: list[Any],
) -> tuple[list[Any], dict[str, Any]]:
    """Load similar-merchant evidence for synthetic receipt planning."""
    if not confusion_targets:
        return [], {
            "status": "skipped",
            "reason": "no_confusion_targets",
        }

    chroma_client = None
    try:
        chroma_client, source = _create_pattern_chroma_client()
        if chroma_client is None:
            return [], source

        embed_fn = _create_pattern_embed_fn()
        examples = _query_similar_merchant_examples(
            chroma_client,
            merchant_name,
            embed_fn,
            confusion_targets,
            max_per_target=_positive_int_env(
                "SIMILAR_MERCHANT_EXAMPLES_PER_TARGET",
                4,
            ),
            n_results=_positive_int_env(
                "SIMILAR_MERCHANT_CHROMA_N_RESULTS",
                40,
            ),
        )
        status = "success" if examples else "empty"
        return examples, {
            **source,
            "status": status,
            "example_count": len(examples),
            "target_count": len(confusion_targets),
        }
    except Exception as exc:
        logger.warning("Similar-merchant evidence lookup failed: %s", exc)
        return [], {
            "status": "unavailable",
            "source": "chroma",
            "reason": "lookup_failed",
            "message": str(exc),
        }
    finally:
        if chroma_client and hasattr(chroma_client, "close"):
            try:
                chroma_client.close()
            except Exception:
                logger.debug("Failed to close Chroma client", exc_info=True)


def _serialize_patterns(patterns, merchant_name: str) -> dict[str, Any]:
    """Serialize MerchantPatterns to JSON-safe dict."""
    import statistics

    if patterns is None:
        return {"patterns": None, "merchant_name": merchant_name}

    # Compute label position statistics from raw y-values
    label_position_stats = {}
    for label, y_positions in patterns.label_positions.items():
        if y_positions:
            mean_y = statistics.mean(y_positions)
            std_y = statistics.stdev(y_positions) if len(y_positions) > 1 else 0.0
            label_position_stats[label] = {
                "mean_y": mean_y,
                "std_y": std_y,
                "count": len(y_positions),
            }

    # Serialize label pair geometry
    label_pair_geometry_list = []
    for pair_tuple, geom in patterns.label_pair_geometry.items():
        label_pair_geometry_list.append(
            {
                "labels": list(pair_tuple),
                "mean_angle": geom.mean_angle,
                "std_angle": geom.std_angle,
                "mean_distance": geom.mean_distance,
                "std_distance": geom.std_distance,
                "mean_dx": geom.mean_dx,
                "mean_dy": geom.mean_dy,
                "std_dx": geom.std_dx,
                "std_dy": geom.std_dy,
                "count": len(geom.observations) if geom.observations else 0,
            }
        )

    # Serialize constellation geometry
    constellation_geometry_list = []
    for labels_tuple, cg in patterns.constellation_geometry.items():
        relative_positions_dict = {}
        for label, rel_pos in cg.relative_positions.items():
            relative_positions_dict[label] = {
                "mean_dx": rel_pos.mean_dx,
                "mean_dy": rel_pos.mean_dy,
                "std_dx": rel_pos.std_dx,
                "std_dy": rel_pos.std_dy,
            }
        constellation_geometry_list.append(
            {
                "labels": list(labels_tuple),
                "observation_count": cg.observation_count,
                "relative_positions": relative_positions_dict,
            }
        )

    return {
        "merchant_name": merchant_name,
        "patterns": {
            "merchant_name": patterns.merchant_name,
            "receipt_count": patterns.receipt_count,
            "label_positions": label_position_stats,
            "label_pair_geometry": label_pair_geometry_list,
            "all_observed_pairs": [list(pair) for pair in patterns.all_observed_pairs],
            "constellation_geometry": constellation_geometry_list,
            "batch_classification": dict(patterns.batch_classification),
            "labels_with_same_line_multiplicity": list(
                patterns.labels_with_same_line_multiplicity
            ),
            "labels_with_receipt_multiplicity": list(
                patterns.labels_with_receipt_multiplicity
            ),
        },
    }

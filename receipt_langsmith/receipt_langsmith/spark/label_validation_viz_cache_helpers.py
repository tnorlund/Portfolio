"""Helper utilities for label validation visualization cache generation."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from receipt_langsmith.spark.cli import run_spark_job
from receipt_langsmith.spark.s3_io import (
    load_json_from_s3,
    ReceiptsCachePointer,
    write_json_with_default,
    write_receipt_cache_index,
    write_receipt_json,
)
from receipt_langsmith.spark.trace_df import (
    TraceReadOptions,
    normalize_trace_df,
    trace_columns,
)
from receipt_langsmith.spark.utils import parse_json_object

logger = logging.getLogger(__name__)
LABEL_DRIVER_ROW_WARN_THRESHOLD = 50_000
LABEL_DRIVER_ROOT_HARD_LIMIT = 250_000
LABEL_DRIVER_VALIDATION_HARD_LIMIT = 1_000_000
LABEL_BASELINE_CONF_KEYS = (
    "spark.sql.adaptive.enabled",
    "spark.sql.shuffle.partitions",
    "spark.sql.files.openCostInBytes",
    "spark.sql.files.maxPartitionBytes",
    "spark.eventLog.enabled",
    "spark.eventLog.dir",
    "spark.dynamicAllocation.enabled",
    "spark.dynamicAllocation.minExecutors",
    "spark.dynamicAllocation.maxExecutors",
    "spark.executor.instances",
    "spark.executor.cores",
    "spark.executor.memory",
    "spark.driver.cores",
    "spark.driver.memory",
)

# --- S3 Utilities ---


def load_receipts_from_s3(
    s3_client: Any, receipts_json_path: str
) -> dict[tuple[str, int], dict[str, Any]]:
    """Load receipt lookup from S3 JSON file.

    Returns:
        dict mapping (image_id, receipt_id) -> receipt data dict containing:
            - cdn_s3_key, cdn_webp_s3_key, cdn_avif_s3_key
            - cdn_medium_s3_key, cdn_medium_webp_s3_key, cdn_medium_avif_s3_key
            - width, height
            - words (list of word dicts with bounding boxes)
            - labels (dict mapping (line_id, word_id) -> label)
    """
    logger.info("Loading receipts from %s", receipts_json_path)

    raw_lookup = load_json_from_s3(s3_client, receipts_json_path)
    if not isinstance(raw_lookup, dict):
        raise ValueError("Receipts lookup payload must be a JSON object")

    # Convert "{image_id}_{receipt_id}" -> (image_id, receipt_id)
    lookup: dict[tuple[str, int], dict[str, Any]] = {}
    for composite_key, receipt_data in raw_lookup.items():
        parts = composite_key.rsplit("_", 1)
        if len(parts) == 2:
            image_id, receipt_id_str = parts
            try:
                lookup[(image_id, int(receipt_id_str))] = receipt_data
            except ValueError:
                continue

    logger.info("Loaded %d receipts from S3", len(lookup))
    return lookup


# --- Spark Processing ---


def read_traces(spark: SparkSession, parquet_path: str) -> Any:
    """Read traces from a parquet path.

    Returns DataFrame with columns needed for label validation analysis.
    Handles flexible schema - adds missing columns with nulls.
    """
    logger.info("Reading traces from %s", parquet_path)

    # Read all columns first to check what's available
    df = spark.read.parquet(parquet_path)
    available_columns = set(df.columns)

    logger.info("Available columns in parquet: %s", sorted(available_columns))

    options = TraceReadOptions(
        include_inputs=True,
        include_outputs=True,
        include_extra=True,
        include_tokens=False,
    )
    df = normalize_trace_df(df, options)
    df = df.select(*trace_columns(options))

    logger.info("Read %d traces", df.count())
    return df


def extract_receipt_traces(df: Any) -> list[dict[str, Any]]:
    """Extract receipt_processing root traces with their validation data.

    Returns list of dicts with:
        - image_id, receipt_id
        - trace_id (for looking up children)
        - outputs (validation results)
        - duration_ms
    """
    # Get root receipt_processing traces
    roots = df.filter(F.col("name") == "receipt_processing")

    # Extract metadata from extra field
    roots = (
        roots.withColumn(
            "image_id",
            F.get_json_object(F.col("extra"), "$.metadata.image_id"),
        )
        .withColumn(
            "receipt_id",
            F.get_json_object(F.col("extra"), "$.metadata.receipt_id").cast(
                "int"
            ),
        )
        .withColumn(
            "duration_ms",
            (
                F.col("end_time").cast("double")
                - F.col("start_time").cast("double")
            )
            * 1000,
        )
    )

    root_rows = roots.select(
        "trace_id", "image_id", "receipt_id", "outputs", "duration_ms"
    ).toLocalIterator()
    root_data: list[dict[str, Any]] = []
    for row in root_rows:
        root_data.append(row.asDict())
        if len(root_data) > LABEL_DRIVER_ROOT_HARD_LIMIT:
            raise RuntimeError(
                "receipt_processing root trace collection exceeded hard "
                f"limit: {len(root_data)} > {LABEL_DRIVER_ROOT_HARD_LIMIT}. "
                "Narrow export scope."
            )

    if len(root_data) > LABEL_DRIVER_ROW_WARN_THRESHOLD:
        logger.warning(
            "Collected %d root traces on driver; consider narrowing export scope",
            len(root_data),
        )
    logger.info("Found %d receipt_processing root traces", len(root_data))
    return root_data


def extract_validation_traces(
    df: Any, trace_ids: list[str]
) -> dict[str, list[dict]]:
    """Extract label_validation_chroma and label_validation_llm traces.

    Returns dict mapping trace_id -> list of validation dicts.
    """
    # Filter to validation traces in our trace IDs
    validations = df.filter(
        (F.col("trace_id").isin(trace_ids))
        & (
            F.col("name").isin(
                [
                    "label_validation_chroma",
                    "label_validation_llm",
                    "llm_batch_validation",
                ]
            )
        )
    )

    # Extract validation data
    validations = validations.withColumn(
        "duration_ms",
        (F.col("end_time").cast("double") - F.col("start_time").cast("double"))
        * 1000,
    )

    validation_data = validations.select(
        "trace_id", "name", "outputs", "duration_ms"
    ).toLocalIterator()

    # Group by trace_id
    result: dict[str, list[dict]] = {}
    validation_rows = 0
    for row in validation_data:
        validation_rows += 1
        if validation_rows > LABEL_DRIVER_VALIDATION_HARD_LIMIT:
            raise RuntimeError(
                "validation trace collection exceeded hard limit: "
                f"{validation_rows} > {LABEL_DRIVER_VALIDATION_HARD_LIMIT}. "
                "Narrow export scope."
            )
        trace_id = row["trace_id"]
        if trace_id not in result:
            result[trace_id] = []

        outputs = parse_json_object(row["outputs"])
        result[trace_id].append(
            {
                "name": row["name"],
                "outputs": outputs,
                "duration_ms": row["duration_ms"],
            }
        )

    if validation_rows > LABEL_DRIVER_ROW_WARN_THRESHOLD:
        logger.warning(
            "Collected %d validation spans on driver; consider narrowing export scope",
            validation_rows,
        )
    logger.info(
        "Extracted validation traces for %d receipts (%d spans)",
        len(result),
        validation_rows,
    )
    return result


# --- Visualization Building ---

def build_viz_receipt(
    root_trace: dict[str, Any],
    validation_traces: list[dict],
    receipt_lookup: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any] | None:
    """Build a single visualization receipt.

    Combines:
    - Root trace metadata (image_id, receipt_id, duration)
    - Validation traces (chroma/llm decisions per word)
    - Receipt lookup (CDN keys, word bboxes, labels from DynamoDB)
    """
    context = _prepare_receipt_context(
        root_trace,
        validation_traces,
        receipt_lookup,
    )
    if not context:
        return None

    viz_words = _build_viz_words(
        context.words_data,
        context.labels_data,
        context.validation.lookup,
    )
    if not viz_words:
        logger.debug(
            "No validated words for %s_%d",
            context.image_id,
            context.receipt_id,
        )
        return None

    tiers = _build_tiers(
        viz_words,
        context.validation.chroma_ms,
        context.validation.llm_ms,
    )
    step_timings = _build_step_timings(
        context.validation.chroma_ms,
        context.validation.llm_ms,
        root_trace.get("duration_ms", 0),
    )
    assets = _build_assets(context.receipt_data)
    if not assets:
        return None

    return _assemble_receipt_payload(
        context,
        viz_words,
        tiers,
        step_timings,
        assets,
    )


def _get_receipt_identity(
    root_trace: dict[str, Any],
) -> tuple[str | None, int | None]:
    return root_trace.get("image_id"), root_trace.get("receipt_id")


def _get_receipt_data(
    receipt_lookup: dict[tuple[str, int], dict[str, Any]],
    image_id: str,
    receipt_id: int,
) -> dict[str, Any] | None:
    receipt_data = receipt_lookup.get((image_id, receipt_id))
    if not receipt_data:
        logger.debug("No receipt data for %s_%d", image_id, receipt_id)
        return None
    return receipt_data


@dataclass(frozen=True)
class _ValidationContext:
    lookup: dict[tuple[int, int], dict]
    chroma_ms: float
    llm_ms: float


@dataclass(frozen=True)
class _ReceiptContext:
    image_id: str
    receipt_id: int
    receipt_data: dict[str, Any]
    words_data: list[dict]
    labels_data: dict
    root_outputs: dict[str, Any]
    validation: _ValidationContext


def _prepare_receipt_context(
    root_trace: dict[str, Any],
    validation_traces: list[dict],
    receipt_lookup: dict[tuple[str, int], dict[str, Any]],
) -> _ReceiptContext | None:
    image_id, receipt_id = _get_receipt_identity(root_trace)
    if not image_id or receipt_id is None:
        return None

    receipt_data = _get_receipt_data(
        receipt_lookup,
        image_id,
        receipt_id,
    )
    if not receipt_data:
        return None

    words_data = receipt_data.get("words", [])
    if not words_data:
        logger.debug("No words for %s_%d", image_id, receipt_id)
        return None

    labels_data = receipt_data.get("labels", {})
    root_outputs = parse_json_object(root_trace.get("outputs"))
    validation_lookup, chroma_ms, llm_ms = _build_validation_lookup(
        validation_traces
    )
    validation = _ValidationContext(
        lookup=validation_lookup,
        chroma_ms=chroma_ms,
        llm_ms=llm_ms,
    )

    return _ReceiptContext(
        image_id=image_id,
        receipt_id=receipt_id,
        receipt_data=receipt_data,
        words_data=words_data,
        labels_data=labels_data,
        root_outputs=root_outputs,
        validation=validation,
    )

def _extract_validations(
    validation_traces: list[dict],
) -> tuple[list[dict], list[dict], float, float]:
    chroma_validations: list[dict] = []
    llm_validations: list[dict] = []
    chroma_duration_ms = 0.0
    llm_duration_ms = 0.0

    for trace in validation_traces:
        name = trace.get("name", "")
        outputs = trace.get("outputs") or {}
        duration = trace.get("duration_ms") or 0.0

        if name in ("label_validation_chroma", "chroma_label_validation"):
            chroma_duration_ms += duration
            if "validations" in outputs:
                chroma_validations.extend(outputs["validations"])
            elif "line_id" in outputs:
                chroma_validations.append(outputs)

        elif name in ("label_validation_llm", "llm_batch_validation"):
            llm_duration_ms += duration
            if "validations" in outputs:
                llm_validations.extend(outputs["validations"])
            elif "line_id" in outputs:
                llm_validations.append(outputs)

    return (
        chroma_validations,
        llm_validations,
        chroma_duration_ms,
        llm_duration_ms,
    )


def _normalize_decision(decision: str) -> str | None:
    if not decision:
        return None
    normalized = decision.upper()
    if normalized in ("CORRECT", "CORRECTED"):
        return "INVALID"
    if normalized in ("NEEDS REVIEW", "NEEDS_REVIEW"):
        return "NEEDS_REVIEW"
    if normalized in ("VALID", "INVALID"):
        return normalized
    return None


def _build_validation_lookup(
    validation_traces: list[dict],
) -> tuple[dict[tuple[int, int], dict], float, float]:
    extracted = _extract_validations(validation_traces)
    chroma_validations, llm_validations, chroma_ms, llm_ms = extracted
    lookup: dict[tuple[int, int], dict] = {}
    for source, validations in (
        ("chroma", chroma_validations),
        ("llm", llm_validations),
    ):
        for validation in validations:
            key = (
                validation.get("line_id", 0),
                validation.get("word_id", 0),
            )
            decision = _normalize_decision(validation.get("decision", ""))
            lookup[key] = {
                "validation_source": source,
                "decision": decision,
                "confidence": validation.get("confidence"),
            }
    return lookup, chroma_ms, llm_ms


def _build_viz_words(
    words_data: list[dict],
    labels_data: dict,
    validation_lookup: dict[tuple[int, int], dict],
) -> list[dict]:
    viz_words = []
    for word in words_data:
        line_id = word.get("line_id")
        word_id = word.get("word_id")
        key = (line_id, word_id)

        label_entry = labels_data.get(f"{line_id}_{word_id}", "")
        if isinstance(label_entry, dict):
            label = label_entry.get("label", "")
            validation_status = label_entry.get("validation_status", "PENDING")
        else:
            label = label_entry
            validation_status = "PENDING"

        if label:
            validation = validation_lookup.get(key)
            viz_words.append(
                {
                    "text": word.get("text", ""),
                    "line_id": line_id,
                    "word_id": word_id,
                    "bbox": word.get("bbox", {}),
                    "label": label,
                    "validation_status": validation_status,
                    "validation_source": (
                        validation.get("validation_source")
                        if validation
                        else None
                    ),
                    "decision": (
                        validation.get("decision") if validation else None
                    ),
                }
            )
    return viz_words


def _build_tiers(
    viz_words: list[dict],
    chroma_duration_ms: float,
    llm_duration_ms: float,
) -> dict[str, dict | None]:
    chroma_decisions = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
    llm_decisions = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}

    for word in viz_words:
        decision = word.get("decision")
        source = word.get("validation_source")
        if decision is None or source is None:
            continue

        norm_decision = _normalize_decision(decision)
        if not norm_decision:
            continue

        if source == "chroma":
            chroma_decisions[norm_decision] += 1
        else:
            llm_decisions[norm_decision] += 1

    chroma_count = sum(chroma_decisions.values())
    llm_count = sum(llm_decisions.values())

    chroma_tier = {
        "tier": "chroma",
        "words_count": chroma_count,
        "duration_seconds": chroma_duration_ms / 1000,
        "decisions": chroma_decisions,
    }

    llm_tier = None
    if llm_count > 0:
        llm_tier = {
            "tier": "llm",
            "words_count": llm_count,
            "duration_seconds": llm_duration_ms / 1000,
            "decisions": llm_decisions,
        }

    return {"chroma": chroma_tier, "llm": llm_tier}


def _build_step_timings(
    chroma_duration_ms: float,
    llm_duration_ms: float,
    total_duration_ms: float,
) -> dict[str, Any]:
    step_timings: dict[str, Any] = {}
    if chroma_duration_ms > 0:
        step_timings["chroma_validation"] = {
            "duration_ms": chroma_duration_ms,
            "duration_seconds": chroma_duration_ms / 1000,
        }
    if llm_duration_ms > 0:
        step_timings["llm_validation"] = {
            "duration_ms": llm_duration_ms,
            "duration_seconds": llm_duration_ms / 1000,
        }
    if total_duration_ms and total_duration_ms > 0:
        step_timings["total"] = {
            "duration_ms": total_duration_ms,
            "duration_seconds": total_duration_ms / 1000,
        }
    return step_timings


def _build_assets(receipt_data: dict[str, Any]) -> dict[str, Any] | None:
    cdn_s3_key = receipt_data.get("cdn_s3_key", "")
    if not cdn_s3_key:
        return None
    return {
        "cdn_s3_key": cdn_s3_key,
        "cdn_webp_s3_key": receipt_data.get("cdn_webp_s3_key"),
        "cdn_avif_s3_key": receipt_data.get("cdn_avif_s3_key"),
        "cdn_medium_s3_key": receipt_data.get("cdn_medium_s3_key"),
        "cdn_medium_webp_s3_key": receipt_data.get("cdn_medium_webp_s3_key"),
        "cdn_medium_avif_s3_key": receipt_data.get("cdn_medium_avif_s3_key"),
        "width": receipt_data.get("width", 0),
        "height": receipt_data.get("height", 0),
    }


def _assemble_receipt_payload(
    context: _ReceiptContext,
    viz_words: list[dict],
    tiers: dict[str, dict | None],
    step_timings: dict[str, Any],
    assets: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "image_id": context.image_id,
        "receipt_id": context.receipt_id,
        "merchant_name": context.root_outputs.get("merchant_name"),
        "words": viz_words,
        "chroma": tiers["chroma"],
        "llm": tiers["llm"],
        "step_timings": step_timings,
    }
    payload.update(assets)
    return payload


def calculate_aggregate_stats(receipts: list[dict]) -> dict[str, Any]:
    """Calculate aggregate statistics from receipts."""
    if not receipts:
        return {"total_receipts": 0, "avg_chroma_rate": 0.0}

    total_words = 0
    chroma_words = 0
    total_valid = 0
    total_invalid = 0
    total_needs_review = 0

    for r in receipts:
        chroma = r.get("chroma", {})
        llm = r.get("llm")

        chroma_count = chroma.get("words_count", 0)
        llm_count = llm.get("words_count", 0) if llm else 0

        total_words += chroma_count + llm_count
        chroma_words += chroma_count

        # Aggregate decisions
        chroma_decisions = chroma.get("decisions", {})
        total_valid += chroma_decisions.get("VALID", 0)
        total_invalid += chroma_decisions.get("INVALID", 0)
        total_needs_review += chroma_decisions.get("NEEDS_REVIEW", 0)

        if llm:
            llm_decisions = llm.get("decisions", {})
            total_valid += llm_decisions.get("VALID", 0)
            total_invalid += llm_decisions.get("INVALID", 0)
            total_needs_review += llm_decisions.get("NEEDS_REVIEW", 0)

    avg_chroma_rate = (
        (chroma_words / total_words * 100) if total_words > 0 else 0.0
    )

    return {
        "total_receipts": len(receipts),
        "avg_chroma_rate": round(avg_chroma_rate, 1),
        "total_valid": total_valid,
        "total_invalid": total_invalid,
        "total_needs_review": total_needs_review,
    }


# --- Cache Writing ---


def write_cache(  # pylint: disable=too-many-locals
    s3_client: Any,
    bucket: str,
    receipts: list[dict[str, Any]],
    parquet_prefix: str,
    *,
    run_profile: dict[str, Any] | None = None,
) -> None:
    """Write individual receipt files + metadata to S3."""
    timestamp = datetime.now(timezone.utc)
    cache_version = timestamp.strftime("%Y%m%d-%H%M%S")
    receipts_prefix = "receipts/"

    logger.info(
        "Writing %d individual receipt files to s3://%s/%s",
        len(receipts),
        bucket,
        receipts_prefix,
    )

    def upload_receipt(receipt: dict[str, Any]) -> str:
        """Upload a single receipt and return its key."""
        image_id = receipt.get("image_id", "unknown")
        receipt_id = receipt.get("receipt_id", 0)
        key = f"{receipts_prefix}receipt-{image_id}-{receipt_id}.json"
        write_receipt_json(s3_client, bucket, key, receipt)
        return key

    # Parallel upload
    uploaded_keys = []
    failed_count = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_receipt = {
            executor.submit(upload_receipt, r): r for r in receipts
        }
        for future in as_completed(future_to_receipt):
            try:
                key = future.result()
                uploaded_keys.append(key)
                if len(uploaded_keys) % 20 == 0:
                    logger.info(
                        "Uploaded %d/%d receipts",
                        len(uploaded_keys),
                        len(receipts),
                    )
            except (ClientError, BotoCoreError):
                failed_count += 1
                logger.exception("Failed to upload receipt")

    if failed_count > 0:
        logger.warning("Completed with %d failures", failed_count)

    # Write metadata.json
    aggregate_stats = calculate_aggregate_stats(receipts)
    metadata = {
        "version": cache_version,
        "parquet_prefix": parquet_prefix,
        "receipt_keys": uploaded_keys,
        "aggregate_stats": aggregate_stats,
        "cached_at": timestamp.isoformat(),
    }
    if run_profile:
        metadata["run_profile"] = run_profile
    logger.info("Writing metadata.json")
    pointer = ReceiptsCachePointer(
        cache_version, receipts_prefix, timestamp.isoformat()
    )
    write_receipt_cache_index(s3_client, bucket, metadata, pointer)
    if run_profile:
        run_summary_key = (
            f"profiling/{cache_version}/"
            "label-validation-viz-cache-baseline.json"
        )
        write_json_with_default(
            s3_client,
            bucket,
            run_summary_key,
            run_profile,
        )
        logger.info("Wrote %s to s3://%s/", run_summary_key, bucket)

    logger.info("Cache generation complete!")
    logger.info("  Version: %s", cache_version)
    logger.info("  Total receipts: %d", len(receipts))
    logger.info(
        "  Avg ChromaDB rate: %.1f%%", aggregate_stats["avg_chroma_rate"]
    )


# --- Job Orchestration ---


def _resolve_parquet_prefix(
    parquet_prefix: str,
) -> str | None:
    normalized = parquet_prefix.strip().strip("/") + "/"
    if "export_id=" not in normalized:
        logger.error(
            "Expected --parquet-prefix to include export_id=..., got: %s",
            parquet_prefix,
        )
        return None
    return normalized



def _build_viz_receipts(
    df: Any,
    receipt_lookup: dict[tuple[str, int], dict[str, Any]],
    max_receipts: int,
    *,
    stats_out: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    build_stats: dict[str, Any] = {}

    def commit_stats() -> None:
        if stats_out is not None:
            stats_out.update(build_stats)

    root_traces = extract_receipt_traces(df)
    build_stats["root_traces_collected"] = len(root_traces)
    if not root_traces:
        logger.error("No receipt_processing traces found")
        commit_stats()
        return []

    trace_ids = [t["trace_id"] for t in root_traces if t.get("trace_id")]
    build_stats["trace_ids"] = len(trace_ids)
    validation_traces = extract_validation_traces(df, trace_ids)
    build_stats["validation_trace_groups"] = len(validation_traces)

    viz_receipts: list[dict[str, Any]] = []
    for root in root_traces:
        trace_id = root.get("trace_id")
        validations = (
            validation_traces.get(trace_id, [])
            if isinstance(trace_id, str)
            else []
        )

        receipt = build_viz_receipt(root, validations, receipt_lookup)
        if receipt:
            viz_receipts.append(receipt)

        if len(viz_receipts) >= max_receipts:
            break

    build_stats["viz_receipts"] = len(viz_receipts)
    commit_stats()
    return viz_receipts


def _collect_spark_conf_profile(spark: SparkSession) -> dict[str, Any]:
    spark_conf = spark.sparkContext.getConf()
    conf_snapshot: dict[str, Any] = {}
    for key in LABEL_BASELINE_CONF_KEYS:
        value = spark_conf.get(key, None)
        if value is not None:
            conf_snapshot[key] = value
    return conf_snapshot


def run_label_validation_cache(args: Any) -> int:
    """Run the label validation viz cache job and return exit status."""
    logger.info("Starting Label Validation visualization cache generation")
    logger.info("Parquet bucket: s3://%s", args.parquet_bucket)
    logger.info("Cache bucket: s3://%s", args.cache_bucket)
    logger.info("Receipts JSON: %s", args.receipts_json)
    logger.info("Max receipts: %d", args.max_receipts)

    s3_client = boto3.client("s3")

    parquet_prefix = _resolve_parquet_prefix(args.parquet_prefix)
    if not parquet_prefix:
        return 1

    logger.info("Using parquet prefix: %s", parquet_prefix)
    parquet_path = f"s3://{args.parquet_bucket}/{parquet_prefix}"
    try:
        s3_client.head_object(
            Bucket=args.parquet_bucket,
            Key=f"{parquet_prefix}_SUCCESS",
        )
    except (ClientError, BotoCoreError):
        logger.warning(
            "Could not verify _SUCCESS marker under %s; Spark will attempt direct read",
            parquet_path,
        )
    else:
        logger.info("Verified export marker at %s_SUCCESS", parquet_path)

    receipt_lookup = load_receipts_from_s3(s3_client, args.receipts_json)

    logger.info("Initializing Spark...")
    spark = SparkSession.builder.appName(
        "LabelValidationVizCache"
    ).getOrCreate()

    def job() -> int:
        df = read_traces(spark, parquet_path)
        build_stats: dict[str, Any] = {}
        viz_receipts = _build_viz_receipts(
            df,
            receipt_lookup,
            args.max_receipts,
            stats_out=build_stats,
        )
        if not viz_receipts:
            logger.error("No visualization receipts could be built")
            return 1

        logger.info("Built %d visualization receipts", len(viz_receipts))
        run_profile = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "parquet_path": parquet_path,
            "parquet_prefix": parquet_prefix,
            "max_receipts": args.max_receipts,
            "receipt_lookup_count": len(receipt_lookup),
            "viz_receipts_count": len(viz_receipts),
            "spark_application_id": spark.sparkContext.applicationId,
            "spark_default_parallelism": (
                spark.sparkContext.defaultParallelism
            ),
            "spark_conf": _collect_spark_conf_profile(spark),
            "build_stats": build_stats,
        }

        write_cache(
            s3_client,
            args.cache_bucket,
            viz_receipts,
            parquet_prefix,
            run_profile=run_profile,
        )
        return 0

    return run_spark_job(
        spark,
        job,
        logger=logger,
        error_message="Cache generation failed",
    )

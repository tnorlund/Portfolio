"""Helper utilities for QA viz cache generation."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from botocore.exceptions import BotoCoreError, ClientError
from py4j.protocol import Py4JJavaError
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import LongType, StringType
from pyspark.sql.utils import AnalysisException

from receipt_langsmith.spark.s3_io import (
    load_json_from_s3,
    write_json_with_default,
    write_latest_json,
    write_metadata_json,
)
from receipt_langsmith.spark.utils import (
    parse_json_object,
    parse_s3_path,
    TRACE_BASE_COLUMNS,
    to_s3a,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QACacheWriteContext:
    """Shared context for writing QA cache outputs."""

    s3_client: Any
    cache_bucket: str
    execution_id: str
    langchain_project: str = ""


@dataclass(frozen=True)
class QACacheTraceContext:
    """Inputs required to build cache files from traces."""

    traces: dict[str, list[dict]]
    root_runs: dict[str, dict]
    trace_to_question: dict[str, int]
    question_results: list[dict]
    receipts_lookup: dict[str, Any]
    max_questions: int


@dataclass(frozen=True)
class QACacheJobConfig:
    """Configuration for QA cache generation."""

    parquet_input: str
    cache_bucket: str
    results_ndjson: str
    receipts_json: str
    execution_id: str = ""
    max_questions: int = 50
    langchain_project: str = ""

    @classmethod
    def from_legacy(
        cls,
        *legacy_args: Any,
        **legacy_kwargs: Any,
    ) -> "QACacheJobConfig":
        """Build config from legacy positional/keyword arguments."""
        fields = [
            "parquet_input",
            "cache_bucket",
            "results_ndjson",
            "receipts_json",
            "execution_id",
            "max_questions",
            "langchain_project",
        ]
        if len(legacy_args) > len(fields):
            raise TypeError(
                "run_qa_cache_job accepts at most 7 positional args: "
                "parquet_input, cache_bucket, results_ndjson, receipts_json, "
                "execution_id, max_questions, langchain_project."
            )
        data = dict(zip(fields, legacy_args))
        unknown = set(legacy_kwargs) - set(fields)
        if unknown:
            raise TypeError(
                f"run_qa_cache_job got unexpected keyword(s): {unknown}"
            )
        data.update(legacy_kwargs)
        return cls(**data)


def qa_cache_config_from_args(
    args: Any,
    *,
    default_max_questions: int = 50,
) -> QACacheJobConfig:
    """Build QACacheJobConfig from argparse args."""
    return QACacheJobConfig(
        parquet_input=args.parquet_input,
        cache_bucket=args.cache_bucket,
        results_ndjson=args.results_ndjson,
        receipts_json=args.receipts_json,
        execution_id=getattr(args, "execution_id", "") or "",
        max_questions=getattr(args, "max_questions", default_max_questions),
        langchain_project=getattr(args, "langchain_project", ""),
    )


def load_receipts_lookup(s3_client: Any, receipts_json: str) -> dict[str, Any]:
    """Load receipts-lookup.json from S3.

    Returns:
        dict mapping "{image_id}_{receipt_id}" to receipt metadata.
    """
    try:
        receipts = load_json_from_s3(s3_client, receipts_json)
        if not isinstance(receipts, dict):
            raise ValueError("receipts lookup payload must be a JSON object")
    except (ClientError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        logger.exception(
            "Failed to load receipts lookup from %s", receipts_json
        )
        raise

    logger.info(
        "Loaded %d receipt lookups from %s", len(receipts), receipts_json
    )
    return receipts


def load_question_results(s3_client: Any, results_ndjson: str) -> list[dict]:
    """Load question-results.ndjson from S3.

    Returns:
        List of per-question result dicts ordered by question index.
    """
    try:
        bucket, key = parse_s3_path(results_ndjson)
        response = s3_client.get_object(Bucket=bucket, Key=key)
        payload = response["Body"].read().decode("utf-8")
    except (ClientError, ValueError, UnicodeDecodeError):
        logger.exception(
            "Failed to load question results from %s", results_ndjson
        )
        raise

    results: list[dict] = []
    malformed = 0
    for line_no, line in enumerate(payload.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            results.append(json.loads(stripped))
        except json.JSONDecodeError:
            malformed += 1
            logger.warning(
                "Skipping malformed NDJSON line %d in %s",
                line_no,
                results_ndjson,
            )

    results.sort(key=lambda r: r.get("questionIndex", 0))

    if malformed:
        logger.warning(
            "Loaded %d question results from %s with %d malformed lines "
            "skipped",
            len(results),
            results_ndjson,
            malformed,
        )
    else:
        logger.info(
            "Loaded %d question results from %s", len(results), results_ndjson
        )

    return results


def find_latest_export_prefix(
    s3_client: Any, bucket: str, prefix: str = "traces/"
) -> Optional[str]:
    """Find the latest LangSmith export prefix in the bucket."""
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        latest_key = None
        latest_time = None

        for page in paginator.paginate(
            Bucket=bucket, Prefix=prefix, Delimiter="/"
        ):
            for common_prefix in page.get("CommonPrefixes", []):
                sub_prefix = common_prefix["Prefix"]
                sub_page = s3_client.list_objects_v2(
                    Bucket=bucket, Prefix=sub_prefix, MaxKeys=1
                )
                if sub_page.get("Contents"):
                    obj_time = sub_page["Contents"][0].get("LastModified")
                    if latest_time is None or (
                        obj_time and obj_time > latest_time
                    ):
                        latest_time = obj_time
                        latest_key = sub_prefix

        return latest_key
    except (ClientError, BotoCoreError):
        logger.exception("Failed to find latest export prefix")
        return None


# pylint: disable=too-many-locals
def derive_trace_steps(
    child_runs: list[dict], question_result: dict, receipts_lookup: dict
) -> list[dict]:
    """Derive trace steps from child runs for the React component.

    Maps each child run to a TraceStep with type, content, detail, and
    durationMs based on the node name, outputs, and timestamps.
    """
    steps: list[dict] = []

    for run in child_runs:
        name = run.get("name", "").lower()
        run_type = run.get("run_type", "")
        outputs = parse_json_object(run.get("outputs"))

        step_type = _classify_run(name, run_type)
        if not step_type:
            continue

        content = ""
        detail = ""
        duration_ms = _compute_duration_ms(run)

        if step_type == "plan":
            content = outputs.get("query", "") or outputs.get("question", "")
            detail = "Question classification"

        elif step_type == "agent":
            content = _extract_agent_content(outputs)
            detail = "Agent reasoning"

        elif step_type == "tool":
            tool_name, tool_detail = _extract_tool_info(outputs)
            content = tool_name
            detail = tool_detail

        elif step_type == "shape":
            receipts_count = question_result.get("receiptCount", 0)
            content = f"{receipts_count} receipts shaped"
            detail = "Receipt shaping"

        elif step_type == "synthesize":
            answer = question_result.get("answer", "")
            receipts_count = question_result.get("receiptCount", 0)
            content = answer if answer else "Answer generated"
            detail = f"{receipts_count} receipts identified"
            evidence = question_result.get("evidence", [])
            receipts = _enrich_evidence(evidence, receipts_lookup)
            steps.append(
                {
                    "type": "synthesize",
                    "content": content,
                    "detail": detail,
                    "durationMs": duration_ms,
                    "receipts": receipts,
                }
            )
            continue

        steps.append(
            {
                "type": step_type,
                "content": content,
                "detail": detail,
                "durationMs": duration_ms,
            }
        )

    return steps


# pylint: enable=too-many-locals


def _classify_run(name: str, run_type: str) -> Optional[str]:
    if "plan" in name:
        return "plan"
    if "agent" in name:
        return "agent"
    if run_type == "tool":
        return "tool"
    if "shape" in name:
        return "shape"
    if "synthesize" in name or "final" in name:
        return "synthesize"
    return None


def _compute_duration_ms(run: dict) -> Optional[int]:
    """Compute step duration in milliseconds from start_time and end_time."""
    start = run.get("start_time")
    end = run.get("end_time")
    if start is None or end is None:
        return None

    try:
        if isinstance(start, str) and isinstance(end, str):
            fmt = "%Y-%m-%d %H:%M:%S"
            s = datetime.strptime(start[:19], fmt)
            e = datetime.strptime(end[:19], fmt)
            return max(int((e - s).total_seconds() * 1000), 0)

        if hasattr(start, "timestamp") and hasattr(end, "timestamp"):
            return max(int((end.timestamp() - start.timestamp()) * 1000), 0)

        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            return max(int((end - start) * 1000), 0)
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None

    return None


def _extract_agent_content(outputs: dict) -> str:
    messages = outputs.get("messages", [])
    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                return content
    fallback = outputs.get("content")
    if fallback is None:
        fallback = outputs.get("output", "")
    return fallback if isinstance(fallback, str) else str(fallback)


def _count_tool_calls(outputs: dict) -> int:
    messages = outputs.get("messages", [])
    tool_calls = 0
    for msg in messages:
        if isinstance(msg, dict):
            tool_calls += len(msg.get("tool_calls", []) or [])
    return tool_calls


def _extract_tool_info(outputs: dict) -> tuple[str, str]:
    tool_calls = outputs.get("tool_calls", []) or []
    if tool_calls:
        tool = tool_calls[0]
        tool_name = tool.get("name", "Tool")
        args = tool.get("args", {})
        detail = json.dumps(args, default=str)
        return tool_name, detail

    tool_outputs = outputs.get("output", "") or ""
    if tool_outputs:
        return "Tool", str(tool_outputs)
    return "Tool", ""


def _enrich_evidence(
    evidence: list[dict], receipts_lookup: dict[str, Any]
) -> list[dict[str, Any]]:
    """Enrich evidence with receipt metadata for the frontend.

    Output matches the ReceiptEvidence TypeScript interface:
    - imageId: string
    - merchant: string
    - item: string
    - amount: number
    - thumbnailKey: string (S3 key for CDN)
    - width: number
    - height: number

    Handles both snake_case (from NDJSON) and camelCase (legacy) input keys.
    """
    enriched = []
    for e in evidence:
        # Handle both snake_case and camelCase keys
        receipt_id = e.get("receipt_id") or e.get("receiptId")
        image_id = e.get("image_id") or e.get("imageId")
        if not image_id:
            continue
        key = f"{image_id}_{receipt_id}"
        receipt = receipts_lookup.get(key, {})
        # Skip if no receipt metadata found
        if not receipt:
            continue
        enriched.append(
            {
                "imageId": image_id,
                "merchant": e.get("merchant", ""),
                "item": e.get("item", ""),
                "amount": e.get("amount", 0),
                "thumbnailKey": receipt.get("cdn_webp_s3_key", "")
                or receipt.get("cdn_s3_key", ""),
                "width": receipt.get("width", 0),
                "height": receipt.get("height", 0),
            }
        )
    return enriched


def compute_stats(all_runs: list[dict], question_result: dict) -> dict:
    """Compute summary stats for a question trace."""
    llm_calls = 0
    tool_invocations = 0
    total_tokens = 0

    for run in all_runs:
        run_type = run.get("run_type", "")
        if run_type == "llm":
            llm_calls += 1
            total_tokens += run.get("total_tokens") or 0
        elif run_type == "tool":
            tool_invocations += 1

    cost = question_result.get("cost", 0) or 0
    receipts_processed = question_result.get("receiptCount", 0)

    return {
        "llmCalls": llm_calls,
        "toolInvocations": tool_invocations,
        "receiptsProcessed": receipts_processed,
        "cost": round(cost, 6),
    }


def build_question_cache(
    trace_id: str,
    root_run_id: str,
    all_runs: list[dict],
    question_result: dict,
    receipts_lookup: dict,
) -> dict:
    """Build a per-question cache JSON."""
    depth1_runs = sorted(
        [r for r in all_runs if r.get("parent_run_id") == root_run_id],
        key=lambda r: r.get("dotted_order", ""),
    )

    return {
        "question": question_result.get("question", ""),
        "questionIndex": question_result.get("questionIndex", 0),
        "traceId": trace_id,
        "trace": derive_trace_steps(
            depth1_runs, question_result, receipts_lookup
        ),
        "stats": compute_stats(all_runs, question_result),
    }


def build_question_text_index(
    question_results: list[dict],
) -> dict[str, int]:
    """Map question text to questionIndex for trace matching."""
    index: dict[str, int] = {}
    for result in question_results:
        question_text = (result.get("question", "") or "").strip()
        if question_text:
            index[question_text] = result.get("questionIndex", 0)
    return index


def read_parquet_traces(
    spark: SparkSession,
    parquet_input: str,
) -> Optional[DataFrame]:
    """Read Parquet traces from S3, returning None on failure."""
    spark_path = to_s3a(parquet_input)
    try:
        return (
            spark.read.option("recursiveFileLookup", "true")
            .option("mergeSchema", "true")
            .parquet(spark_path)
        )
    except (
        AnalysisException,
        Py4JJavaError,
        OSError,
        ClientError,
        BotoCoreError,
    ):
        logger.exception(
            "Failed to read parquet from %s — falling back to NDJSON",
            spark_path,
        )
        return None


def normalize_trace_df(df: DataFrame) -> DataFrame:
    """Normalize trace timestamps and add missing columns."""
    for col_name in ["start_time", "end_time", "first_token_time"]:
        if col_name in df.columns:
            col_type = df.schema[col_name].dataType
            if isinstance(col_type, LongType):
                df = df.withColumn(
                    col_name,
                    F.from_unixtime(F.col(col_name) / 1_000_000_000),
                )

    for col_name in [
        "trace_id",
        "parent_run_id",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
    ]:
        if col_name not in df.columns:
            col_type = LongType() if "tokens" in col_name else StringType()
            df = df.withColumn(col_name, F.lit(None).cast(col_type))

    return df


def collect_traces(
    df: DataFrame,
) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """Collect traces and root runs into dictionaries."""
    runs = df.select(
        *TRACE_BASE_COLUMNS,
        "dotted_order",
        "is_root",
        "inputs",
        "outputs",
        "total_tokens",
        "start_time",
        "end_time",
    ).collect()

    traces: dict[str, list[dict]] = {}
    root_runs: dict[str, dict] = {}
    for row in runs:
        row_dict = row.asDict()
        tid = row_dict.get("trace_id", "")
        if not tid:
            continue
        traces.setdefault(tid, []).append(row_dict)
        if row_dict.get("is_root"):
            root_runs[tid] = row_dict
    return traces, root_runs


def _parse_inputs_value(raw_inputs: Any) -> dict[str, Any]:
    return parse_json_object(raw_inputs)


def _extract_question_text(inputs: dict[str, Any]) -> str:
    question_text = ""
    inner_input = inputs.get("input", {})
    if isinstance(inner_input, dict):
        question_text = inner_input.get("question", "")
    if not question_text:
        question_text = inputs.get("question", "")
    return question_text.strip()


def map_traces_to_questions(
    root_runs: dict[str, dict],
    question_text_to_index: dict[str, int],
) -> dict[str, int]:
    """Match root runs to question indices based on input text."""
    trace_to_question: dict[str, int] = {}
    for tid, root in root_runs.items():
        inputs = _parse_inputs_value(root.get("inputs", {}))
        question_text = _extract_question_text(inputs)
        if not question_text:
            continue
        q_idx = question_text_to_index.get(question_text)
        if q_idx is not None:
            trace_to_question[tid] = q_idx
    return trace_to_question


def _selected_results(
    question_results: list[dict],
    max_questions: int,
) -> list[dict]:
    if max_questions and max_questions > 0:
        return question_results[:max_questions]
    return question_results


def _append_missing_cache_files(
    cache_files: list[dict],
    question_results: list[dict],
    receipts_lookup: dict[str, Any],
    max_questions: int,
) -> None:
    traced_indices = {c["questionIndex"] for c in cache_files}
    for result in _selected_results(question_results, max_questions):
        q_idx = result.get("questionIndex", -1)
        if q_idx not in traced_indices:
            cache_files.append(
                {
                    "question": result.get("question", ""),
                    "questionIndex": q_idx,
                    "traceId": "",
                    "trace": _minimal_trace_from_result(
                        result, receipts_lookup
                    ),
                    "stats": {
                        "llmCalls": result.get("llmCalls", 0),
                        "toolInvocations": result.get("toolInvocations", 0),
                        "receiptsProcessed": result.get("receiptCount", 0),
                        "cost": result.get("cost", 0),
                    },
                }
            )


def build_cache_files_from_traces(
    trace_ctx: QACacheTraceContext,
) -> list[dict]:
    """Build cache files for traces plus NDJSON fallbacks."""
    result_by_index = {
        r.get("questionIndex", -1): r for r in trace_ctx.question_results
    }
    cache_files: list[dict] = []
    for tid, runs_list in trace_ctx.traces.items():
        q_idx = trace_ctx.trace_to_question.get(tid)
        if q_idx is None:
            continue
        question_result = result_by_index.get(q_idx)
        if not question_result:
            continue

        root = trace_ctx.root_runs.get(tid, {})
        root_run_id = root.get("id", "")
        non_root_runs = [r for r in runs_list if not r.get("is_root")]

        cache_files.append(
            build_question_cache(
                tid,
                root_run_id,
                non_root_runs,
                question_result,
                trace_ctx.receipts_lookup,
            )
        )

    _append_missing_cache_files(
        cache_files,
        trace_ctx.question_results,
        trace_ctx.receipts_lookup,
        trace_ctx.max_questions,
    )
    cache_files.sort(key=lambda c: c["questionIndex"])
    return cache_files


def build_cache_files_from_parquet(
    spark: SparkSession,
    parquet_input: str,
    question_results: list[dict],
    receipts_lookup: dict[str, Any],
    max_questions: int,
) -> Optional[list[dict]]:
    """Build cache files from parquet traces, or return None for fallback."""
    question_text_to_index = build_question_text_index(question_results)
    df = read_parquet_traces(spark, parquet_input)
    if df is None:
        return None

    df = normalize_trace_df(df)
    total_rows = df.count()
    logger.info("Total parquet rows: %d", total_rows)
    if total_rows == 0:
        logger.warning("No traces found in parquet")
        return None

    traces, root_runs = collect_traces(df)
    logger.info(
        "Found %d unique traces, %d root runs",
        len(traces),
        len(root_runs),
    )

    trace_to_question = map_traces_to_questions(
        root_runs,
        question_text_to_index,
    )
    logger.info("Matched %d traces to questions", len(trace_to_question))

    trace_ctx = QACacheTraceContext(
        traces=traces,
        root_runs=root_runs,
        trace_to_question=trace_to_question,
        question_results=question_results,
        receipts_lookup=receipts_lookup,
        max_questions=max_questions,
    )
    return build_cache_files_from_traces(trace_ctx)


def _minimal_trace_from_result(
    result: dict[str, Any], receipts_lookup: dict[str, Any]
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [
        {"type": "plan", "content": "Question classified", "detail": ""},
        {"type": "agent", "content": "Retrieved receipt data", "detail": ""},
        {
            "type": "shape",
            "content": f"{result.get('receiptCount', 0)} receipts shaped",
            "detail": "",
        },
    ]

    answer = result.get("answer", "")
    evidence = result.get("evidence", [])
    steps.append(
        {
            "type": "synthesize",
            "content": answer if answer else "Answer generated",
            "detail": f"{result.get('receiptCount', 0)} receipts identified",
            "receipts": _enrich_evidence(evidence, receipts_lookup),
        }
    )

    return steps


def write_cache_from_ndjson(
    write_ctx: QACacheWriteContext,
    question_results: list[dict],
    receipts_lookup: dict,
    max_questions: int = 0,
) -> None:
    """Write cache files from NDJSON only (fallback when no parquet traces)."""
    cache_files = []
    selected_results = _selected_results(question_results, max_questions)
    for result in selected_results:
        cache_files.append(
            {
                "question": result.get("question", ""),
                "questionIndex": result.get("questionIndex", 0),
                "traceId": "",
                "trace": _minimal_trace_from_result(result, receipts_lookup),
                "stats": {
                    "llmCalls": result.get("llmCalls", 0),
                    "toolInvocations": result.get("toolInvocations", 0),
                    "receiptsProcessed": result.get("receiptCount", 0),
                    "cost": result.get("cost", 0),
                },
            }
        )

    write_cache_files(write_ctx, cache_files, selected_results)


def write_cache_files(
    write_ctx: QACacheWriteContext,
    cache_files: list[dict],
    question_results: list[dict],
) -> None:
    """Write per-question JSON files and metadata to S3."""
    s3_client = write_ctx.s3_client
    cache_bucket = write_ctx.cache_bucket

    written = _write_question_files(
        s3_client,
        cache_bucket,
        cache_files,
    )

    logger.info(
        "Wrote %d question cache files to s3://%s/questions/",
        written,
        cache_bucket,
    )

    metadata = _build_cache_metadata(
        cache_files,
        question_results,
        write_ctx.execution_id,
        write_ctx.langchain_project,
    )

    write_metadata_json(
        s3_client,
        cache_bucket,
        metadata,
    )

    write_latest_json(
        s3_client,
        cache_bucket,
        {
            "execution_id": write_ctx.execution_id,
            "generated_at": metadata["generated_at"],
        },
        indent=None,
    )

    logger.info(
        "Wrote metadata.json and latest.json to s3://%s/", cache_bucket
    )


def _write_question_files(
    s3_client: Any,
    cache_bucket: str,
    cache_files: list[dict],
) -> int:
    def upload_question(cache: dict) -> str:
        key = f"questions/question-{cache['questionIndex']}.json"
        write_json_with_default(
            s3_client,
            cache_bucket,
            key,
            cache,
        )
        return key

    written = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(upload_question, c): c["questionIndex"]
            for c in cache_files
        }
        for future in as_completed(futures):
            try:
                future.result()
                written += 1
            except (ClientError, BotoCoreError):
                idx = futures[future]
                logger.exception("Failed to write question-%d.json", idx)
    return written


def _build_cache_metadata(
    cache_files: list[dict],
    question_results: list[dict],
    execution_id: str,
    langchain_project: str,
) -> dict[str, Any]:
    cached_indices = {c["questionIndex"] for c in cache_files}
    cached_results = [
        r for r in question_results if r.get("questionIndex") in cached_indices
    ]
    total_cost = sum(r.get("cost", 0) for r in cached_results)
    total_questions = len(cache_files)
    source_questions = len(question_results)
    success_count = sum(1 for r in cached_results if r.get("success"))
    return {
        "total_questions": total_questions,
        "cached_questions": total_questions,
        "source_questions": source_questions,
        "success_count": success_count,
        "total_cost": round(total_cost, 6),
        "avg_cost_per_question": (
            round(total_cost / total_questions, 6)
            if total_questions > 0
            else 0
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution_id": execution_id,
        "langsmith_project": langchain_project or "qa-agent-marquee",
    }

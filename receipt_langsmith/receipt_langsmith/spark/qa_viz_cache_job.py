#!/usr/bin/env python3
"""EMR Serverless job for QA Agent visualization cache generation.

Builds per-question cache JSONs from LangSmith trace data and
question results NDJSON. Output is consumed by the React QAAgentFlow
component via the /qa/visualization API.

Inputs:
    --parquet-input     S3 path to LangSmith Parquet exports
    --cache-bucket      S3 bucket for viz cache output
    --results-ndjson    S3 path to question-results.ndjson from Step Function
    --receipts-json     S3 path to receipts-lookup.json

Output:
    questions/question-{index}.json  (one per question)
    metadata.json                     (aggregate stats)
    latest.json                       (version pointer)

Usage:
    spark-submit \\
        --conf spark.executor.memory=4g \\
        qa_viz_cache_job.py \\
        --parquet-input s3://bucket/traces/ \\
        --cache-bucket viz-cache-bucket \\
        --results-ndjson s3://bucket/qa-runs/abc/question-results.ndjson \\
        --receipts-json s3://bucket/qa-runs/abc/receipts-lookup.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import LongType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="QA Agent visualization cache generator for EMR Serverless"
    )
    parser.add_argument(
        "--parquet-input",
        required=True,
        help="S3 path to LangSmith Parquet exports",
    )
    parser.add_argument(
        "--cache-bucket",
        required=True,
        help="S3 bucket to write visualization cache",
    )
    parser.add_argument(
        "--results-ndjson",
        required=True,
        help="S3 path to question-results.ndjson from Step Function",
    )
    parser.add_argument(
        "--receipts-json",
        required=True,
        help="S3 path to receipts-lookup.json (CDN keys from DynamoDB)",
    )
    parser.add_argument(
        "--execution-id",
        default="",
        help="Execution ID for this run",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=50,
        help="Maximum number of questions to process (default: 50)",
    )
    return parser.parse_args()


def parse_s3_path(s3_path: str) -> tuple[str, str]:
    """Parse s3://bucket/key into (bucket, key)."""
    match = re.match(r"s3://([^/]+)/(.*)", s3_path)
    if not match:
        raise ValueError(f"Invalid S3 path: {s3_path}")
    return match.group(1), match.group(2)


def to_s3a(path: str) -> str:
    """Convert s3:// to s3a:// for Spark."""
    if path.startswith("s3://"):
        return "s3a://" + path[5:]
    return path


def load_receipts_lookup(s3_client: Any, receipts_json: str) -> dict[str, Any]:
    """Load receipts-lookup.json from S3.

    Returns:
        dict mapping "{image_id}_{receipt_id}" to receipt metadata.
    """
    try:
        bucket, key = parse_s3_path(receipts_json)
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except Exception:
        logger.exception("Failed to load receipts lookup from %s", receipts_json)
        return {}


def load_question_results(s3_client: Any, results_ndjson: str) -> list[dict]:
    """Load question-results.ndjson from S3.

    Returns:
        List of per-question result dicts ordered by question index.
    """
    try:
        bucket, key = parse_s3_path(results_ndjson)
        response = s3_client.get_object(Bucket=bucket, Key=key)
        lines = response["Body"].read().decode("utf-8").strip().split("\n")
        results = [json.loads(line) for line in lines if line.strip()]
        # Sort by questionIndex
        results.sort(key=lambda r: r.get("questionIndex", 0))
        return results
    except Exception:
        logger.exception("Failed to load question results from %s", results_ndjson)
        return []


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
                # Check for parquet files
                sub_page = s3_client.list_objects_v2(
                    Bucket=bucket, Prefix=sub_prefix, MaxKeys=1
                )
                if sub_page.get("Contents"):
                    obj_time = sub_page["Contents"][0].get("LastModified")
                    if latest_time is None or (obj_time and obj_time > latest_time):
                        latest_time = obj_time
                        latest_key = sub_prefix

        return latest_key
    except Exception:
        logger.exception("Failed to find latest export prefix")
        return None


def derive_trace_steps(
    child_runs: list[dict], question_result: dict, receipts_lookup: dict
) -> list[dict]:
    """Derive trace steps from child runs for the React component.

    Maps each child run to a TraceStep with type, content, and detail
    based on the node name and outputs.

    Args:
        child_runs: Sorted list of child run dicts from parquet.
        question_result: The question result from NDJSON.
        receipts_lookup: Receipt metadata lookup dict.

    Returns:
        List of trace step dicts matching the React TraceStep interface.
    """
    steps: list[dict] = []

    for run in child_runs:
        name = run.get("name", "").lower()
        outputs_str = run.get("outputs", "{}")
        try:
            outputs = json.loads(outputs_str) if isinstance(outputs_str, str) else (outputs_str or {})
        except (json.JSONDecodeError, TypeError):
            outputs = {}

        run_type = run.get("run_type", "")

        # Determine step type from run name
        step_type = _classify_run(name, run_type)
        if not step_type:
            continue

        step = {"type": step_type}

        if step_type == "plan":
            # Plan outputs are nested under "classification" key
            classification = outputs.get("classification", outputs)
            q_type = classification.get("question_type", "")
            strategy = classification.get("retrieval_strategy", "")
            rewrites = classification.get("query_rewrites", [])
            step["content"] = f"{q_type} → {strategy}" if q_type else "Classify question and choose retrieval strategy"
            step["detail"] = f"Query rewrites: {', '.join(rewrites)}" if rewrites else "Classify question and choose retrieval strategy"

        elif step_type == "agent":
            # Extract first ~100 chars of AIMessage content
            content = _extract_agent_content(outputs)
            step["content"] = content[:100] if content else "Deciding which tools to call"
            # Count tool calls in messages
            tool_calls = _count_tool_calls(outputs)
            if tool_calls > 0:
                step["detail"] = f"{tool_calls} tool call(s) planned"
            else:
                step["detail"] = "Ready to shape results"

        elif step_type == "tools":
            # Extract tool name + result summary
            tool_name, result_summary = _extract_tool_info(outputs)
            step["content"] = tool_name or "Tool execution"
            step["detail"] = result_summary[:100] if result_summary else ""

        elif step_type == "shape":
            summaries = outputs.get("shaped_summaries", [])
            n_receipts = len(summaries)
            n_items = sum(
                len(s.get("items", s.get("line_items", [])))
                for s in summaries
            )
            step["content"] = f"{n_receipts} receipts → {n_items} structured line items"
            step["detail"] = "Extract product names + amounts using word labels"
            # Include structured data for the component
            step["structuredData"] = [
                {
                    "merchant": s.get("merchant", "Unknown"),
                    "items": [
                        {"name": item.get("name", ""), "amount": item.get("amount", 0)}
                        for item in s.get("items", s.get("line_items", []))[:5]
                    ],
                }
                for s in summaries[:5]
            ]

        elif step_type == "synthesize":
            answer = outputs.get("final_answer", outputs.get("answer", ""))
            step["content"] = answer[:100] if answer else "Generating final answer"
            receipt_count = outputs.get("receipt_count", question_result.get("receiptCount", 0))
            step["detail"] = f"{receipt_count} receipts identified"
            # Include receipt evidence with CDN keys
            evidence = outputs.get("evidence", question_result.get("evidence", []))
            step["receipts"] = _enrich_evidence(evidence, receipts_lookup)

        steps.append(step)

    return steps


def _classify_run(name: str, run_type: str) -> Optional[str]:
    """Classify a run into a step type based on its name."""
    if "plan" in name or "classify" in name:
        return "plan"
    if "synthesize" in name or "synthesis" in name:
        return "synthesize"
    if "shape" in name:
        return "shape"
    if run_type == "tool" or "tool" in name:
        return "tools"
    if "agent" in name or run_type == "chain":
        return "agent"
    return None


def _extract_agent_content(outputs: dict) -> str:
    """Extract the text content from an agent node's output."""
    # Check for messages in output
    messages = outputs.get("messages", [])
    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if content and isinstance(content, str):
                return content
    # Fallback: direct content
    return outputs.get("content", outputs.get("output", ""))


def _count_tool_calls(outputs: dict) -> int:
    """Count tool calls in agent output messages."""
    messages = outputs.get("messages", [])
    count = 0
    for msg in messages:
        if isinstance(msg, dict):
            tool_calls = msg.get("tool_calls", [])
            if isinstance(tool_calls, list):
                count += len(tool_calls)
    return count


def _extract_tool_info(outputs: dict) -> tuple[str, str]:
    """Extract tool names and result summary from tool node output.

    Summarizes ALL tool messages in the node, not just the first.
    """
    messages = outputs.get("messages", [])
    tool_names: list[str] = []
    result_parts: list[str] = []

    for msg in messages:
        if isinstance(msg, dict):
            name = msg.get("name", "")
            content = msg.get("content", "")
            if name:
                tool_names.append(name)
            if content:
                result_parts.append(str(content)[:80])

    if not tool_names:
        return "", ""

    name_summary = ", ".join(tool_names)
    result_summary = " | ".join(result_parts) if result_parts else ""
    return name_summary, result_summary[:200]


def _enrich_evidence(
    evidence: list[dict], receipts_lookup: dict
) -> list[dict]:
    """Enrich evidence with CDN keys from receipts lookup."""
    enriched = []
    for e in evidence[:10]:  # Limit to 10 receipts
        image_id = e.get("imageId") or e.get("image_id", "")
        receipt_id = str(e.get("receiptId") or e.get("receipt_id", ""))
        key = f"{image_id}_{receipt_id}"
        lookup = receipts_lookup.get(key, {})

        enriched.append({
            "imageId": image_id,
            "merchant": e.get("merchant", ""),
            "item": e.get("item", ""),
            "amount": e.get("amount", 0),
            "thumbnailKey": lookup.get("cdn_webp_s3_key", lookup.get("cdn_s3_key", "")),
            "width": lookup.get("width", 350),
            "height": lookup.get("height", 900),
        })
    return enriched


def compute_stats(all_runs: list[dict], question_result: dict) -> dict:
    """Compute trace statistics for a question.

    Args:
        all_runs: ALL runs in the trace (not just depth-1), used to count
                  LLM calls and tool invocations at every depth.
        question_result: The NDJSON result dict (has cost from CostTrackingCallback).
    """
    llm_calls = 0
    tool_invocations = 0
    total_tokens = 0

    for run in all_runs:
        run_type = run.get("run_type", "")
        if run_type == "llm":
            llm_calls += 1
            total_tokens += (run.get("total_tokens") or 0)
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
    """Build a per-question cache JSON.

    Args:
        trace_id: LangSmith trace ID.
        root_run_id: ID of the root run (used to filter depth-1 children).
        all_runs: All runs in this trace (including nested).
        question_result: Result dict from NDJSON.
        receipts_lookup: CDN key lookup dict.

    Returns:
        dict matching the QAQuestionCache schema for the React component.
    """
    # Depth-1 runs are direct children of the root (plan, agent, tools, shape, synthesize)
    depth1_runs = sorted(
        [r for r in all_runs if r.get("parent_run_id") == root_run_id],
        key=lambda r: r.get("dotted_order", ""),
    )

    return {
        "question": question_result.get("question", ""),
        "questionIndex": question_result.get("questionIndex", 0),
        "traceId": trace_id,
        "trace": derive_trace_steps(depth1_runs, question_result, receipts_lookup),
        "stats": compute_stats(all_runs, question_result),
    }


def run_qa_cache_job(
    spark: SparkSession,
    parquet_input: str,
    cache_bucket: str,
    results_ndjson: str,
    receipts_json: str,
    execution_id: str = "",
    max_questions: int = 50,
    langchain_project: str = "",
) -> None:
    """Main entry point for QA viz cache generation.

    Args:
        spark: Spark session.
        parquet_input: S3 path to LangSmith Parquet exports.
        cache_bucket: S3 bucket for output cache.
        results_ndjson: S3 path to question-results.ndjson.
        receipts_json: S3 path to receipts-lookup.json.
        execution_id: Execution ID for this run.
        max_questions: Maximum questions to process.
        langchain_project: LangSmith project name (for metadata).
    """
    s3_client = boto3.client("s3")

    # 1. Load question results from NDJSON
    question_results = load_question_results(s3_client, results_ndjson)
    if not question_results:
        logger.error("No question results found in %s", results_ndjson)
        return

    logger.info("Loaded %d question results", len(question_results))

    # Build question text → index lookup for trace matching
    question_text_to_index: dict[str, int] = {}
    for r in question_results:
        q_text = r.get("question", "").strip()
        if q_text:
            question_text_to_index[q_text] = r.get("questionIndex", 0)

    # 2. Load receipts lookup
    receipts_lookup = load_receipts_lookup(s3_client, receipts_json)
    logger.info("Loaded %d receipt lookups", len(receipts_lookup))

    # 3. Read parquet traces
    spark_path = to_s3a(parquet_input)
    try:
        df = (
            spark.read.option("recursiveFileLookup", "true")
            .option("mergeSchema", "true")
            .parquet(spark_path)
        )
    except Exception:
        logger.exception("Failed to read parquet from %s", spark_path)
        return

    # Handle nanosecond timestamps
    for col_name in ["start_time", "end_time", "first_token_time"]:
        if col_name in df.columns:
            col_type = df.schema[col_name].dataType
            if isinstance(col_type, LongType):
                df = df.withColumn(
                    col_name,
                    F.from_unixtime(F.col(col_name) / 1_000_000_000),
                )

    # Add missing columns
    for col_name in ["trace_id", "parent_run_id", "total_tokens", "prompt_tokens", "completion_tokens"]:
        if col_name not in df.columns:
            df = df.withColumn(col_name, F.lit(None))

    # 4. No session_name filter needed — LangSmith exports are per-project.
    #    We identify our traces by matching root run question text against
    #    the known question list from NDJSON.

    total_rows = df.count()
    logger.info("Total parquet rows: %d", total_rows)

    if total_rows == 0:
        logger.warning("No traces found in parquet")
        _write_cache_from_ndjson(
            s3_client, cache_bucket, question_results, receipts_lookup,
            execution_id, langchain_project,
        )
        return

    # 5. Collect all runs grouped by trace_id
    runs = df.select(
        "id",
        "trace_id",
        "parent_run_id",
        "name",
        "run_type",
        "status",
        "dotted_order",
        "is_root",
        "inputs",
        "outputs",
        "total_tokens",
        "start_time",
        "end_time",
    ).collect()

    # Group by trace_id
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

    logger.info("Found %d unique traces, %d root runs", len(traces), len(root_runs))

    # 6. Match traces to question results by question text.
    #    Root run inputs contain the question at inputs.input.question
    #    (or inputs.question for simpler invocations).
    trace_to_question: dict[str, int] = {}
    for tid, root in root_runs.items():
        try:
            inputs_str = root.get("inputs", "{}")
            inputs = json.loads(inputs_str) if isinstance(inputs_str, str) else (inputs_str or {})

            # Try inputs.input.question first (LangGraph invocation pattern)
            question_text = ""
            inner_input = inputs.get("input", {})
            if isinstance(inner_input, dict):
                question_text = inner_input.get("question", "")
            if not question_text:
                question_text = inputs.get("question", "")

            question_text = question_text.strip()
            if question_text and question_text in question_text_to_index:
                trace_to_question[tid] = question_text_to_index[question_text]
        except (json.JSONDecodeError, TypeError):
            pass

    logger.info("Matched %d traces to questions", len(trace_to_question))

    # Build result index
    result_by_index = {r.get("questionIndex", -1): r for r in question_results}

    # 7. Build per-question cache and write to S3
    cache_files: list[dict] = []
    for tid, runs_list in traces.items():
        q_idx = trace_to_question.get(tid)
        if q_idx is None or q_idx not in result_by_index:
            continue

        question_result = result_by_index[q_idx]
        root = root_runs.get(tid, {})
        root_run_id = root.get("id", "")

        # All non-root runs for stats; depth-1 filtering happens inside build_question_cache
        non_root_runs = [r for r in runs_list if not r.get("is_root")]

        cache = build_question_cache(
            tid, root_run_id, non_root_runs, question_result, receipts_lookup
        )
        cache_files.append(cache)

    # For questions without traces, create minimal cache from NDJSON
    traced_indices = {c["questionIndex"] for c in cache_files}
    for result in question_results[:max_questions]:
        q_idx = result.get("questionIndex", -1)
        if q_idx not in traced_indices:
            cache_files.append({
                "question": result.get("question", ""),
                "questionIndex": q_idx,
                "traceId": "",
                "trace": _minimal_trace_from_result(result, receipts_lookup),
                "stats": {
                    "llmCalls": result.get("llmCalls", 0),
                    "toolInvocations": result.get("toolInvocations", 0),
                    "receiptsProcessed": result.get("receiptCount", 0),
                    "cost": result.get("cost", 0),
                },
            })

    cache_files.sort(key=lambda c: c["questionIndex"])

    # Write to S3
    _write_cache_files(
        s3_client, cache_bucket, cache_files, question_results,
        execution_id, langchain_project,
    )


def _minimal_trace_from_result(result: dict, receipts_lookup: dict) -> list[dict]:
    """Build a minimal trace from NDJSON result when parquet trace is unavailable."""
    steps = [
        {"type": "plan", "content": "Question classified", "detail": ""},
        {"type": "agent", "content": "Retrieved receipt data", "detail": ""},
        {"type": "shape", "content": f"{result.get('receiptCount', 0)} receipts shaped", "detail": ""},
    ]

    answer = result.get("answer", "")
    evidence = result.get("evidence", [])
    steps.append({
        "type": "synthesize",
        "content": answer[:100] if answer else "Answer generated",
        "detail": f"{result.get('receiptCount', 0)} receipts identified",
        "receipts": _enrich_evidence(evidence, receipts_lookup),
    })

    return steps


def _write_cache_from_ndjson(
    s3_client: Any,
    cache_bucket: str,
    question_results: list[dict],
    receipts_lookup: dict,
    execution_id: str,
    langchain_project: str = "",
) -> None:
    """Write cache files from NDJSON only (fallback when no parquet traces)."""
    cache_files = []
    for result in question_results:
        cache_files.append({
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
        })

    _write_cache_files(
        s3_client, cache_bucket, cache_files, question_results,
        execution_id, langchain_project,
    )


def _write_cache_files(
    s3_client: Any,
    cache_bucket: str,
    cache_files: list[dict],
    question_results: list[dict],
    execution_id: str,
    langchain_project: str = "",
) -> None:
    """Write per-question JSON files and metadata to S3."""
    # Write individual question files in parallel
    def upload_question(cache: dict) -> str:
        key = f"questions/question-{cache['questionIndex']}.json"
        s3_client.put_object(
            Bucket=cache_bucket,
            Key=key,
            Body=json.dumps(cache, default=str),
            ContentType="application/json",
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
            except Exception:
                idx = futures[future]
                logger.exception("Failed to write question-%d.json", idx)

    logger.info("Wrote %d question cache files to s3://%s/questions/", written, cache_bucket)

    # Write metadata.json
    total_cost = sum(r.get("cost", 0) for r in question_results)
    total_questions = len(question_results)
    metadata = {
        "total_questions": total_questions,
        "success_count": sum(1 for r in question_results if r.get("success")),
        "total_cost": round(total_cost, 6),
        "avg_cost_per_question": round(total_cost / total_questions, 6) if total_questions > 0 else 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution_id": execution_id,
        "langsmith_project": langchain_project or "qa-agent-marquee",
    }

    s3_client.put_object(
        Bucket=cache_bucket,
        Key="metadata.json",
        Body=json.dumps(metadata, indent=2),
        ContentType="application/json",
    )

    # Write latest.json
    s3_client.put_object(
        Bucket=cache_bucket,
        Key="latest.json",
        Body=json.dumps({"execution_id": execution_id, "generated_at": metadata["generated_at"]}),
        ContentType="application/json",
    )

    logger.info("Wrote metadata.json and latest.json to s3://%s/", cache_bucket)


def main() -> None:
    """Entry point for standalone execution."""
    args = parse_args()

    spark = (
        SparkSession.builder.appName("qa-viz-cache-generator")
        .config("spark.sql.legacy.parquet.nanosAsLong", "true")
        .getOrCreate()
    )

    try:
        run_qa_cache_job(
            spark=spark,
            parquet_input=args.parquet_input,
            cache_bucket=args.cache_bucket,
            results_ndjson=args.results_ndjson,
            receipts_json=args.receipts_json,
            execution_id=args.execution_id,
            max_questions=args.max_questions,
        )
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

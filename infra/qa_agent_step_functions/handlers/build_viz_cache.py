"""Build the public QA cache from private native trace records in S3.

Publish metadata last as the single pointer to an immutable set of question
files. Failed writes leave the previous cache visible to the API.
"""

import json
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import boto3

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


def _evidence(
    result: dict[str, Any],
    lookup: dict[str, Any],
) -> list[dict[str, Any]]:
    receipts = []
    for entry in result.get("evidence", []):
        image_id = entry.get("imageId") or entry.get("image_id")
        receipt_id = entry.get("receiptId") or entry.get("receipt_id")
        receipt = lookup.get(f"{image_id}_{receipt_id}")
        if not receipt:
            raise ValueError(
                f"Missing receipt metadata: {image_id}/{receipt_id}"
            )
        receipts.append(
            {
                "imageId": image_id,
                "merchant": entry.get("merchant", ""),
                "item": entry.get("item", ""),
                "amount": entry.get("amount", 0),
                "thumbnailKey": receipt.get("cdn_webp_s3_key")
                or receipt.get("cdn_s3_key", ""),
                "width": receipt.get("width", 0),
                "height": receipt.get("height", 0),
            }
        )
    return receipts


def build_question(
    result: dict[str, Any],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Keep actual node/tool timing; never invent unexecuted phases."""
    events = result.get("trace", [])
    started = result.get("startedAt") or min(
        (e["start_ts"] for e in events),
        default=0,
    )
    trace = []
    for event in events:
        phase = event["type"]
        if phase not in {"plan", "agent", "tools", "shape", "synthesize"}:
            raise ValueError(f"Unknown trace phase: {phase}")
        outputs = event.get("outputs") or {}
        content = {
            "plan": result["question"],
            "agent": "Reasoning",
            "tools": event.get("name", "Tool"),
            "shape": f"{result.get('receiptCount', 0)} receipts shaped",
            "synthesize": result.get("answer", ""),
        }[phase]
        if phase == "agent" and isinstance(outputs, dict):
            messages = outputs.get("messages") or []
            if messages and isinstance(messages[-1], dict):
                content = messages[-1].get("content") or content
                if not isinstance(content, str):
                    content = json.dumps(content)
        step = {
            "type": phase,
            "content": content,
            "startOffsetMs": max(
                0, round((event["start_ts"] - started) * 1000)
            ),
            "status": event.get("status", "ok"),
        }
        if event.get("duration_ms") is not None:
            step["durationMs"] = max(0, round(event["duration_ms"]))
        if event.get("error"):
            step["detail"] = event["error"]
        elif phase == "tools":
            # Public cache exposes the same tool arguments as the previous
            # cache, while full state and outputs remain in private qa-runs/.
            step["detail"] = str(event.get("inputs", ""))
        if phase == "synthesize":
            step["receipts"] = _evidence(result, lookup)
        trace.append(step)
    return {
        "question": result["question"],
        "questionIndex": result["questionIndex"],
        "traceId": result.get("traceId", ""),
        "trace": trace,
        "success": result.get("success", False),
        "error": result.get("error"),
        "stats": {
            "llmCalls": result.get("llmCalls", 0),
            "toolInvocations": result.get("toolInvocations", 0),
            "receiptsProcessed": result.get("receiptCount", 0),
            "cost": round(result.get("cost", 0), 6),
        },
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Publish a complete QA batch through one metadata pointer."""
    bucket = os.environ["BATCH_BUCKET"]
    client: S3Client = boto3.client("s3")
    execution_id = event["execution_id"]
    run_prefix = f"qa-runs/{execution_id}/"
    results_key = event["results_ndjson_key"]
    receipts_uri = event["receipts_lookup_path"]
    expected_uri = f"s3://{bucket}/{run_prefix}"
    if not results_key.startswith(run_prefix) or not receipts_uri.startswith(
        expected_uri
    ):
        raise ValueError("Inputs must belong to this QA execution and bucket")
    results = [
        json.loads(line)
        for line in client.get_object(
            Bucket=bucket,
            Key=results_key,
        )["Body"]
        .read()
        .decode()
        .splitlines()
        if line.strip()
    ]
    indices = [r["questionIndex"] for r in results]
    if not results or sorted(indices) != list(range(event["total_questions"])):
        raise ValueError("Incomplete or duplicate question results")
    lookup = json.loads(
        client.get_object(
            Bucket=bucket,
            Key=receipts_uri.removeprefix(f"s3://{bucket}/"),
        )["Body"].read()
    )
    # A unique prefix also isolates retries of the same execution.
    prefix = f"cache-runs/{execution_id}/{uuid4()}/questions/"
    for result in results:
        client.put_object(
            Bucket=bucket,
            Key=f"{prefix}question-{result['questionIndex']}.json",
            Body=json.dumps(build_question(result, lookup)).encode(),
            ContentType="application/json",
        )
    total_cost = sum(r.get("cost", 0) for r in results)
    metadata = {
        "total_questions": len(results),
        "cached_questions": len(results),
        "source_questions": len(results),
        "success_count": sum(bool(r.get("success")) for r in results),
        "total_cost": round(total_cost, 6),
        "avg_cost_per_question": round(total_cost / len(results), 6),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution_id": execution_id,
        "questions_prefix": prefix,
        "trace_source": "native-s3",
        "schema_version": 1,
        "langsmith_project": event.get("langchain_project", ""),
    }
    client.put_object(
        Bucket=bucket,
        Key="metadata.json",
        Body=json.dumps(metadata).encode(),
        ContentType="application/json",
    )
    return metadata

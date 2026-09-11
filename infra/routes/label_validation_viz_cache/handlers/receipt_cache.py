"""Build the existing receipt visualization payload using plain Python."""

import heapq
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

MAX_TRACE_OBJECTS = 500
MAX_TRACE_BYTES = 100 * 1024 * 1024
MAX_RECEIPTS = 50
SOURCES = {
    "label_validation_chroma": "chroma",
    "chroma_label_validation": "chroma",
    "label_validation_similarity": "chroma",
    "similarity_label_validation": "chroma",
    "label_validation_llm": "llm",
    "llm_batch_validation": "llm",
}


def json_object(value: Any) -> dict[str, Any]:
    """Decode the native span's JSON payload columns."""
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def timestamp(value: str) -> float:
    """Compare UTC timestamps numerically, including fractional seconds."""
    return datetime.fromisoformat(value).timestamp()


def read_traces(s3: Any, bucket: str) -> list[dict[str, Any]]:
    """Read recent trace bundles plus older ancestors of deferred work.

    Only native NDJSON is read. Large inputs fail before cache publication;
    historical Parquet objects never enter the Lambda's memory.
    """
    objects = [
        obj
        for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix="native-traces/"
        )
        for obj in page.get("Contents", [])
        if obj["Key"].endswith(".ndjson")
    ]
    recent = heapq.nlargest(
        MAX_TRACE_OBJECTS,
        objects,
        key=lambda obj: (obj["LastModified"], obj["Key"]),
    )
    rows: list[dict[str, Any]] = []
    size = 0

    def read(obj: dict[str, Any]) -> None:
        nonlocal size
        size += obj["Size"]
        if size > MAX_TRACE_BYTES:
            raise ValueError("Native receipt trace sample exceeds byte limit")
        body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"]
        try:
            for line in body.iter_lines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("schema_version") != 1:
                    raise ValueError("Unsupported native receipt trace schema")
                if (
                    row.get("status") == "error"
                    or row.get("capture_status") == "error"
                ):
                    continue
                rows.append(row)
        finally:
            body.close()

    for obj in recent:
        read(obj)
    trace_ids = {row["trace_id"] for row in rows}
    loaded = {obj["Key"] for obj in recent}
    for obj in objects:
        trace_id = obj["Key"].rsplit("/", 1)[-1][:36]
        if obj["Key"] not in loaded and trace_id in trace_ids:
            read(obj)
    return rows


def receipt_roots(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select one latest successful root per receipt."""
    roots = sorted(
        (r for r in rows if r.get("name") == "receipt_processing"),
        key=lambda r: timestamp(r["start_time"]),
        reverse=True,
    )
    selected: dict[tuple[str, int], dict[str, Any]] = {}
    for root in roots:
        metadata = json_object(root.get("extra")).get("metadata", {})
        if not metadata.get("image_id") or metadata.get("receipt_id") is None:
            continue
        key = (metadata["image_id"], int(metadata["receipt_id"]))
        selected.setdefault(
            key, {**root, "image_id": key[0], "receipt_id": key[1]}
        )
    return list(selected.values())


def build_receipt(
    root: dict[str, Any],
    spans: list[dict[str, Any]],
    receipt: Any,
    words: list[Any],
    labels: list[Any],
) -> dict[str, Any] | None:
    """Combine real validation decisions with current word geometry/labels."""
    decisions: dict[tuple[int, int], dict[str, Any]] = {}
    durations = {"chroma": 0.0, "llm": 0.0}
    # LLM decisions supersede similarity decisions; retries use the latest span.
    ordered = sorted(
        spans,
        key=lambda r: (
            SOURCES.get(r.get("name"), ""),
            timestamp(r["start_time"]),
        ),
    )
    for span in ordered:
        source = SOURCES.get(span.get("name"))
        if not source:
            continue
        durations[source] += max(
            0, timestamp(span["end_time"]) - timestamp(span["start_time"])
        )
        outputs = json_object(span.get("outputs"))
        validations = outputs.get(
            "validations", [outputs] if "line_id" in outputs else []
        )
        for value in validations:
            decision = str(value.get("decision", "")).upper().replace(" ", "_")
            if decision in {"CORRECT", "CORRECTED"}:
                decision = "INVALID"
            if decision not in {"VALID", "INVALID", "NEEDS_REVIEW"}:
                continue
            decisions[(int(value["line_id"]), int(value["word_id"]))] = {
                "validation_source": source,
                "decision": decision,
            }
    label_map = {(l.line_id, l.word_id): l for l in labels}
    viz_words = []
    for word in words:
        key = (word.line_id, word.word_id)
        label = label_map.get(key)
        if label is None:
            continue
        viz_words.append(
            {
                "text": word.text,
                "line_id": word.line_id,
                "word_id": word.word_id,
                "bbox": word.bounding_box,
                "label": label.label,
                "validation_status": label.validation_status,
                **decisions.get(
                    key, {"validation_source": None, "decision": None}
                ),
            }
        )
    if not viz_words or not receipt.cdn_s3_key:
        return None
    tiers = {}
    timings = {}
    for source in ("chroma", "llm"):
        counts = {
            decision: sum(
                w["validation_source"] == source and w["decision"] == decision
                for w in viz_words
            )
            for decision in ("VALID", "INVALID", "NEEDS_REVIEW")
        }
        count = sum(counts.values())
        tiers[source] = (
            {
                "tier": source,
                "words_count": count,
                "duration_seconds": durations[source],
                "decisions": counts,
            }
            if count or source == "chroma"
            else None
        )
        if durations[source] > 0:
            timings[f"{source}_validation"] = {
                "duration_ms": durations[source] * 1000,
                "duration_seconds": durations[source],
            }
    end = max(timestamp(s["end_time"]) for s in spans)
    total = max(0, end - timestamp(root["start_time"]))
    timings["total"] = {"duration_ms": total * 1000, "duration_seconds": total}
    assets = {
        key: getattr(receipt, key, None)
        for key in (
            "cdn_s3_key",
            "cdn_webp_s3_key",
            "cdn_avif_s3_key",
            "cdn_medium_s3_key",
            "cdn_medium_webp_s3_key",
            "cdn_medium_avif_s3_key",
            "width",
            "height",
        )
    }
    return {
        "image_id": root["image_id"],
        "receipt_id": root["receipt_id"],
        "merchant_name": json_object(root.get("outputs")).get("merchant_name"),
        "words": viz_words,
        **tiers,
        "step_timings": timings,
        **assets,
    }


def group_spans(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Join synchronous and SQS-delivered spans by trace identity."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["trace_id"]].append(row)
    return dict(grouped)


def aggregate_stats(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """Preserve the API's existing aggregate statistics."""
    counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
    chroma_words = total_words = 0
    for receipt in receipts:
        for source in ("chroma", "llm"):
            tier = receipt[source]
            if not tier:
                continue
            total_words += tier["words_count"]
            if source == "chroma":
                chroma_words += tier["words_count"]
            for decision in counts:
                counts[decision] += tier["decisions"][decision]
    return {
        "total_receipts": len(receipts),
        "avg_chroma_rate": (
            round(chroma_words / total_words * 100, 1) if total_words else 0.0
        ),
        "total_valid": counts["VALID"],
        "total_invalid": counts["INVALID"],
        "total_needs_review": counts["NEEDS_REVIEW"],
    }

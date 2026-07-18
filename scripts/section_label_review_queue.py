#!/usr/bin/env python3
"""Generate a read-only low-prior review queue from current VALID dev labels.

This command is read-only. The DynamoDB table must be supplied through
``DYNAMODB_TABLE_NAME`` and the environment must explicitly be ``dev``.

At the selected 0.05 threshold, the 2026-07-18 validation study flagged
26.3% of known-bad sectioned labels versus 6.3% of currently VALID core
labels, a 4.2x enrichment. Outputs reflect mutable dev state and are intended
for local review rather than source control.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator, Optional

import boto3

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "receipt_dynamo"))

# pylint: disable=wrong-import-position
from receipt_dynamo.constants import CORE_LABELS  # isort: skip

# pylint: enable=wrong-import-position

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"
AWS_REGION = "us-east-1"
_MAX_BATCH_RETRIES = 8

_GATE_PATH = _REPO_ROOT / "receipt_upload" / "receipt_upload" / "label_section_gate.py"
_DEFAULT_PRIORS = (
    _REPO_ROOT
    / "receipt_upload"
    / "receipt_upload"
    / "assets"
    / "section_label_priors_v1.json"
)
_LEGACY_SECTION_TYPES = {
    "HEADER",
    "ITEMS_DESCRIPTION",
    "ITEMS_VALUE",
}
_SECTION_SK_RE = re.compile(r"^RECEIPT#(?P<receipt>\d+)#SECTION#")
_LABEL_SK_RE = re.compile(
    r"^IMAGE#(?P<image>.+?)"
    r"#RECEIPT#(?P<receipt>\d+)"
    r"#LINE#(?P<line>\d+)"
    r"#WORD#(?P<word>\d+)"
    r"#LABEL#(?P<label>.+)$"
)


def _load_gate_module() -> ModuleType:
    """Load the leaf gate module without importing receipt_upload itself."""
    # receipt_upload.__init__ pulls numpy and Pillow. Loading this dependency
    # leaf directly keeps the review script's runtime to stdlib plus boto3.
    module_name = "_receipt_upload_label_section_gate"
    spec = importlib.util.spec_from_file_location(module_name, _GATE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load gate module from {_GATE_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _arguments(gate: ModuleType) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate a read-only low-prior VALID-label review queue."
    )
    parser.add_argument(
        "--environment",
        default=os.environ.get("DEPLOYMENT_ENVIRONMENT"),
        help="Deployment environment; must be dev.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd() / "section_label_review_queue_out",
        help="Directory receiving the JSON and Markdown review queues.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=gate.DEFAULT_LOW_PRIOR_THRESHOLD,
        help="Prior values strictly below this threshold are queued.",
    )
    parser.add_argument(
        "--priors",
        type=Path,
        default=_DEFAULT_PRIORS,
        help="Section-label prior artifact.",
    )
    return parser.parse_args()


def _query_all(
    client: Any,
    table_name: str,
    index: str,
    pk_attr: str,
    pk_value: str,
    projection: str,
    names: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    """Query every page for one exact GSI partition-key value."""
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "TableName": table_name,
        "IndexName": index,
        "KeyConditionExpression": f"{pk_attr} = :p",
        "ExpressionAttributeValues": {":p": {"S": pk_value}},
        "ProjectionExpression": projection,
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names

    while True:
        response = client.query(**kwargs)
        items.extend(response.get("Items", []))
        if not response.get("LastEvaluatedKey"):
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _string_attribute(item: dict[str, Any], name: str) -> str:
    """Return a DynamoDB string attribute or an empty string."""
    value = item.get(name, {}).get("S")
    return value if isinstance(value, str) else ""


def _optional_string_attribute(
    item: dict[str, Any],
    name: str,
) -> Optional[str]:
    """Return an optional DynamoDB string attribute."""
    value = item.get(name, {}).get("S")
    return value if isinstance(value, str) else None


def _line_ids(item: dict[str, Any]) -> set[int]:
    """Parse numeric or string line IDs from a DynamoDB list attribute."""
    output: set[int] = set()
    for value in item.get("line_ids", {}).get("L", []):
        raw_line_id = value.get("S") or value.get("N")
        if raw_line_id is None:
            continue
        try:
            output.add(int(raw_line_id))
        except (TypeError, ValueError):
            continue
    return output


def _section_identity(
    item: dict[str, Any],
) -> Optional[tuple[str, int]]:
    """Parse an image and receipt identity from a section item."""
    partition_key = _string_attribute(item, "PK")
    sort_key = _string_attribute(item, "SK")
    match = _SECTION_SK_RE.match(sort_key)
    if not partition_key.startswith("IMAGE#") or match is None:
        return None
    return partition_key.removeprefix("IMAGE#"), int(match.group("receipt"))


def _label_identity(
    item: dict[str, Any],
) -> Optional[tuple[str, int, int, int, str]]:
    """Parse word-label coordinates and label from a GSI3 sort key."""
    sort_key = _string_attribute(item, "GSI3SK")
    match = _LABEL_SK_RE.match(sort_key)
    if match is None:
        return None
    return (
        match.group("image"),
        int(match.group("receipt")),
        int(match.group("line")),
        int(match.group("word")),
        match.group("label"),
    )


def _build_section_map(
    section_items: list[dict[str, Any]],
    canonical_sections: set[str],
) -> dict[tuple[str, int, int], set[str]]:
    """Map each line to its set of VALID canonical section types."""
    sections_by_line: dict[tuple[str, int, int], set[str]] = {}

    for item in section_items:
        if _string_attribute(item, "validation_status").upper() != "VALID":
            continue

        section_type = _string_attribute(item, "section_type")
        if section_type in _LEGACY_SECTION_TYPES:
            continue
        if section_type not in canonical_sections:
            continue

        identity = _section_identity(item)
        if identity is None:
            continue

        image_id, receipt_id = identity
        for line_id in _line_ids(item):
            line_key = (image_id, receipt_id, line_id)
            sections_by_line.setdefault(line_key, set()).add(section_type)

    return sections_by_line


def _word_key(row: dict[str, Any]) -> tuple[str, str]:
    """Return the base-table partition and sort keys for a queued word."""
    partition_key = f"IMAGE#{row['image_id']}"
    sort_key = (
        f"RECEIPT#{row['receipt_id']:05d}"
        f"#LINE#{row['line_id']:05d}"
        f"#WORD#{row['word_id']:05d}"
    )
    return partition_key, sort_key


def _chunks(
    values: list[tuple[str, str]],
    size: int,
) -> Iterator[list[tuple[str, str]]]:
    """Yield fixed-size slices of key tuples."""
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _fetch_word_texts(
    client: Any,
    table_name: str,
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], str]:
    """Batch-fetch word text, retrying all DynamoDB unprocessed keys."""
    unique_keys = sorted({_word_key(row) for row in rows})
    output: dict[tuple[str, str], str] = {}

    for key_chunk in _chunks(unique_keys, 100):
        keys = [
            {
                "PK": {"S": partition_key},
                "SK": {"S": sort_key},
            }
            for partition_key, sort_key in key_chunk
        ]
        pending: dict[str, Any] = {
            table_name: {
                "Keys": keys,
                "ProjectionExpression": "PK, SK, #t",
                "ExpressionAttributeNames": {"#t": "text"},
            }
        }

        attempts = 0
        while pending:
            response = client.batch_get_item(RequestItems=pending)
            for item in response.get("Responses", {}).get(table_name, []):
                partition_key = _string_attribute(item, "PK")
                sort_key = _string_attribute(item, "SK")
                text = _string_attribute(item, "text")
                output[(partition_key, sort_key)] = text
            pending = response.get("UnprocessedKeys", {})
            if not pending:
                break
            attempts += 1
            if attempts > _MAX_BATCH_RETRIES:
                remaining = len(pending.get(table_name, {}).get("Keys", []))
                raise SystemExit(
                    f"BatchGetItem left {remaining} unprocessed keys after "
                    f"{_MAX_BATCH_RETRIES} retries"
                )
            time.sleep(min(2.0, 0.1 * (2**attempts)))

    return output


def _queue_rows(
    label_items: list[dict[str, Any]],
    sections_by_line: dict[tuple[str, int, int], set[str]],
    priors: dict[str, Any],
    gate: ModuleType,
    threshold: float,
) -> list[dict[str, Any]]:
    """Evaluate VALID core labels and retain low-prior rows."""
    rows: list[dict[str, Any]] = []

    for item in label_items:
        identity = _label_identity(item)
        if identity is None:
            continue

        image_id, receipt_id, line_id, word_id, label = identity
        if label not in CORE_LABELS:
            continue

        line_sections = sorted(
            sections_by_line.get((image_id, receipt_id, line_id), set())
        )
        result = gate.evaluate_label_section(
            label,
            line_sections,
            priors,
            threshold=threshold,
        )
        if result.verdict != gate.VERDICT_LOW_PRIOR:
            continue

        rows.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "line_id": line_id,
                "word_id": word_id,
                "label": label,
                "word_text": None,
                "line_sections": line_sections,
                "prior": result.prior,
                "label_proposed_by": _optional_string_attribute(
                    item,
                    "label_proposed_by",
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            row["label"],
            row["image_id"],
            row["receipt_id"],
            row["line_id"],
            row["word_id"],
        )
    )
    return rows


def _markdown_text(value: Optional[str]) -> str:
    """Normalize, truncate, and escape word text for a Markdown table."""
    normalized = " ".join((value or "").split())[:40]
    escaped = (
        normalized.replace("\\", "\\\\")
        .replace("|", r"\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return escaped


def _markdown_report(
    threshold: float,
    rows: list[dict[str, Any]],
    by_label: dict[str, int],
) -> str:
    """Render the review queue as deterministic Markdown."""
    ordered_counts = sorted(
        by_label.items(),
        key=lambda item: (-item[1], item[0]),
    )
    lines = [
        "# Section-label review queue",
        "",
        f"- Threshold: `{threshold}`",
        f"- Total rows: `{len(rows)}`",
        "",
        "## Counts by label",
        "",
        "| Label | Count |",
        "| --- | ---: |",
    ]
    for label, count in ordered_counts:
        lines.append(f"| {label} | {count} |")

    for label, _count in ordered_counts:
        lines.extend(
            [
                "",
                f"## {label}",
                "",
                ("| image_id | receipt | line | word | text | sections | " "prior |"),
                "| --- | ---: | ---: | ---: | --- | --- | ---: |",
            ]
        )
        for row in rows:
            if row["label"] != label:
                continue
            sections = ", ".join(row["line_sections"])
            prior = float(row["prior"])
            lines.append(
                f"| {row['image_id']} "
                f"| {row['receipt_id']} "
                f"| {row['line_id']} "
                f"| {row['word_id']} "
                f"| {_markdown_text(row['word_text'])} "
                f"| {sections} "
                f"| {prior:.6f} |"
            )

    return "\n".join(lines) + "\n"


def _write_outputs(
    output_dir: Path,
    threshold: float,
    priors_sha256: str,
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Write deterministic JSON and Markdown queue files."""
    counts = Counter(row["label"] for row in rows)
    by_label = dict(sorted(counts.items()))
    payload = {
        "threshold": threshold,
        "priors_sha256": priors_sha256,
        "row_count": len(rows),
        "by_label": by_label,
        "rows": rows,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "section_label_review_queue.json"
    markdown_path = output_dir / "section_label_review_queue.md"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(
        _markdown_report(threshold, rows, by_label),
        encoding="utf-8",
        newline="\n",
    )
    return by_label


def main() -> int:
    """Validate the dev guard and generate the read-only review queue."""
    gate = _load_gate_module()
    args = _arguments(gate)
    if not math.isfinite(args.threshold) or not 0.0 <= args.threshold <= 1.0:
        raise SystemExit(
            f"Refusing threshold {args.threshold!r}; must be finite and "
            "within [0, 1]"
        )
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if args.environment != "dev":
        raise SystemExit("Refusing to read anything except the dev environment")
    if table_name != DEV_TABLE or table_name == PROD_TABLE:
        raise SystemExit(
            f"Refusing table {table_name!r}; expected exact dev table " f"{DEV_TABLE!r}"
        )

    priors_bytes = args.priors.read_bytes()
    priors_sha256 = hashlib.sha256(priors_bytes).hexdigest()
    # Parse the exact bytes just hashed so priors_sha256 always describes
    # the priors actually evaluated, even if the file is replaced meanwhile.
    priors = json.loads(priors_bytes)
    if priors.get("source_environment") != "dev":
        raise SystemExit(
            "Refusing priors artifact whose source_environment is not 'dev'"
        )

    client = boto3.client("dynamodb", region_name=AWS_REGION)
    section_items = _query_all(
        client,
        table_name,
        "GSITYPE",
        "#T",
        "RECEIPT_SECTION",
        "PK, SK, section_type, line_ids, validation_status",
        {"#T": "TYPE"},
    )
    label_items = _query_all(
        client,
        table_name,
        "GSI3",
        "GSI3PK",
        "VALIDATION_STATUS#VALID",
        "GSI3SK, label_proposed_by",
    )

    canonical_sections = set(priors.get("sections", []))
    sections_by_line = _build_section_map(
        section_items,
        canonical_sections,
    )
    rows = _queue_rows(
        label_items,
        sections_by_line,
        priors,
        gate,
        args.threshold,
    )

    word_texts = _fetch_word_texts(client, table_name, rows)
    for row in rows:
        row["word_text"] = word_texts.get(_word_key(row))

    by_label = _write_outputs(
        args.output_dir,
        args.threshold,
        priors_sha256,
        rows,
    )

    print(f"Low-prior rows: {len(rows)}")
    for label, count in sorted(
        by_label.items(),
        key=lambda item: (-item[1], item[0]),
    )[:8]:
        print(f"{label}: {count}")
    print(
        "Reminder: these outputs depend on current dev state and must NOT be "
        "committed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

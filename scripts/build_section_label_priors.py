#!/usr/bin/env python3
"""Build deterministic section-to-word-label priors from QA-valid dev data.

This command is read-only. The DynamoDB table must be supplied through
``DYNAMODB_TABLE_NAME`` and the environment must explicitly be ``dev``.

The measured 2026-07-18 dev corpus contained 5,768 section rows, including
5,751 VALID rows, and 29,015 VALID word-label rows. Of 813 receipts with
VALID labels, 794 also had VALID sections (98%).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import boto3

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "receipt_dynamo"))

# pylint: disable=wrong-import-position
from receipt_dynamo.constants import CORE_LABELS, SectionType  # isort: skip

# pylint: enable=wrong-import-position

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"
AWS_REGION = "us-east-1"

_DEFAULT_OUTPUT = (
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


def _arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=("Build deterministic section-to-word-label priors from dev data.")
    )
    parser.add_argument(
        "--environment",
        default=os.environ.get("DEPLOYMENT_ENVIRONMENT"),
        help="Deployment environment; must be dev.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Destination JSON artifact.",
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


def _canonical_sections() -> list[str]:
    """Return sorted canonical section values from the shared enum."""
    return sorted(
        {
            str(member.value)
            for member in SectionType
            if str(member.value) not in _LEGACY_SECTION_TYPES
        }
    )


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


def _label_identity(
    item: dict[str, Any],
) -> Optional[tuple[str, int, int, str]]:
    """Parse image, receipt, line, and label from a GSI3 sort key."""
    sort_key = _string_attribute(item, "GSI3SK")
    match = _LABEL_SK_RE.match(sort_key)
    if match is None:
        return None
    return (
        match.group("image"),
        int(match.group("receipt")),
        int(match.group("line")),
        match.group("label"),
    )


def _snapshot_sha256(observations: list[list[Any]]) -> str:
    """Hash sorted observations using the deterministic corpus encoding."""
    payload = json.dumps(
        sorted(observations),
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _build_model(
    section_items: list[dict[str, Any]],
    label_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the deterministic prior artifact from raw DynamoDB items."""
    canonical_sections = _canonical_sections()
    canonical_section_set = set(canonical_sections)

    valid_section_row_count = 0
    legacy_valid_section_rows_excluded = 0
    sections_by_line: dict[tuple[str, int, int], set[str]] = {}
    receipts_with_valid_sections: set[tuple[str, int]] = set()

    for item in section_items:
        if _string_attribute(item, "validation_status").upper() != "VALID":
            continue

        valid_section_row_count += 1
        section_type = _string_attribute(item, "section_type")
        if section_type in _LEGACY_SECTION_TYPES:
            legacy_valid_section_rows_excluded += 1
            continue
        if section_type not in canonical_section_set:
            continue

        identity = _section_identity(item)
        if identity is None:
            continue

        image_id, receipt_id = identity
        receipts_with_valid_sections.add((image_id, receipt_id))
        for line_id in _line_ids(item):
            line_key = (image_id, receipt_id, line_id)
            sections_by_line.setdefault(line_key, set()).add(section_type)

    tallies: dict[str, dict[str, Any]] = {
        label: {
            "sectioned": 0,
            "section_pairs": Counter(),
            "total": 0,
            "unsectioned": 0,
        }
        for label in CORE_LABELS
    }
    observations: list[list[Any]] = []
    receipts_with_valid_labels: set[tuple[str, int]] = set()
    core_label_row_count = 0
    sectioned_core_label_row_count = 0

    for item in label_items:
        identity = _label_identity(item)
        if identity is None:
            continue

        image_id, receipt_id, line_id, label = identity
        receipts_with_valid_labels.add((image_id, receipt_id))
        if label not in CORE_LABELS:
            continue

        core_label_row_count += 1
        label_tally = tallies[label]
        label_tally["total"] += 1
        line_key = (image_id, receipt_id, line_id)
        line_sections = sections_by_line.get(line_key, set())

        if line_sections:
            label_tally["sectioned"] += 1
            sectioned_core_label_row_count += 1
            for section_type in sorted(line_sections):
                label_tally["section_pairs"][section_type] += 1
                observations.append(
                    [
                        image_id,
                        receipt_id,
                        line_id,
                        label,
                        section_type,
                    ]
                )
        else:
            # Every core label row lands in the hashed corpus, sectioned or
            # not, so any change to the artifact's inputs changes the hash.
            label_tally["unsectioned"] += 1
            observations.append(
                [
                    image_id,
                    receipt_id,
                    line_id,
                    label,
                    "<line-unsectioned>",
                ]
            )

    labels: dict[str, Any] = {}
    for label in sorted(CORE_LABELS):
        tally = tallies[label]
        section_pairs: Counter[str] = tally["section_pairs"]
        pair_total = sum(section_pairs.values())
        section_probabilities = {
            section_type: {
                "count": count,
                "p": round(count / pair_total, 6),
            }
            for section_type, count in sorted(section_pairs.items())
        }
        labels[label] = {
            "total": tally["total"],
            "sectioned": tally["sectioned"],
            "unsectioned": tally["unsectioned"],
            "sections": section_probabilities,
        }

    receipts_with_sections_and_labels = (
        receipts_with_valid_sections & receipts_with_valid_labels
    )
    source = {
        "section_row_count": len(section_items),
        "valid_section_row_count": valid_section_row_count,
        "legacy_valid_section_rows_excluded": (legacy_valid_section_rows_excluded),
        "valid_label_row_count": len(label_items),
        "core_label_row_count": core_label_row_count,
        "sectioned_core_label_row_count": sectioned_core_label_row_count,
        "receipts_with_valid_labels": len(receipts_with_valid_labels),
        "receipts_with_valid_sections_and_labels": len(
            receipts_with_sections_and_labels
        ),
        "corpus_sha256": _snapshot_sha256(observations),
    }

    return {
        "schema_version": 1,
        "model_source": "section-label-prior-v1",
        "source_environment": "dev",
        "denominator": (
            "pairs of (VALID core-label row, VALID canonical section on the "
            "row's line); p = count / label pair total"
        ),
        "sections": canonical_sections,
        "source": source,
        "labels": labels,
    }


def main() -> int:
    """Validate the dev guard, read DynamoDB, and write the prior artifact."""
    args = _arguments()
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if args.environment != "dev":
        raise SystemExit("Refusing to read anything except the dev environment")
    if table_name != DEV_TABLE or table_name == PROD_TABLE:
        raise SystemExit(
            f"Refusing table {table_name!r}; expected exact dev table " f"{DEV_TABLE!r}"
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
        "GSI3SK",
    )

    model = _build_model(section_items, label_items)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(model, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(model["source"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

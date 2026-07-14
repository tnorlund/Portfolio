#!/usr/bin/env python3
"""Build deterministic row-section priors from QA-valid dev sections.

This command is read-only. The DynamoDB table must be supplied through
``DYNAMODB_TABLE_NAME`` and the environment must explicitly be ``dev``.
"""

# Monorepo sibling paths must be installed before importing runtime packages.
# pylint: disable=wrong-import-position

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _package in ("receipt_dynamo", "receipt_chroma", "receipt_upload"):
    sys.path.insert(0, str(_REPO_ROOT / _package))

from receipt_upload.section_assignment import (
    extract_row_features,
    learn_prior,
    normalize_merchant_key,
)

from receipt_chroma.embedding.formatting import build_receipt_rows
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    OperationError,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--environment",
        default=os.environ.get("DEPLOYMENT_ENVIRONMENT"),
        help="Must be dev (or DEPLOYMENT_ENVIRONMENT)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_REPO_ROOT
        / "receipt_upload/receipt_upload/assets/section_order_priors_v1.json",
    )
    return parser.parse_args()


def _section_snapshot(sections: list[Any]) -> str:
    records = [
        {
            "image_id": section.image_id,
            "receipt_id": section.receipt_id,
            "section_type": str(section.section_type),
            "line_ids": sorted(section.line_ids),
            "validation_status": section.validation_status,
        }
        for section in sections
    ]
    payload = json.dumps(
        sorted(
            records,
            key=lambda item: (
                item["image_id"],
                item["receipt_id"],
                item["section_type"],
            ),
        ),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _row_label(row: Any, line_sections: dict[int, str]) -> str | None:
    votes = Counter(
        line_sections[line_id]
        for line_id in row.line_ids
        if line_id in line_sections
    )
    if not votes:
        return None
    maximum = max(votes.values())
    leaders = sorted(
        label for label, count in votes.items() if count == maximum
    )
    primary = line_sections.get(row.row_id)
    return primary if primary in leaders else leaders[0]


def _merchant_name(client: Any, image_id: str, receipt_id: int) -> str:
    try:
        return normalize_merchant_key(
            client.get_receipt_place(image_id, receipt_id).merchant_name
        )
    except EntityNotFoundError:
        return ""
    except OperationError as error:
        if "does not match entity keys" not in str(error):
            raise
        response = client._client.get_item(  # pylint: disable=protected-access
            TableName=client.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {"S": f"RECEIPT#{receipt_id:05d}#PLACE"},
            },
            ProjectionExpression="merchant_name",
            ConsistentRead=True,
        )
        return normalize_merchant_key(
            response.get("Item", {}).get("merchant_name", {}).get("S", "")
        )


def build_model(client: Any, sections: list[Any]) -> dict[str, Any]:
    valid = [
        section
        for section in sections
        if section.validation_status == ValidationStatus.VALID.value
    ]
    by_receipt: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for section in valid:
        by_receipt[(section.image_id, section.receipt_id)].append(section)

    labeled: list[list[Any]] = []
    by_merchant: dict[str, list[list[Any]]] = defaultdict(list)
    skipped: Counter[str] = Counter()
    receipt_keys = sorted(by_receipt)
    for index, (image_id, receipt_id) in enumerate(receipt_keys, start=1):
        receipt_sections = by_receipt[(image_id, receipt_id)]
        lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
        words = client.list_receipt_words_from_receipt(image_id, receipt_id)
        if not lines:
            skipped["no_lines"] += 1
            continue
        rows = build_receipt_rows(lines, words)
        features = extract_row_features(rows, lines)
        line_sections = {
            line_id: str(section.section_type)
            for section in receipt_sections
            for line_id in section.line_ids
        }
        receipt_labels = [
            (feature, label)
            for feature in features
            if (label := _row_label(feature.row, line_sections)) is not None
        ]
        if not receipt_labels:
            skipped["no_labeled_rows"] += 1
            continue
        labeled.append(receipt_labels)
        merchant = _merchant_name(client, image_id, receipt_id)
        if merchant:
            by_merchant[merchant].append(receipt_labels)
        if index % 100 == 0 or index == len(receipt_keys):
            print(f"trained {index}/{len(receipt_keys)} receipts", flush=True)

    merchant_receipt_counts = [
        len(receipts) for receipts in by_merchant.values()
    ]
    merchant_receipt_median = (
        median(merchant_receipt_counts) if merchant_receipt_counts else 0
    )
    eligible_merchants = {
        merchant: receipts
        for merchant, receipts in by_merchant.items()
        if len(receipts) > merchant_receipt_median
    }

    return {
        "schema_version": 1,
        "model_source": "upload-determinism-v1",
        "source_environment": "dev",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "section_count": len(sections),
            "valid_section_count": len(valid),
            "receipt_count": len(by_receipt),
            "trained_receipt_count": len(labeled),
            "section_snapshot_sha256": _section_snapshot(sections),
            "skipped": dict(sorted(skipped.items())),
            "merchant_prior_min_receipts": int(merchant_receipt_median) + 1,
            "merchant_prior_cutoff_derivation": (
                "strictly greater than corpus median merchant receipt count"
            ),
        },
        "global": learn_prior(labeled),
        "merchants": {
            merchant: learn_prior(receipts, include_tokens=False)
            for merchant, receipts in sorted(eligible_merchants.items())
        },
    }


def main() -> int:
    args = _arguments()
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if args.environment != "dev":
        raise SystemExit(
            "Refusing to read anything except the dev environment"
        )
    if not table_name:
        raise SystemExit("DYNAMODB_TABLE_NAME is required")
    client = DynamoClient(table_name)
    sections, cursor = client.list_receipt_sections()
    if cursor is not None:
        raise RuntimeError("Unexpected unconsumed section pagination cursor")
    model = build_model(client, sections)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(model["source"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

from receipt_chroma.embedding.formatting import build_receipt_rows
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    OperationError,
)
from receipt_upload.section_assignment import (
    extract_row_features,
    learn_prior,
    normalize_merchant_key,
)

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"


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
        / "receipt_upload/receipt_upload/assets/section_order_priors_v2.json",
    )
    parser.add_argument(
        "--exclude-manifest",
        type=Path,
        help=("JSON array of image_id/receipt_id keys held out from training"),
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


def _row_label(row: Any, line_sections: dict[int, set[str]]) -> str | None:
    votes = Counter(
        section
        for line_id in row.line_ids
        if line_id in line_sections
        for section in line_sections[line_id]
    )
    if not votes:
        return None
    maximum = max(votes.values())
    leaders = sorted(
        label for label, count in votes.items() if count == maximum
    )
    return leaders[0] if len(leaders) == 1 else None


def _exclusions(path: Path | None) -> tuple[set[tuple[str, int]], str | None]:
    if path is None:
        return set(), None
    payload = json.loads(path.read_text(encoding="utf-8"))
    keys = {
        (str(item["image_id"]), int(item["receipt_id"])) for item in payload
    }
    canonical = json.dumps(
        sorted(keys), separators=(",", ":"), sort_keys=True
    ).encode()
    return keys, hashlib.sha256(canonical).hexdigest()


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


def build_model(
    client: Any,
    sections: list[Any],
    *,
    excluded_receipts: set[tuple[str, int]] | None = None,
    exclusion_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Learn a model after proving every requested holdout is excluded."""

    # Corpus construction is intentionally linear and auditable.
    # pylint: disable=too-many-locals,too-many-branches
    excluded_receipts = excluded_receipts or set()
    valid = [
        section
        for section in sections
        if section.validation_status == ValidationStatus.VALID.value
    ]
    valid_receipt_keys = {
        (section.image_id, section.receipt_id) for section in valid
    }
    missing_exclusions = excluded_receipts - valid_receipt_keys
    if missing_exclusions:
        raise ValueError(
            f"Exclusion manifest has {len(missing_exclusions)} keys without "
            "QA-VALID section evidence"
        )
    by_receipt: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for section in valid:
        key = (section.image_id, section.receipt_id)
        if key not in excluded_receipts:
            by_receipt[key].append(section)

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
        line_sections: dict[int, set[str]] = defaultdict(set)
        for section in receipt_sections:
            for line_id in section.line_ids:
                line_sections[line_id].add(str(section.section_type))
        receipt_labels = []
        for feature in features:
            label = _row_label(feature.row, line_sections)
            if label is not None:
                receipt_labels.append((feature, label))
            elif any(
                line_id in line_sections for line_id in feature.row.line_ids
            ):
                skipped["ambiguous_row_labels"] += 1
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
        "schema_version": 2,
        "model_source": "upload-determinism-v1",
        "source_environment": "dev",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "section_count": len(sections),
            "valid_section_count": len(valid),
            "source_receipt_count": len(valid_receipt_keys),
            "receipt_count": len(by_receipt),
            "trained_receipt_count": len(labeled),
            "excluded_receipt_count": len(excluded_receipts),
            "excluded_training_receipt_count": len(
                excluded_receipts & valid_receipt_keys
            ),
            "exclusion_manifest_sha256": exclusion_manifest_sha256,
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
    """Validate the dev-only configuration and write the learned artifact."""

    args = _arguments()
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if args.environment != "dev":
        raise SystemExit(
            "Refusing to read anything except the dev environment"
        )
    if table_name != DEV_TABLE or table_name == PROD_TABLE:
        raise SystemExit(
            f"Refusing table {table_name!r}; expected exact dev table "
            f"{DEV_TABLE!r}"
        )
    client = DynamoClient(table_name)
    sections = []
    cursor = None
    while True:
        page, cursor = client.list_receipt_sections(last_evaluated_key=cursor)
        sections.extend(page)
        if cursor is None:
            break
    excluded, exclusion_hash = _exclusions(args.exclude_manifest)
    model = build_model(
        client,
        sections,
        excluded_receipts=excluded,
        exclusion_manifest_sha256=exclusion_hash,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(model["source"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

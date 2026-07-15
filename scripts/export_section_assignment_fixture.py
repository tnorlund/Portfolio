#!/usr/bin/env python3
"""Export privacy-safe feature fixtures from QA-VALID dev receipts."""

# Sibling package paths precede runtime imports; the guard is deliberately
# duplicated in standalone read-only dev scripts.
# pylint: disable=wrong-import-position,duplicate-code

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _package in ("receipt_dynamo", "receipt_upload"):
    sys.path.insert(0, str(_REPO_ROOT / _package))

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_upload.section_assignment import (
    assign_feature_sections,
    extract_row_features,
    load_prior_model,
    normalize_merchant_key,
)

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"
_SAFE_TOKENS = {
    "__amount__",
    "__number__",
    "address",
    "aid",
    "amount",
    "approval",
    "approved",
    "auth",
    "balance",
    "card",
    "cash",
    "cashback",
    "change",
    "credit",
    "debit",
    "due",
    "each",
    "feedback",
    "footer",
    "items",
    "market",
    "mastercard",
    "payment",
    "purchase",
    "store",
    "subtotal",
    "survey",
    "tax",
    "thank",
    "total",
    "visa",
    "visit",
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        action="append",
        required=True,
        help="image_id:receipt_id:merchant name",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--table", default=os.environ.get("DYNAMODB_TABLE_NAME")
    )
    return parser.parse_args()


def _truth(sections) -> dict[int, str]:
    result: dict[int, str] = {}
    for section in sections:
        if section.validation_status != ValidationStatus.VALID.value:
            continue
        for row_id in section.row_ids or []:
            value = str(section.section_type)
            previous = result.get(row_id)
            if previous is not None and previous != value:
                raise ValueError(f"Conflicting QA label for row {row_id}")
            result[row_id] = value
    return result


def main() -> int:
    """Export normalized features and frozen predictions for named cases."""

    # The export stays linear so every privacy transformation is inspectable.
    # pylint: disable=too-many-locals
    args = _arguments()
    if args.table != DEV_TABLE or args.table == PROD_TABLE:
        raise SystemExit(
            f"Refusing table {args.table!r}; expected {DEV_TABLE!r}"
        )
    client = DynamoClient(args.table)
    model = load_prior_model()
    fixtures = []
    for specification in args.case:
        image_id, receipt_id_raw, merchant = specification.split(":", 2)
        receipt_id = int(receipt_id_raw)
        prefix = image_id[:8]
        rows = client.get_receipt_rows_from_receipt(image_id, receipt_id)
        lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
        sections = client.get_receipt_sections_from_receipt(
            image_id, receipt_id
        )
        expected = _truth(sections)
        global_prior = model["global"]
        merchant_prior = model.get("merchants", {}).get(
            normalize_merchant_key(merchant), global_prior
        )
        features = extract_row_features(rows, lines)
        predicted = {
            assignment.row.row_id: assignment.section_type
            for assignment in assign_feature_sections(
                features, model, merchant
            )
        }
        feature_rows = []
        for feature in features:
            row_id = feature.row.row_id
            feature_rows.append(
                {
                    "row_id": row_id,
                    "position": feature.position,
                    "x_span": feature.x_span,
                    "alpha_ratio": feature.alpha_ratio,
                    "has_amount": feature.has_amount,
                    "amount_density": feature.amount_density,
                    "has_quantity": feature.has_quantity,
                    "tokens": [
                        token
                        for token in feature.tokens
                        if token in _SAFE_TOKENS
                    ],
                    "token_evidence": {
                        section: sum(
                            float(token_log_odds[token])
                            for token in feature.tokens
                            if token in token_log_odds
                        )
                        for section in global_prior["sections"]
                        for section_model in [
                            merchant_prior["section_models"].get(
                                section,
                                global_prior["section_models"][section],
                            )
                        ]
                        for token_log_odds in [
                            section_model.get(
                                "token_log_odds",
                                global_prior["section_models"][section].get(
                                    "token_log_odds", {}
                                ),
                            )
                        ]
                    },
                    "expected": expected.get(row_id),
                    "predicted": predicted[row_id],
                }
            )
        source_hash = hashlib.sha256(
            json.dumps(
                feature_rows, separators=(",", ":"), sort_keys=True
            ).encode()
        ).hexdigest()
        qa_scored = len(expected)
        qa_matched = sum(
            predicted[row_id] == section_type
            for row_id, section_type in expected.items()
        )
        item_rows = {
            row_id
            for row_id, section_type in expected.items()
            if section_type == "ITEMS"
        }
        items_matched = sum(
            predicted[row_id] == "ITEMS" for row_id in item_rows
        )
        fixtures.append(
            {
                "case": prefix,
                "receipt_id": receipt_id,
                "merchant": merchant,
                "source_feature_sha256": source_hash,
                "qa": {
                    "matched": qa_matched,
                    "scored": qa_scored,
                    "items_matched": items_matched,
                    "items_scored": len(item_rows),
                },
                "rows": feature_rows,
            }
        )
    args.output.write_text(
        json.dumps(fixtures, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

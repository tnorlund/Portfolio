#!/usr/bin/env python3
"""Owner-gated cleanup of a FAILED (never-sealed) merchant-truth mint.

Removes the OPEN manifest + component rows of one version so a fixed
migration can re-mint it. Refuses SEALED versions unconditionally, refuses
the prod table unconditionally, preserves append-only AUDIT rows, and is a
dry run unless ``--delete`` is passed explicitly.

Recovery context: the G1 v1 mint wrote costco_wholesale v1 with legacy
AttributeValue-encoded payloads whose number forms DynamoDB normalized
(``0.0`` -> ``0``); those rows can never re-verify against their content
hashes, and mint_version is create-only, so they must be removed before the
fixed migration re-mints.
"""

from __future__ import annotations

import argparse

import boto3

from receipt_dynamo.migrations.merchant_truth_v1 import DEV_TABLE_NAME
from receipt_dynamo.migrations.merchant_truth_v1_live import (
    cleanup_unsealed_open_version,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument(
        "--table",
        "--table-name",
        dest="table_name",
        default=None,
        help=(
            f"DynamoDB table (default: exact dev table {DEV_TABLE_NAME}). "
            "Any other table must be passed explicitly; the prod table is "
            "refused unconditionally."
        ),
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--delete",
        action="store_true",
        help=(
            "Actually delete the OPEN rows (single conditional "
            "transaction). Without this flag the script only lists them."
        ),
    )
    args = parser.parse_args(argv)

    explicit_table = args.table_name is not None
    table_name = args.table_name or DEV_TABLE_NAME
    result = cleanup_unsealed_open_version(
        boto3.client("dynamodb", region_name=args.region),
        table_name=table_name,
        slug=args.slug,
        version=args.version,
        explicit_table=explicit_table,
        delete=args.delete,
    )
    for line in result.report_lines:
        print(line)
    if not result.found_keys:
        print("nothing to clean up")
    elif not result.deleted:
        print("dry run only — re-run with --delete to remove these rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

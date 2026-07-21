#!/usr/bin/env python3
"""Promote one sealed MerchantTruth bundle; never flip prod ACTIVE."""

from __future__ import annotations

import argparse

import boto3

from receipt_dynamo.migrations.merchant_truth_v1 import DEV_TABLE_NAME
from receipt_dynamo.promotions.merchant_truth import (
    PROD_TABLE_NAME,
    MerchantTruthPromoter,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("version", type=int)
    parser.add_argument("--dev-table", default=DEV_TABLE_NAME)
    parser.add_argument("--prod-table", default=PROD_TABLE_NAME)
    parser.add_argument("--dev-bucket", required=True)
    parser.add_argument("--prod-bucket", required=True)
    parser.add_argument("--expected-prod-account-id", required=True)
    parser.add_argument("--promoted-by", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Perform copy/import. Without this flag, verify and print "
            "plan only."
        ),
    )
    args = parser.parse_args()

    dynamodb = boto3.client("dynamodb", region_name=args.region)
    s3 = boto3.client("s3", region_name=args.region)
    account_id = boto3.client(
        "sts", region_name=args.region
    ).get_caller_identity()["Account"]
    promoter = MerchantTruthPromoter(
        dynamodb,
        dynamodb,
        s3,
        dev_table_name=args.dev_table,
        prod_table_name=args.prod_table,
        dev_bucket=args.dev_bucket,
        prod_bucket=args.prod_bucket,
        actual_account_id=account_id,
        expected_prod_account_id=args.expected_prod_account_id,
    )
    if args.execute:
        plan = promoter.promote(
            args.slug, args.version, promoted_by=args.promoted_by
        )
        print(
            f"Promoted {plan.slug} v{plan.version}; "
            "prod ACTIVE was not changed."
        )
    else:
        plan = promoter.plan(args.slug, args.version)
        print(
            f"DRY RUN: verified {plan.slug} v{plan.version}, "
            f"{len(plan.components)} components, {len(plan.assets)} S3 assets."
        )
        print("No DynamoDB or S3 writes were performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

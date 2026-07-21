"""Moto coverage for selected, additive MerchantTruth promotion."""

from __future__ import annotations

import hashlib
from typing import Any

import boto3
import pytest
from moto import mock_aws

from receipt_dynamo.data.shared_exceptions import (
    MerchantTruthConflictError,
    MerchantTruthPromotionError,
    MerchantTruthTableMismatchError,
)
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthComponent,
    MerchantTruthManifest,
    compute_bundle_hash,
)
from receipt_dynamo.migrations.merchant_truth_v1 import (
    ARTIFACT_BUCKET_ALIAS,
    DEV_TABLE_NAME,
)
from receipt_dynamo.promotions.merchant_truth import (
    PROD_TABLE_NAME,
    MerchantTruthPromoter,
)

pytestmark = pytest.mark.integration
SLUG = "sprouts_farmers_market"
DEV_BUCKET = "merchant-truth-dev-artifacts"
PROD_BUCKET = "merchant-truth-prod-artifacts"
ACCOUNT_ID = "123456789012"


def create_table(client: Any, table_name: str) -> None:
    client.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def bundle_items(
    asset_bytes: bytes,
    *,
    content_addressed: bool = True,
) -> tuple[list[dict[str, Any]], str, str]:
    digest = hashlib.sha256(asset_bytes).hexdigest()
    token = digest[:8] if content_addressed else "mutable"
    asset_key = f"merchant_fonts/sprouts/regular-{token}.npz"
    payloads: dict[str, Any] = {
        "identity": {"merchant_name": "Sprouts Farmers Market"},
        "typography": {"condense": 0.9},
        "stylemap": {"available": False},
        "layout": {"available": False},
        "assets": {
            "fonts": {
                "regular": {
                    "bucket_alias": ARTIFACT_BUCKET_ALIAS,
                    "s3_key": asset_key,
                    "content_hash": digest,
                }
            }
        },
        "flags": {"compose": True},
        "catalog_snapshot": {
            "items": [],
            "item_count": 0,
            "catalog_hash": hashlib.sha256(b"[]").hexdigest(),
        },
    }
    components = [
        MerchantTruthComponent(
            slug=SLUG,
            version=1,
            name=name,
            payload=payloads[name],
            provenance={"source_kind": "migration"},
        )
        for name in sorted(COMPONENT_NAMES)
    ]
    hashes = {item.name: item.content_hash for item in components}
    bundle_hash = compute_bundle_hash(hashes)
    manifest = MerchantTruthManifest(
        slug=SLUG,
        version=1,
        component_hashes=hashes,
        bundle_hash=bundle_hash,
        status="SEALED",
        provenance={"written_by": "migration"},
        mint_run_id="run-1",
        gate_status="PASS",
    )
    return (
        [manifest.to_item(), *[item.to_item() for item in components]],
        asset_key,
        bundle_hash,
    )


def promoter(
    dynamodb: Any,
    s3: Any,
    *,
    account_id: str = ACCOUNT_ID,
) -> MerchantTruthPromoter:
    return MerchantTruthPromoter(
        dynamodb,
        dynamodb,
        s3,
        dev_table_name=DEV_TABLE_NAME,
        prod_table_name=PROD_TABLE_NAME,
        dev_bucket=DEV_BUCKET,
        prod_bucket=PROD_BUCKET,
        actual_account_id=account_id,
        expected_prod_account_id=ACCOUNT_ID,
    )


def setup_source(
    dynamodb: Any,
    s3: Any,
    items: list[dict[str, Any]],
    asset_key: str,
    asset_bytes: bytes,
) -> None:
    create_table(dynamodb, DEV_TABLE_NAME)
    create_table(dynamodb, PROD_TABLE_NAME)
    s3.create_bucket(Bucket=DEV_BUCKET)
    s3.create_bucket(Bucket=PROD_BUCKET)
    for item in items:
        dynamodb.put_item(TableName=DEV_TABLE_NAME, Item=item)
    s3.put_object(Bucket=DEV_BUCKET, Key=asset_key, Body=asset_bytes)


def test_promotion_copies_verifies_imports_and_never_writes_active() -> None:
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        asset_bytes = b"font-atlas"
        items, asset_key, bundle_hash = bundle_items(asset_bytes)
        setup_source(dynamodb, s3, items, asset_key, asset_bytes)

        plan = promoter(dynamodb, s3).promote(
            SLUG,
            1,
            promoted_at="2026-07-20T17:00:00Z",
            promoted_by="owner",
        )

        assert plan.manifest.bundle_hash == bundle_hash
        copied = s3.get_object(Bucket=PROD_BUCKET, Key=asset_key)[
            "Body"
        ].read()
        assert copied == asset_bytes
        response = dynamodb.query(
            TableName=PROD_TABLE_NAME,
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": {"S": f"MERCHANT_TRUTH#{SLUG}"}},
        )
        assert len(response["Items"]) == 9
        assert all(
            item["SK"]["S"] != "TRUTH#ACTIVE" for item in response["Items"]
        )
        assert (
            sum(
                item["TYPE"]["S"] == "MERCHANT_TRUTH_AUDIT"
                for item in response["Items"]
            )
            == 1
        )


def test_exact_existing_prod_bundle_is_idempotent_without_second_audit() -> (
    None
):
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        asset_bytes = b"font-atlas"
        items, asset_key, _ = bundle_items(asset_bytes)
        setup_source(dynamodb, s3, items, asset_key, asset_bytes)
        service = promoter(dynamodb, s3)
        service.promote(
            SLUG,
            1,
            promoted_at="2026-07-20T17:00:00Z",
            promoted_by="owner",
        )

        service.promote(
            SLUG,
            1,
            promoted_at="2026-07-20T17:01:00Z",
            promoted_by="owner",
        )

        response = dynamodb.query(
            TableName=PROD_TABLE_NAME,
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": {"S": f"MERCHANT_TRUTH#{SLUG}"}},
        )
        assert len(response["Items"]) == 9


def test_existing_different_prod_version_fails_instead_of_renumbering() -> (
    None
):
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        asset_bytes = b"font-atlas"
        items, asset_key, _ = bundle_items(asset_bytes)
        setup_source(dynamodb, s3, items, asset_key, asset_bytes)
        conflicting = dict(items[0])
        conflicting["bundle_hash"] = {"S": "0" * 64}
        dynamodb.put_item(TableName=PROD_TABLE_NAME, Item=conflicting)

        with pytest.raises(MerchantTruthConflictError, match="different"):
            promoter(dynamodb, s3).promote(
                SLUG,
                1,
                promoted_at="2026-07-20T17:00:00Z",
                promoted_by="owner",
            )


def test_source_hash_mismatch_fails_before_any_prod_write() -> None:
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        items, asset_key, _ = bundle_items(b"expected")
        setup_source(dynamodb, s3, items, asset_key, b"corrupt")

        with pytest.raises(MerchantTruthPromotionError, match="hash mismatch"):
            promoter(dynamodb, s3).promote(SLUG, 1, promoted_by="owner")

        assert dynamodb.scan(TableName=PROD_TABLE_NAME)["Count"] == 0


def test_non_content_addressed_key_and_wrong_account_fail_closed() -> None:
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        items, asset_key, _ = bundle_items(
            b"font-atlas", content_addressed=False
        )
        setup_source(dynamodb, s3, items, asset_key, b"font-atlas")

        with pytest.raises(
            MerchantTruthPromotionError, match="not content-addressed"
        ):
            promoter(dynamodb, s3).plan(SLUG, 1)
        with pytest.raises(MerchantTruthTableMismatchError, match="account"):
            promoter(dynamodb, s3, account_id="000000000000")

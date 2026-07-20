"""Selected-bundle MerchantTruth promotion, separate from image reconcile."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable

from botocore.exceptions import ClientError

from receipt_dynamo.data._merchant_truth import CREATE_ONLY
from receipt_dynamo.data.shared_exceptions import (
    MerchantTruthConflictError,
    MerchantTruthIntegrityError,
    MerchantTruthPromotionError,
    MerchantTruthTableMismatchError,
)
from receipt_dynamo.entities.merchant_truth import (
    MerchantTruthAudit,
    MerchantTruthComponent,
    MerchantTruthManifest,
    merchant_truth_pk,
    version_prefix,
)
from receipt_dynamo.merchant_truth_loader import (
    verify_merchant_truth_bundle_items,
)
from receipt_dynamo.migrations.merchant_truth_v1 import (
    ARTIFACT_BUCKET_ALIAS,
    DEV_TABLE_NAME,
)

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_s3 import S3Client

PROD_TABLE_NAME = "ReceiptsTable-d7ff76a"


@dataclass(frozen=True)
class AssetPointer:
    """Environment-neutral content-addressed S3 dependency."""

    key: str
    content_hash: str
    bucket_alias: str


@dataclass(frozen=True)
class MerchantTruthPromotionPlan:
    """Verified closure and assets selected for one promotion."""

    slug: str
    version: int
    manifest: MerchantTruthManifest
    components: list[MerchantTruthComponent]
    source_items: list[dict[str, Any]]
    assets: tuple[AssetPointer, ...]


class MerchantTruthPromoter:
    """Copy one SEALED/PASS bundle without ever moving prod ACTIVE."""

    def __init__(
        self,
        dev_dynamodb: "DynamoDBClient",
        prod_dynamodb: "DynamoDBClient",
        s3_client: "S3Client",
        *,
        dev_table_name: str,
        prod_table_name: str,
        dev_bucket: str,
        prod_bucket: str,
        actual_account_id: str,
        expected_prod_account_id: str,
    ) -> None:
        if dev_table_name != DEV_TABLE_NAME:
            raise MerchantTruthTableMismatchError(
                f"expected exact dev table {DEV_TABLE_NAME!r}, "
                f"got {dev_table_name!r}"
            )
        if prod_table_name != PROD_TABLE_NAME:
            raise MerchantTruthTableMismatchError(
                f"expected exact prod table {PROD_TABLE_NAME!r}, "
                f"got {prod_table_name!r}"
            )
        if (
            not expected_prod_account_id
            or actual_account_id != expected_prod_account_id
        ):
            raise MerchantTruthTableMismatchError(
                "AWS account does not match the explicitly expected "
                "prod account"
            )
        if not dev_bucket or not prod_bucket or dev_bucket == prod_bucket:
            raise MerchantTruthPromotionError(
                "promotion requires distinct non-empty dev/prod buckets"
            )
        self._dev_dynamodb = dev_dynamodb
        self._prod_dynamodb = prod_dynamodb
        self._s3 = s3_client
        self._dev_table = dev_table_name
        self._prod_table = prod_table_name
        self._dev_bucket = dev_bucket
        self._prod_bucket = prod_bucket
        self._account_id = actual_account_id

    def plan(self, slug: str, version: int) -> MerchantTruthPromotionPlan:
        """Read and verify the selected dev closure without making writes."""
        items = self._query_bundle(
            self._dev_dynamodb, self._dev_table, slug, version
        )
        manifests = [
            MerchantTruthManifest.from_item(item)
            for item in items
            if item.get("TYPE", {}).get("S") == "MERCHANT_TRUTH_MANIFEST"
        ]
        if len(manifests) != 1:
            raise MerchantTruthIntegrityError(
                "selected dev bundle must contain exactly one manifest"
            )
        manifest, components = verify_merchant_truth_bundle_items(
            items,
            slug=slug,
            version=version,
            expected_bundle_hash=manifests[0].bundle_hash,
        )
        assets = tuple(self._discover_assets(components))
        for asset in assets:
            self._read_verified_object(self._dev_bucket, asset)
        return MerchantTruthPromotionPlan(
            slug=slug,
            version=version,
            manifest=manifest,
            components=components,
            source_items=items,
            assets=assets,
        )

    def promote(
        self,
        slug: str,
        version: int,
        *,
        promoted_at: str | None = None,
        promoted_by: str,
    ) -> MerchantTruthPromotionPlan:
        """Copy assets, import the immutable closure, verify, and stop."""
        plan = self.plan(slug, version)
        for asset in plan.assets:
            self._copy_and_verify_asset(asset)
        timestamp = promoted_at or datetime.now(timezone.utc).isoformat()
        audit = self._promotion_audit(plan, timestamp, promoted_by)
        actions = [
            self._put_action(item)
            for item in [*plan.source_items, audit.to_item()]
        ]
        if len(actions) != 9:
            raise MerchantTruthPromotionError(
                "normal promotion requires exactly 9 writes, got "
                f"{len(actions)}"
            )
        try:
            self._prod_dynamodb.transact_write_items(
                TransactItems=actions,  # type: ignore[arg-type]
                ClientRequestToken=self._request_token(plan, audit),
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {
                "ConditionalCheckFailedException",
                "TransactionCanceledException",
            }:
                raise
            self._assert_idempotent_existing_bundle(plan, error)

        prod_items = self._query_bundle(
            self._prod_dynamodb, self._prod_table, slug, version
        )
        verify_merchant_truth_bundle_items(
            prod_items,
            slug=slug,
            version=version,
            expected_bundle_hash=plan.manifest.bundle_hash,
        )
        if self._get_prod_active(slug) is not None:
            # Promotion never writes ACTIVE. If an ACTIVE exists it must have
            # predated this operation; its presence is not modified here.
            pass
        for asset in plan.assets:
            self._read_verified_object(self._prod_bucket, asset)
        return plan

    def _put_action(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("SK", {}).get("S") == "TRUTH#ACTIVE":
            raise MerchantTruthPromotionError(
                "promotion must never import ACTIVE"
            )
        return {
            "Put": {
                "TableName": self._prod_table,
                "Item": item,
                "ConditionExpression": CREATE_ONLY,
            }
        }

    def _promotion_audit(
        self,
        plan: MerchantTruthPromotionPlan,
        timestamp: str,
        promoted_by: str,
    ) -> MerchantTruthAudit:
        seed = (
            f"{plan.slug}:{plan.version}:{plan.manifest.bundle_hash}:"
            f"{timestamp}:{promoted_by}:{self._account_id}"
        )
        return MerchantTruthAudit(
            slug=plan.slug,
            created_at=timestamp,
            audit_id=hashlib.sha256(seed.encode("utf-8")).hexdigest()[:26],
            action="PROMOTE",
            version=plan.version,
            bundle_hash=plan.manifest.bundle_hash,
            details={
                "promoted_by": promoted_by,
                "source_table": self._dev_table,
                "destination_table": self._prod_table,
                "source_bucket": self._dev_bucket,
                "destination_bucket": self._prod_bucket,
                "account_id": self._account_id,
            },
        )

    @staticmethod
    def _request_token(
        plan: MerchantTruthPromotionPlan, audit: MerchantTruthAudit
    ) -> str:
        seed = (
            f"promote:{plan.slug}:{plan.version}:{plan.manifest.bundle_hash}:"
            f"{audit.created_at}:{audit.audit_id}"
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:36]

    def _copy_and_verify_asset(self, asset: AssetPointer) -> None:
        content = self._read_verified_object(self._dev_bucket, asset)
        try:
            existing = self._read_verified_object(self._prod_bucket, asset)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {
                "NoSuchKey",
                "404",
            }:
                raise
        else:
            if existing != content:
                raise MerchantTruthPromotionError(
                    f"prod content-addressed key collision: {asset.key}"
                )
            return
        self._s3.copy_object(
            CopySource={"Bucket": self._dev_bucket, "Key": asset.key},
            Bucket=self._prod_bucket,
            Key=asset.key,
            MetadataDirective="COPY",
        )
        copied = self._read_verified_object(self._prod_bucket, asset)
        if copied != content:
            raise MerchantTruthPromotionError(
                f"post-copy bytes differ for {asset.key}"
            )

    def _read_verified_object(self, bucket: str, asset: AssetPointer) -> bytes:
        response = self._s3.get_object(Bucket=bucket, Key=asset.key)
        content = response["Body"].read()
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != asset.content_hash:
            raise MerchantTruthPromotionError(
                f"S3 hash mismatch for s3://{bucket}/{asset.key}: "
                f"expected {asset.content_hash}, got {actual_hash}"
            )
        return content

    @staticmethod
    def _discover_assets(
        components: list[MerchantTruthComponent],
    ) -> Iterable[AssetPointer]:
        discovered: dict[tuple[str, str], AssetPointer] = {}

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                if {"bucket_alias", "s3_key", "content_hash"}.issubset(value):
                    pointer = AssetPointer(
                        key=value["s3_key"],
                        content_hash=value["content_hash"],
                        bucket_alias=value["bucket_alias"],
                    )
                    if pointer.bucket_alias != ARTIFACT_BUCKET_ALIAS:
                        raise MerchantTruthPromotionError(
                            f"unknown artifact bucket alias: "
                            f"{pointer.bucket_alias}"
                        )
                    if pointer.content_hash[:8] not in pointer.key:
                        raise MerchantTruthPromotionError(
                            "asset key is not content-addressed: "
                            f"{pointer.key}"
                        )
                    identity = (pointer.key, pointer.content_hash)
                    discovered[identity] = pointer
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        for component in components:
            visit(component.payload)
        return [discovered[key] for key in sorted(discovered)]

    def _assert_idempotent_existing_bundle(
        self, plan: MerchantTruthPromotionPlan, error: ClientError
    ) -> None:
        existing = self._query_bundle(
            self._prod_dynamodb,
            self._prod_table,
            plan.slug,
            plan.version,
        )
        try:
            verify_merchant_truth_bundle_items(
                existing,
                slug=plan.slug,
                version=plan.version,
                expected_bundle_hash=plan.manifest.bundle_hash,
            )
        except (ValueError, MerchantTruthIntegrityError) as verify_error:
            raise MerchantTruthConflictError(
                "prod already contains a different or incomplete version"
            ) from verify_error
        if self._canonical_items(existing) != self._canonical_items(
            plan.source_items
        ):
            raise MerchantTruthConflictError(
                "prod version has matching payload hashes but "
                "different records"
            ) from error

    @staticmethod
    def _canonical_items(items: list[dict[str, Any]]) -> list[str]:
        return sorted(
            json.dumps(
                item, sort_keys=True, separators=(",", ":"), default=str
            )
            for item in items
        )

    @staticmethod
    def _query_bundle(
        client: "DynamoDBClient",
        table_name: str,
        slug: str,
        version: int,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        exclusive_start_key = None
        while True:
            request: dict[str, Any] = {
                "TableName": table_name,
                "KeyConditionExpression": (
                    "PK = :pk AND begins_with(SK, :sk)"
                ),
                "ExpressionAttributeValues": {
                    ":pk": {"S": merchant_truth_pk(slug)},
                    ":sk": {"S": f"{version_prefix(version)}#"},
                },
                "ConsistentRead": True,
            }
            if exclusive_start_key is not None:
                request["ExclusiveStartKey"] = exclusive_start_key
            response = client.query(**request)
            items.extend(response.get("Items", []))
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                return items

    def _get_prod_active(self, slug: str) -> dict[str, Any] | None:
        response = self._prod_dynamodb.get_item(
            TableName=self._prod_table,
            Key={
                "PK": {"S": merchant_truth_pk(slug)},
                "SK": {"S": "TRUTH#ACTIVE"},
            },
            ConsistentRead=True,
        )
        return response.get("Item")

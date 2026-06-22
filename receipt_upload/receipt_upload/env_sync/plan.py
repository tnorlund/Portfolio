"""Read-only planner for the cross-environment record migration.

Identifies the genuinely-new records to copy from ``src`` to ``dst`` (a receipt
whose exact ``(image_id, receipt_id)`` key is absent in the target) and the S3
objects they reference. Receipts whose key already exists in the target are
skipped — a different-sha copy there is a cosmetic re-crop, not missing data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from receipt_dynamo import DynamoClient

from receipt_upload.dedup._ddb import paginate

# Per-env buckets that differ; the dev<->prod counterpart is the mapped value.
# ``upload-images-image-bucket-4bcea7e`` is SHARED by both envs and is omitted
# (its objects need no copy).
BUCKET_MAP = {
    "raw-image-bucket-c779c32": "raw-image-bucket-0facc78",
    "raw-image-bucket-0facc78": "raw-image-bucket-c779c32",
    "sitebucket-ad92f1f": "sitebucket-778abc9",
    "sitebucket-778abc9": "sitebucket-ad92f1f",
}
SHARED_BUCKETS = {"upload-images-image-bucket-4bcea7e"}

_CDN_FIELDS = [
    "cdn_s3_key", "cdn_webp_s3_key", "cdn_avif_s3_key",
    "cdn_thumbnail_s3_key", "cdn_thumbnail_webp_s3_key",
    "cdn_thumbnail_avif_s3_key", "cdn_small_s3_key", "cdn_small_webp_s3_key",
    "cdn_small_avif_s3_key", "cdn_medium_s3_key", "cdn_medium_webp_s3_key",
    "cdn_medium_avif_s3_key",
]


def remap_bucket(bucket: str) -> str:
    """The counterpart bucket in the other env (shared/unknown -> unchanged)."""
    return BUCKET_MAP.get(bucket, bucket)


def _entity_s3_refs(entity) -> List[Tuple[str, str]]:
    out = []
    rb, rk = getattr(entity, "raw_s3_bucket", None), getattr(
        entity, "raw_s3_key", None
    )
    if rb and rk:
        out.append((rb, rk))
    cb = getattr(entity, "cdn_s3_bucket", None)
    for f in _CDN_FIELDS:
        k = getattr(entity, f, None)
        if cb and k:
            out.append((cb, k))
    return out


@dataclass
class MigrationPlan:
    src_env: str
    dst_env: str
    new_images: List[str] = field(default_factory=list)  # full-partition copy
    new_receipts: List[Tuple[str, int]] = field(
        default_factory=list
    )  # new receipt on an image already in dst
    dynamo_items: List[dict] = field(default_factory=list)  # raw items to put
    s3_copies: List[Tuple[str, str, str, str]] = field(
        default_factory=list
    )  # (src_bucket, src_key, dst_bucket, dst_key)
    s3_shared_skipped: int = 0

    def summary(self) -> dict:
        return {
            "src": self.src_env,
            "dst": self.dst_env,
            "new_images": len(self.new_images),
            "new_receipts_on_shared_images": len(self.new_receipts),
            "dynamo_items_to_copy": len(self.dynamo_items),
            "s3_objects_to_copy": len(self.s3_copies),
            "s3_objects_shared_no_copy": self.s3_shared_skipped,
        }


def _subtree_items(dynamo, image_id: str, receipt_id: int) -> List[dict]:
    """Raw items under one receipt (padded/unpadded rid), within IMAGE#."""
    padded, unpadded = f"{receipt_id:05d}", str(receipt_id)
    out = []
    for it in paginate(
        dynamo,
        TableName=dynamo.table_name,
        KeyConditionExpression="#pk = :pk AND begins_with(#sk, :sk)",
        ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
        ExpressionAttributeValues={
            ":pk": {"S": f"IMAGE#{image_id}"}, ":sk": {"S": "RECEIPT#"}
        },
    ):
        parts = it["SK"]["S"].split("#")
        if len(parts) >= 2 and parts[1] in (padded, unpadded):
            out.append(it)
    return out


def _partition_items(dynamo, image_id: str) -> List[dict]:
    return list(
        paginate(
            dynamo,
            TableName=dynamo.table_name,
            KeyConditionExpression="#pk = :pk",
            ExpressionAttributeNames={"#pk": "PK"},
            ExpressionAttributeValues={":pk": {"S": f"IMAGE#{image_id}"}},
        )
    )


def build_plan(src_env: str, dst_env: str, env_table: Dict[str, str]) -> MigrationPlan:
    """Enumerate the genuinely-new records + S3 objects to copy src -> dst."""
    src = DynamoClient(env_table[src_env])
    dst = DynamoClient(env_table[dst_env])

    src_receipts = src.list_receipts()[0]
    src_images = {im.image_id: im for im in src.list_images()[0]}
    dst_keys = {
        (r.image_id, r.receipt_id) for r in dst.list_receipts()[0]
    }
    dst_image_ids = {im.image_id for im in dst.list_images()[0]}

    plan = MigrationPlan(src_env=src_env, dst_env=dst_env)
    new_image_ids = set()
    new_receipt_keys = []
    for r in src_receipts:
        if (r.image_id, r.receipt_id) in dst_keys:
            continue  # already present (cosmetic re-crop diffs are skipped)
        if r.image_id not in dst_image_ids:
            new_image_ids.add(r.image_id)
        else:
            new_receipt_keys.append((r.image_id, r.receipt_id))

    s3_refs: List[Tuple[str, str]] = []

    # new images: copy the whole partition + the image's own S3 objects
    for image_id in sorted(new_image_ids):
        plan.new_images.append(image_id)
        plan.dynamo_items.extend(_partition_items(src, image_id))
        if image_id in src_images:
            s3_refs.extend(_entity_s3_refs(src_images[image_id]))

    # new receipts on a shared image: copy just the receipt subtree
    new_rkeys = set(new_receipt_keys)
    for key in new_receipt_keys:
        plan.new_receipts.append(key)
        plan.dynamo_items.extend(_subtree_items(src, key[0], key[1]))

    # S3 crops for every receipt being copied (on new images or new keys)
    for r in src_receipts:
        if r.image_id in new_image_ids or (
            r.image_id, r.receipt_id
        ) in new_rkeys:
            s3_refs.extend(_entity_s3_refs(r))

    seen = set()
    for bucket, key in s3_refs:
        if (bucket, key) in seen:
            continue
        seen.add((bucket, key))
        if bucket in SHARED_BUCKETS:
            plan.s3_shared_skipped += 1
        else:
            plan.s3_copies.append((bucket, key, remap_bucket(bucket), key))
    return plan

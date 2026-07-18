"""Plan and apply safe, line-first receipt re-segmentation.

Concurrency and durability model
---------------------------------
The persisted plan document in S3 is the single source of truth for a
re-segmentation's lifecycle. Every mutation of the head plan object is a
conditional write (``If-Match`` on the last-read ETag, or ``If-None-Match:*``
for creates); a lost precondition surfaces a "plan changed underneath you"
error instead of silently clobbering a concurrent writer.

``apply_plan`` treats the ``PLANNED`` -> ``COMMITTING`` transition as an
execution lock: it is a compare-and-swap on the plan ETag performed *before*
any DynamoDB row is reserved, deleted, or committed. A second concurrent apply
of the same plan loses that swap and bails out before it can touch data, which
(together with the guarded ``release_receipt_id_reservations`` and the
``_commit_landed`` recovery) closes the concurrent-apply data-loss window.

Residual race (documented, not eliminated): the child rows that become the
output receipts are snapshotted into ``outputs`` early in apply. The source
fingerprint is re-verified immediately before the commit transaction to shrink
the window, but an external writer that edits a source word or label *after*
that final check and *before* the commit lands would have its edit dropped
from the committed outputs. Eliminating this fully requires the commit
transaction to guard each source child row's snapshot, which the current
single-parent commit condition does not do.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Mapping

import boto3
from botocore.exceptions import ClientError
from PIL import Image as PILImage

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class PlanPreconditionError(RuntimeError):
    """A conditional plan write lost to a concurrent writer.

    Raised when an ``If-Match`` / ``If-None-Match`` precondition fails, i.e.
    the stored plan object changed (or already exists) since it was read.
    """


# S3 returns PreconditionFailed for a failed If-Match/If-None-Match; some
# stacks report the 409 ConditionalRequestConflict for racing writers.
_PRECONDITION_CODES = {"PreconditionFailed", "ConditionalRequestConflict"}

PLAN_PREFIX = "resegment-plans"
SUPPORTED_SOURCE_TYPES = {
    "COMPACTION_RUN",
    "RECEIPT",
    "RECEIPT_LETTER",
    "RECEIPT_LINE",
    "RECEIPT_PLACE",
    "RECEIPT_WORD",
    "RECEIPT_WORD_LABEL",
}


_PLAN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _validate_plan_id(plan_id: str) -> str:
    """Reject plan IDs that could escape the plan S3 key namespace."""
    if not _PLAN_ID_PATTERN.fullmatch(plan_id):
        raise ValueError(
            "plan_id must be 1-64 URL-safe characters and start with a "
            "letter or number"
        )
    return plan_id


def _plan_key(plan_id: str) -> str:
    return f"{PLAN_PREFIX}/{_validate_plan_id(plan_id)}.json"


def _revision_key(plan_id: str, revision: int) -> str:
    return (
        f"{PLAN_PREFIX}/{_validate_plan_id(plan_id)}/revisions/" f"{revision:04d}.json"
    )


def _artifact_prefix(plan_id: str, revision: int) -> str:
    return (
        f"{PLAN_PREFIX}/{_validate_plan_id(plan_id)}/revisions/"
        f"{revision:04d}/preview"
    )


def _png_bytes(image: PILImage.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _jpeg_bytes(image: PILImage.Image) -> bytes:
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=82, optimize=True)
    return output.getvalue()


def _artifact_record(
    *,
    s3_client: "S3Client",
    bucket: str,
    key: str,
    image: PILImage.Image,
    kind: str,
    mime_type: str = "image/png",
) -> dict[str, Any]:
    body = _jpeg_bytes(image) if mime_type == "image/jpeg" else _png_bytes(image)
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=mime_type,
    )
    return {
        "kind": kind,
        "s3_key": key,
        "mime_type": mime_type,
        "sha256": hashlib.sha256(body).hexdigest(),
        "byte_size": len(body),
        "width": image.width,
        "height": image.height,
    }


def _put_plan_json(
    s3_client: "S3Client",
    bucket: str,
    key: str,
    payload: Mapping[str, Any],
    *,
    if_match: str | None = None,
    if_none_match: bool = False,
) -> str:
    """Write a plan document, optionally guarded by an S3 precondition.

    Returns the new object ETag (unquoted). Raises ``PlanPreconditionError``
    when a supplied precondition fails so callers can report a clear
    "plan changed underneath you" error instead of overwriting a racing
    writer.
    """
    kwargs: dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "Body": json.dumps(payload, sort_keys=True, default=str).encode(
            "utf-8"
        ),
        "ContentType": "application/json",
    }
    if if_match is not None:
        kwargs["IfMatch"] = if_match
    if if_none_match:
        kwargs["IfNoneMatch"] = "*"
    try:
        response = s3_client.put_object(**kwargs)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        status = error.response.get("ResponseMetadata", {}).get(
            "HTTPStatusCode"
        )
        if code in _PRECONDITION_CODES or status in (409, 412):
            raise PlanPreconditionError(
                "The plan changed underneath this write; retrieve the latest "
                "plan and try again"
            ) from error
        raise
    return str(response.get("ETag", "")).strip('"')


def _save_plan(
    s3_client: "S3Client",
    bucket: str,
    plan: dict,
    *,
    if_match: str | None = None,
    if_none_match: bool = False,
) -> str:
    return _put_plan_json(
        s3_client,
        bucket,
        _plan_key(plan["plan_id"]),
        plan,
        if_match=if_match,
        if_none_match=if_none_match,
    )


def _save_revision(
    s3_client: "S3Client",
    bucket: str,
    plan: dict,
    *,
    if_none_match: bool = False,
) -> str:
    # Revision keys are immutable and unique per revision number, so a
    # revision write is always a create guarded by If-None-Match:* to stop
    # two concurrent revisions from clobbering the same revision object.
    return _put_plan_json(
        s3_client,
        bucket,
        _revision_key(plan["plan_id"], int(plan.get("revision", 1))),
        plan,
        if_none_match=if_none_match,
    )


def _load_plan(
    s3_client: "S3Client", bucket: str, plan_id: str
) -> dict[str, Any]:
    plan, _ = _load_plan_with_etag(s3_client, bucket, plan_id)
    return plan


def _load_plan_with_etag(
    s3_client: "S3Client", bucket: str, plan_id: str
) -> tuple[dict[str, Any], str]:
    response = s3_client.get_object(Bucket=bucket, Key=_plan_key(plan_id))
    etag = str(response.get("ETag", "")).strip('"')
    return json.loads(response["Body"].read()), etag


def _load_revision(
    s3_client: "S3Client", bucket: str, plan_id: str, revision: int
) -> dict[str, Any]:
    response = s3_client.get_object(Bucket=bucket, Key=_revision_key(plan_id, revision))
    return json.loads(response["Body"].read())


def _word_ref_set(segment: dict[str, Any]) -> set[tuple[int, int]]:
    return {(int(ref["line_id"]), int(ref["word_id"])) for ref in segment["word_refs"]}


def _combined_words_by_ref(
    combined_words: list[dict[str, Any]], source_receipt_id: int
) -> dict[tuple[int, int], dict[str, Any]]:
    return {
        (int(word["line_id"]), int(word["word_id"])): word
        for word in combined_words
        if int(word["receipt_id"]) == source_receipt_id
    }


def _download_original_image(
    s3_client: "S3Client", image_entity: Any
) -> PILImage.Image:
    image, _ = _download_original_image_with_identity(s3_client, image_entity)
    return image


def _download_original_image_with_identity(
    s3_client: "S3Client", image_entity: Any
) -> tuple[PILImage.Image, dict[str, Any]]:
    response = s3_client.get_object(
        Bucket=image_entity.raw_s3_bucket, Key=image_entity.raw_s3_key
    )
    body = response["Body"].read()
    identity = {
        "bucket": image_entity.raw_s3_bucket,
        "key": image_entity.raw_s3_key,
        "version_id": response.get("VersionId"),
        "etag": str(response.get("ETag", "")).strip('"') or None,
        "content_length": len(body),
        "content_sha256": hashlib.sha256(body).hexdigest(),
    }
    return PILImage.open(io.BytesIO(body)).convert("RGB"), identity


def _segment_geometry(
    segment_words: list[dict[str, Any]],
    image_width: int,
    image_height: int,
    padding_px: float,
) -> dict[str, Any]:
    from receipt_upload.combine import calculate_min_area_rect

    geometry = calculate_min_area_rect(
        segment_words,
        image_width,
        image_height,
        padding_px=padding_px,
    )

    return {
        "bounds": geometry["bounds"],
        "src_corners": [list(point) for point in geometry["src_corners"]],
        "warped_width": geometry["warped_width"],
        "warped_height": geometry["warped_height"],
        "requested_padding_px": padding_px,
        "applied_padding_px": padding_px,
    }


def _plan_preview_url(s3_client: "S3Client", bucket: str, key: str) -> str:
    return s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=3600,
    )


def _preview_delivery(
    s3_client: "S3Client", bucket: str, plan: Mapping[str, Any]
) -> dict[str, Any]:
    manifest = plan.get("visualizations") or {}
    if not manifest:
        return {
            segment["segment_key"]: _plan_preview_url(
                s3_client, bucket, segment["preview_s3_key"]
            )
            for segment in plan.get("segments", ())
            if segment.get("preview_s3_key")
        }
    delivery: dict[str, Any] = {
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "segments": {},
    }
    for name in ("overlay", "contact_sheet"):
        artifact = manifest.get(name)
        if artifact:
            delivery[name] = _plan_preview_url(s3_client, bucket, artifact["s3_key"])
    for segment_key, artifacts in manifest.get("segments", {}).items():
        delivery["segments"][segment_key] = {
            name: _plan_preview_url(s3_client, bucket, artifact["s3_key"])
            for name, artifact in artifacts.items()
            if isinstance(artifact, Mapping) and artifact.get("s3_key")
        }
    return delivery


def create_plan(
    event: dict[str, Any],
    *,
    dynamo_client: Any,
    s3_client: "S3Client",
    raw_bucket: str,
    plan_id: str | None = None,
    revision: int = 1,
    supersedes_plan_hash: str | None = None,
    expected_source_fingerprint: str | None = None,
    head_if_match: str | None = None,
) -> dict[str, Any]:
    """Create and optionally persist a visual, immutable split plan.

    ``head_if_match`` guards the head-plan write: pass the ETag read from the
    existing head when superseding it with a new revision (revise), or leave
    it ``None`` for a brand-new plan (the head is then written with
    ``If-None-Match:*`` so it cannot clobber an existing plan_id).
    """
    from receipt_upload.combine import (
        combine_receipt_words_to_image_coords,
        create_warped_receipt_image,
    )
    from receipt_upload.resegment import (
        build_preview_bundle,
        build_source_fingerprint,
        compute_plan_hash,
        normalize_line_resegmentation_plan,
        normalize_resegmentation_plan,
    )

    image_id = str(event["image_id"])
    source_receipt_id = int(event["source_receipt_id"])
    schema_version = int(
        event.get("schema_version", 2 if event.get("assignments") else 1)
    )
    if schema_version not in {1, 2}:
        raise ValueError("schema_version must be 1 or 2")
    if schema_version == 2 and not event.get("assignments"):
        raise ValueError("schema_version=2 requires line-first assignments")
    if schema_version == 1 and event.get("assignments"):
        raise ValueError("assignments require schema_version=2")

    visualization = dict(event.get("visualization") or {})
    padding_px = float(visualization.get("padding_px", event.get("padding_px", 12.0)))
    if not math.isfinite(padding_px) or not 0 <= padding_px <= 256:
        raise ValueError("padding_px must be a finite number between 0 and 256")
    persist_plan = bool(event.get("persist_plan", True))
    if event.get("rerun_ocr", False):
        raise ValueError("rerun_ocr is not supported in the first release")

    details = dynamo_client.get_receipt_details(image_id, source_receipt_id)
    image_entity = dynamo_client.get_image(image_id)
    image_type = str(image_entity.image_type)
    letters = (
        dynamo_client.list_receipt_letters_from_receipt(image_id, source_receipt_id)
        if schema_version == 2
        else []
    )
    type_counts = dynamo_client.get_receipt_item_type_counts(
        image_id, source_receipt_id
    )
    unsupported = sorted(set(type_counts) - SUPPORTED_SOURCE_TYPES)
    if unsupported:
        raise ValueError(
            "This receipt has unsupported dependent entity types: "
            f"{unsupported}. Add an explicit migration policy before splitting."
        )
    if details.barcodes:
        raise ValueError("Receipt barcodes need an explicit segment assignment policy")

    if schema_version == 2:
        normalized = normalize_line_resegmentation_plan(
            lines=details.lines,
            words=details.words,
            letters=letters,
            labels=details.labels,
            segments=event["segments"],
            assignments=event["assignments"],
        )
    else:
        normalized = normalize_resegmentation_plan(
            words=details.words,
            labels=details.labels,
            segments=event["segments"],
            discard_line_ids=event.get("discard_line_ids", ()),
            discard_word_refs=event.get("discard_word_refs", ()),
            discard_reason=event.get("discard_reason"),
            allow_labeled_discard=bool(event.get("allow_labeled_discard", False)),
        )

    original_image = None
    source_object = None
    if schema_version == 2 or persist_plan:
        original_image, source_object = _download_original_image_with_identity(
            s3_client, image_entity
        )
    source_fingerprint = build_source_fingerprint(
        receipt=details.receipt,
        words=details.words,
        labels=details.labels,
        place=details.place,
        image=image_entity if schema_version == 2 else None,
        lines=details.lines if schema_version == 2 else (),
        letters=letters,
        # Bind the source image object identity for v1 plans too (M4): a v1
        # fingerprint that omitted it could not detect a same-key overwrite
        # of the underlying image between plan and apply.
        source_object=source_object,
        source_type_counts=type_counts,
    )
    if (
        expected_source_fingerprint is not None
        and source_fingerprint != expected_source_fingerprint
    ):
        raise ValueError("The source receipt changed; create a new plan")
    all_receipts = dynamo_client.get_receipts_from_image(image_id)
    next_receipt_id = (
        max((receipt.receipt_id for receipt in all_receipts), default=0) + 1
    )

    combined_words = combine_receipt_words_to_image_coords(
        dynamo_client,
        image_id,
        [source_receipt_id],
        image_entity.width,
        image_entity.height,
        deduplicate=False,
    )
    words_by_ref = _combined_words_by_ref(combined_words, source_receipt_id)
    if len(words_by_ref) != normalized["totals"]["source_words"]:
        raise ValueError("Not every source word could be transformed to image space")

    plan_id = plan_id or str(uuid.uuid4())
    plan_segments = []
    for offset, segment in enumerate(normalized["segments"]):
        refs = _word_ref_set(segment)
        segment_words = [words_by_ref[ref] for ref in sorted(refs)]
        geometry = _segment_geometry(
            segment_words,
            image_entity.width,
            image_entity.height,
            padding_px,
        )
        plan_segment = {
            **segment,
            "output_receipt_id": next_receipt_id + offset,
            "geometry": geometry,
        }
        plan_segments.append(plan_segment)

    requested_strategy = str(visualization.get("strategy", "AUTO")).upper()
    if requested_strategy not in {"AUTO", "RECTANGULAR", "LAYERED_MULTI_REGION"}:
        raise ValueError(
            "visualization.strategy must be AUTO, RECTANGULAR, or "
            "LAYERED_MULTI_REGION"
        )
    if requested_strategy == "AUTO":
        layered_evidence = schema_version == 2 and (
            normalized["totals"].get("mixed_lines", 0) > 0
            or any(
                segment.get("visible_regions")
                or segment.get("occluded_by")
                or int(segment.get("z_index", 0)) != 0
                for segment in plan_segments
            )
        )
        effective_strategy = (
            "LAYERED_MULTI_REGION"
            if image_type == "PHOTO" and layered_evidence
            else "RECTANGULAR"
        )
    else:
        effective_strategy = requested_strategy
    if image_type == "NATIVE" and effective_strategy == "LAYERED_MULTI_REGION":
        raise ValueError("NATIVE images do not support layered re-segmentation")
    if (
        image_type == "SCAN"
        and effective_strategy == "LAYERED_MULTI_REGION"
        and not visualization.get("confirm_stacked_scan", False)
    ):
        raise ValueError("SCAN layered segmentation requires confirm_stacked_scan=true")
    if effective_strategy == "RECTANGULAR" and any(
        segment.get("visible_regions") for segment in plan_segments
    ):
        raise ValueError(
            "visible_regions require the LAYERED_MULTI_REGION strategy; a "
            "RECTANGULAR apply writes the min-area crop and would ignore "
            "them, so the preview would not match the applied output"
        )

    plan = {
        "version": schema_version,
        "schema_version": schema_version,
        "plan_id": plan_id,
        "revision": revision,
        "supersedes_plan_hash": supersedes_plan_hash,
        "revision_reason": event.get("revision_reason"),
        "status": "PLANNED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "image_id": image_id,
        "source_receipt_id": source_receipt_id,
        "image": {
            "entity_type": "IMAGE",
            "image_id": image_id,
            "image_type": image_type,
            "width": image_entity.width,
            "height": image_entity.height,
        },
        "source_object": source_object if schema_version == 2 else None,
        "source_fingerprint": source_fingerprint,
        "source_type_counts": type_counts,
        "padding_px": padding_px,
        "visualization": {
            "requested_strategy": requested_strategy,
            "effective_strategy": effective_strategy,
            "padding_px": padding_px,
            "confirm_stacked_scan": bool(
                visualization.get("confirm_stacked_scan", False)
            ),
        },
        "segments": plan_segments,
        "assignments": normalized.get("assignments"),
        "discard": normalized["discard"],
        "totals": normalized["totals"],
    }

    preview_urls: dict[str, Any] = {}
    if persist_plan:
        if original_image is None:
            original_image = _download_original_image(s3_client, image_entity)
        if schema_version == 2:
            discard_refs = {
                (int(ref["line_id"]), int(ref["word_id"]))
                for ref in normalized["discard"]["word_refs"]
            }
            bundle = build_preview_bundle(
                original_image,
                image_type=image_type,
                strategy=effective_strategy,
                lines=details.lines,
                words_by_ref=words_by_ref,
                segments=plan_segments,
                discard_refs=discard_refs,
                letters=letters,
                padding_px=int(round(padding_px)),
            )
            prefix = _artifact_prefix(plan_id, revision)
            visualizations: dict[str, Any] = {
                "schema_version": 1,
                "source": {"entity_type": "IMAGE", "image_type": image_type},
                "coordinate_space": {
                    "space": "SOURCE_IMAGE_PIXELS",
                    "origin": "TOP_LEFT",
                    "width": image_entity.width,
                    "height": image_entity.height,
                },
                "segments": {},
            }
            visualizations["overlay"] = _artifact_record(
                s3_client=s3_client,
                bucket=raw_bucket,
                key=f"{prefix}/overlay.png",
                image=bundle["images"]["overlay"],
                kind="ASSIGNMENT_OVERLAY",
            )
            visualizations["contact_sheet"] = _artifact_record(
                s3_client=s3_client,
                bucket=raw_bucket,
                key=f"{prefix}/contact-sheet.jpg",
                image=bundle["images"]["contact_sheet"],
                kind="MCP_CONTACT_SHEET",
                mime_type="image/jpeg",
            )
            for segment_key, images in bundle["images"]["segments"].items():
                visualizations["segments"][segment_key] = {
                    "mask": _artifact_record(
                        s3_client=s3_client,
                        bucket=raw_bucket,
                        key=f"{prefix}/segments/{segment_key}/mask.png",
                        image=images["mask"],
                        kind="VISIBLE_MASK",
                    ),
                    "visible_crop": _artifact_record(
                        s3_client=s3_client,
                        bucket=raw_bucket,
                        key=f"{prefix}/segments/{segment_key}/visible.png",
                        image=images["visible_crop"],
                        kind="MASKED_RGBA_CROP",
                    ),
                }
            plan["visualizations"] = visualizations
            plan["metrics"] = bundle["metrics"]
            plan["findings"] = bundle["findings"]
            plan["evidence"] = bundle["evidence"]
        else:
            for segment, plan_segment in zip(normalized["segments"], plan_segments):
                geometry = plan_segment["geometry"]
                preview = create_warped_receipt_image(
                    original_image,
                    [tuple(point) for point in geometry["src_corners"]],
                    geometry["warped_width"],
                    geometry["warped_height"],
                )
                preview_key = (
                    f"{PLAN_PREFIX}/{plan_id}/" f"{segment['segment_key']}.png"
                )
                s3_client.put_object(
                    Bucket=raw_bucket,
                    Key=preview_key,
                    Body=_png_bytes(preview),
                    ContentType="image/png",
                )
                plan_segment["preview_s3_key"] = preview_key

    plan["plan_hash"] = compute_plan_hash(plan)
    if persist_plan:
        _save_revision(s3_client, raw_bucket, plan, if_none_match=True)
        if head_if_match is None:
            _save_plan(s3_client, raw_bucket, plan, if_none_match=True)
        else:
            _save_plan(s3_client, raw_bucket, plan, if_match=head_if_match)
        preview_urls = _preview_delivery(s3_client, raw_bucket, plan)

    return {
        **plan,
        "applicable": not any(
            finding.get("severity") == "BLOCKER" for finding in plan.get("findings", ())
        ),
        "preview_urls": preview_urls,
    }


def get_plan(
    event: dict[str, Any],
    *,
    s3_client: "S3Client",
    raw_bucket: str,
) -> dict[str, Any]:
    """Retrieve a plan revision and refresh all expiring preview URLs."""
    plan_id = str(event["plan_id"])
    head = _load_plan(s3_client, raw_bucket, plan_id)
    requested_revision = event.get("revision")
    if requested_revision is None or int(requested_revision) == int(
        head.get("revision", 1)
    ):
        plan = head
    else:
        plan = _load_revision(s3_client, raw_bucket, plan_id, int(requested_revision))
    blockers = [
        finding
        for finding in plan.get("findings", ())
        if finding.get("severity") == "BLOCKER"
    ]
    return {
        **plan,
        "is_latest": int(plan.get("revision", 1)) == int(head.get("revision", 1)),
        "applicable": not blockers
        and int(plan.get("revision", 1)) == int(head.get("revision", 1))
        and plan.get("status") == "PLANNED",
        "preview_urls": _preview_delivery(s3_client, raw_bucket, plan),
    }


def revise_plan(
    event: dict[str, Any],
    *,
    dynamo_client: Any,
    s3_client: "S3Client",
    raw_bucket: str,
) -> dict[str, Any]:
    """Create an immutable replacement revision with optimistic concurrency."""
    plan_id = str(event["plan_id"])
    head, head_etag = _load_plan_with_etag(s3_client, raw_bucket, plan_id)
    if head.get("status") != "PLANNED":
        raise ValueError("Only a PLANNED re-segmentation can be revised")
    if int(event["base_revision"]) != int(head.get("revision", 1)):
        raise ValueError("base_revision is stale; retrieve the latest plan")
    if str(event["base_plan_hash"]) != str(head["plan_hash"]):
        raise ValueError("base_plan_hash is stale; retrieve the latest plan")
    if int(head.get("schema_version", head.get("version", 1))) != 2:
        raise ValueError("Only schema_version=2 plans support revision")

    previous_visualization = head.get("visualization", {})
    replacement_event = {
        "schema_version": 2,
        "image_id": head["image_id"],
        "source_receipt_id": head["source_receipt_id"],
        "segments": event["segments"],
        "assignments": event["assignments"],
        "visualization": event.get("visualization")
        or {
            "strategy": previous_visualization.get("requested_strategy", "AUTO"),
            "padding_px": previous_visualization.get("padding_px", 12),
            "confirm_stacked_scan": previous_visualization.get(
                "confirm_stacked_scan", False
            ),
        },
        "revision_reason": event["revision_reason"],
    }
    return create_plan(
        replacement_event,
        dynamo_client=dynamo_client,
        s3_client=s3_client,
        raw_bucket=raw_bucket,
        plan_id=plan_id,
        revision=int(head.get("revision", 1)) + 1,
        supersedes_plan_hash=head["plan_hash"],
        expected_source_fingerprint=head["source_fingerprint"],
        head_if_match=head_etag,
    )


def _migrate_labels(
    source_labels: list[Any],
    source_receipt_id: int,
    output_receipt_id: int,
    line_id_map: dict,
    word_id_map: dict,
) -> list[Any]:
    from receipt_dynamo.entities import ReceiptWordLabel

    migrated = []
    for label in source_labels:
        source_key = (label.word_id, label.line_id, source_receipt_id)
        new_word_id = word_id_map.get(source_key)
        new_line_id = line_id_map.get((label.line_id, source_receipt_id))
        if new_word_id is None or new_line_id is None:
            continue
        migrated.append(
            ReceiptWordLabel(
                image_id=label.image_id,
                receipt_id=output_receipt_id,
                line_id=new_line_id,
                word_id=new_word_id,
                label=label.label,
                reasoning=label.reasoning,
                timestamp_added=label.timestamp_added,
                validation_status=label.validation_status,
                label_proposed_by=label.label_proposed_by,
                label_consolidated_from=label.label_consolidated_from,
            )
        )
    return migrated


def _set_cdn_fields(receipt: Any, cdn_keys: dict[str, str]) -> None:
    mappings = {
        "cdn_s3_key": "jpeg",
        "cdn_webp_s3_key": "webp",
        "cdn_avif_s3_key": "avif",
        "cdn_thumbnail_s3_key": "jpeg_thumbnail",
        "cdn_thumbnail_webp_s3_key": "webp_thumbnail",
        "cdn_thumbnail_avif_s3_key": "avif_thumbnail",
        "cdn_small_s3_key": "jpeg_small",
        "cdn_small_webp_s3_key": "webp_small",
        "cdn_small_avif_s3_key": "avif_small",
        "cdn_medium_s3_key": "jpeg_medium",
        "cdn_medium_webp_s3_key": "webp_medium",
        "cdn_medium_avif_s3_key": "avif_medium",
    }
    for attribute, key in mappings.items():
        setattr(receipt, attribute, cdn_keys.get(key))


def _expected_cdn_keys(base_key: str) -> list[str]:
    return [
        f"{base_key}{suffix}.{extension}"
        for suffix in ("_thumbnail", "_small", "_medium", "")
        for extension in ("jpg", "webp", "avif")
    ]


def _build_outputs(
    *,
    plan: dict[str, Any],
    details: Any,
    image_entity: Any,
    original_image: PILImage.Image,
    combined_words: list[dict[str, Any]],
    dynamo_client: Any,
    raw_bucket: str,
    site_bucket: str,
) -> list[dict[str, Any]]:
    from receipt_upload.combine import (
        combine_receipt_letters_to_image_coords,
        create_combined_receipt_records,
        create_receipt_letters_from_combined,
        create_warped_receipt_image,
    )
    from receipt_upload.utils import calculate_sha256_from_bytes

    source_receipt_id = int(plan["source_receipt_id"])
    words_by_ref = _combined_words_by_ref(combined_words, source_receipt_id)
    outputs = []
    for segment in plan["segments"]:
        output_receipt_id = int(segment["output_receipt_id"])
        segment_words = [words_by_ref[ref] for ref in sorted(_word_ref_set(segment))]
        geometry = _segment_geometry(
            segment_words,
            image_entity.width,
            image_entity.height,
            float(plan["padding_px"]),
        )
        if geometry != segment["geometry"]:
            raise ValueError(f"Geometry changed for segment {segment['segment_key']}")
        src_corners = [tuple(point) for point in geometry["src_corners"]]
        warped_image = create_warped_receipt_image(
            original_image,
            src_corners,
            geometry["warped_width"],
            geometry["warped_height"],
        )
        records = create_combined_receipt_records(
            image_id=plan["image_id"],
            new_receipt_id=output_receipt_id,
            combined_words=segment_words,
            bounds=geometry["bounds"],
            raw_bucket=raw_bucket,
            site_bucket=site_bucket,
            image_width=image_entity.width,
            image_height=image_entity.height,
            warped_width=geometry["warped_width"],
            warped_height=geometry["warped_height"],
            src_corners=src_corners,
        )
        combined_letters = combine_receipt_letters_to_image_coords(
            dynamo_client,
            plan["image_id"],
            [source_receipt_id],
            image_entity.width,
            image_entity.height,
            records["word_id_map"],
            records["line_id_map"],
        )
        letters = create_receipt_letters_from_combined(
            combined_letters=combined_letters,
            new_receipt_id=output_receipt_id,
            image_id=plan["image_id"],
            receipt_width=geometry["warped_width"],
            receipt_height=geometry["warped_height"],
            image_height=image_entity.height,
            warped_height=geometry["warped_height"],
            src_corners=src_corners,
            warped_width=geometry["warped_width"],
        )
        labels = _migrate_labels(
            details.labels,
            source_receipt_id,
            output_receipt_id,
            records["line_id_map"],
            records["word_id_map"],
        )
        place = None
        if segment.get("place_policy", "none") == "inherit" and details.place:
            place = copy.deepcopy(details.place)
            place.receipt_id = output_receipt_id
        elif segment.get("place_policy") not in {"inherit", "none"}:
            raise ValueError(f"Unsupported place_policy: {segment.get('place_policy')}")

        raw_key = (
            f"receipts/{plan['image_id']}/"
            f"{plan['image_id']}_RECEIPT_{output_receipt_id:05d}.png"
        )
        receipt = records["receipt"]
        receipt.raw_s3_key = raw_key
        receipt.sha256 = calculate_sha256_from_bytes(_png_bytes(warped_image))
        outputs.append(
            {
                "segment_key": segment["segment_key"],
                "image": warped_image,
                "receipt": receipt,
                "lines": records["receipt_lines"],
                "words": records["receipt_words"],
                "letters": letters,
                "labels": labels,
                "place": place,
                "raw_key": raw_key,
            }
        )
    return outputs


def _stage_outputs(
    *,
    outputs: list[dict[str, Any]],
    dynamo_client: Any,
    raw_bucket: str,
    site_bucket: str,
    uploaded_keys: list[str],
) -> None:
    from receipt_upload.utils import upload_all_cdn_formats, upload_png_to_s3

    for output in outputs:
        uploaded_keys.append(output["raw_key"])
        upload_png_to_s3(output["image"], raw_bucket, output["raw_key"])
        receipt_id = output["receipt"].receipt_id
        cdn_base_key = f"assets/{output['receipt'].image_id}/{receipt_id}"
        # Record every possible CDN key before the multi-format uploader runs,
        # so a mid-upload exception can still roll back partial objects.
        uploaded_keys.extend(_expected_cdn_keys(cdn_base_key))
        cdn_keys = upload_all_cdn_formats(
            output["image"],
            site_bucket,
            cdn_base_key,
            generate_thumbnails=True,
        )
        _set_cdn_fields(output["receipt"], cdn_keys)

        dynamo_client.add_receipt_lines(output["lines"])
        dynamo_client.add_receipt_words(output["words"])
        if output["letters"]:
            dynamo_client.add_receipt_letters(output["letters"])
        if output["labels"]:
            dynamo_client.add_receipt_word_labels(output["labels"])
        if output["place"]:
            dynamo_client.add_receipt_place(output["place"])


def _embed_outputs(
    *,
    outputs: list[dict[str, Any]],
    dynamo_client: Any,
    chromadb_bucket: str,
    wait_for_embeddings: bool,
) -> list[str]:
    from receipt_chroma.embedding.orchestration import (
        EmbeddingConfig,
        create_embeddings_and_compaction_run,
    )

    run_ids = []
    for output in outputs:
        receipt = output["receipt"]
        config = EmbeddingConfig(
            image_id=receipt.image_id,
            receipt_id=receipt.receipt_id,
            chromadb_bucket=chromadb_bucket,
            dynamo_client=dynamo_client,
            receipt_place=output["place"],
            receipt_word_labels=output["labels"] or None,
        )
        result = create_embeddings_and_compaction_run(
            receipt_lines=output["lines"],
            receipt_words=[word for word in output["words"] if not word.is_noise]
            or output["words"],
            config=config,
        )
        try:
            run_ids.append(result.compaction_run.run_id)
            if wait_for_embeddings and not result.wait_for_compaction_to_finish(
                dynamo_client, max_wait_seconds=300
            ):
                raise RuntimeError(
                    f"Embedding compaction failed for receipt {receipt.receipt_id}"
                )
        finally:
            result.close()
    return run_ids


def _delete_uploaded_objects(
    s3_client: "S3Client",
    raw_bucket: str,
    site_bucket: str,
    keys: list[str],
) -> None:
    for key in keys:
        bucket = site_bucket if key.startswith("assets/") else raw_bucket
        try:
            s3_client.delete_object(Bucket=bucket, Key=key)
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to delete staged s3://%s/%s", bucket, key)


def _finish_cleanup(
    *, plan: dict[str, Any], dynamo_client: Any, image_entity: Any
) -> dict[str, Any]:
    deleted_items = dynamo_client.delete_receipt_items(
        plan["image_id"], int(plan["source_receipt_id"]), include_parent=True
    )
    output_ids = [int(segment["output_receipt_id"]) for segment in plan["segments"]]
    remaining_receipts = dynamo_client.get_receipts_from_image(plan["image_id"])
    visible_ids = {receipt.receipt_id for receipt in remaining_receipts}
    if not set(output_ids).issubset(visible_ids):
        raise RuntimeError("Committed output receipts are not visible")
    image_entity.receipt_count = len(remaining_receipts)
    dynamo_client.update_image(image_entity)
    return {
        "image_id": plan["image_id"],
        "source_receipt_id": plan["source_receipt_id"],
        "output_receipt_ids": output_ids,
        "deleted_source_items": deleted_items,
    }


def _commit_landed(
    dynamo_client: Any, plan: dict[str, Any], output_ids: list[int]
) -> bool:
    """Return True when the commit transaction actually took effect.

    The commit atomically deletes the source parent and activates every
    output, so it landed exactly when the source is gone and each output
    parses as a real Receipt (a reservation row raises OperationError).
    """
    from receipt_dynamo.data.shared_exceptions import (
        EntityNotFoundError,
        OperationError,
    )

    try:
        dynamo_client.get_receipt(plan["image_id"], int(plan["source_receipt_id"]))
        return False
    except EntityNotFoundError:
        pass
    except OperationError:
        return False
    for output_id in output_ids:
        try:
            dynamo_client.get_receipt(plan["image_id"], output_id)
        except (EntityNotFoundError, OperationError):
            return False
    return True


def _current_source_fingerprint(
    *,
    dynamo_client: Any,
    plan: dict[str, Any],
    image_entity: Any,
    source_object: dict[str, Any] | None,
    schema_version: int,
) -> str:
    """Recompute the source fingerprint from the live source rows.

    Used both for the up-front plan/apply consistency check and for the
    re-verification immediately before the commit transaction (M2).
    """
    from receipt_upload.resegment import build_source_fingerprint

    image_id = plan["image_id"]
    source_receipt_id = int(plan["source_receipt_id"])
    details = dynamo_client.get_receipt_details(image_id, source_receipt_id)
    type_counts = dynamo_client.get_receipt_item_type_counts(
        image_id, source_receipt_id
    )
    letters = (
        dynamo_client.list_receipt_letters_from_receipt(
            image_id, source_receipt_id
        )
        if schema_version == 2
        else []
    )
    return build_source_fingerprint(
        receipt=details.receipt,
        words=details.words,
        labels=details.labels,
        place=details.place,
        image=image_entity if schema_version == 2 else None,
        lines=details.lines if schema_version == 2 else (),
        letters=letters,
        source_object=source_object,
        source_type_counts=type_counts,
    )


def apply_plan(
    event: dict[str, Any],
    *,
    dynamo_client: Any,
    s3_client: "S3Client",
    raw_bucket: str,
    site_bucket: str,
    chromadb_bucket: str,
) -> dict[str, Any]:
    """Apply a persisted plan with staging, guarded commit, and cleanup."""
    from receipt_dynamo.data.shared_exceptions import (
        EntityNotFoundError,
        OperationError,
    )
    from receipt_upload.combine import combine_receipt_words_to_image_coords
    from receipt_upload.resegment import (
        build_source_fingerprint,
        compute_plan_hash,
    )

    plan, plan_etag = _load_plan_with_etag(
        s3_client, raw_bucket, str(event["plan_id"])
    )
    if event.get("plan_hash") != plan["plan_hash"]:
        raise ValueError("plan_hash does not match the stored plan")
    if compute_plan_hash(plan) != plan["plan_hash"]:
        raise ValueError("Stored plan contents failed integrity validation")
    if plan["status"] == "APPLIED":
        return {"status": "APPLIED", **plan["result"]}
    blockers = [
        finding.get("code", "UNKNOWN_BLOCKER")
        for finding in plan.get("findings", ())
        if finding.get("severity") == "BLOCKER"
    ]
    if blockers:
        raise ValueError(
            "The reviewed plan has blocking findings and cannot be applied: "
            f"{sorted(blockers)}"
        )
    if (
        plan.get("visualization", {}).get("effective_strategy")
        == "LAYERED_MULTI_REGION"
    ):
        raise ValueError(
            "LAYERED_MULTI_REGION plans cannot be applied until masked output "
            "rendering is enabled"
        )
    # Defense-in-depth (M3): a RECTANGULAR apply writes the min-area crop and
    # ignores visible_regions, so a stored plan that pairs them would apply an
    # artifact its preview never showed. create_plan already rejects this, but
    # re-checking here keeps a hand-edited or legacy plan from diverging.
    if plan.get("visualization", {}).get(
        "effective_strategy"
    ) != "LAYERED_MULTI_REGION" and any(
        segment.get("visible_regions") for segment in plan["segments"]
    ):
        raise ValueError(
            "visible_regions require the LAYERED_MULTI_REGION strategy; this "
            "plan pairs them with a RECTANGULAR apply and cannot be applied"
        )

    image_entity = dynamo_client.get_image(plan["image_id"])
    output_ids = [int(segment["output_receipt_id"]) for segment in plan["segments"]]

    if plan["status"] in {"COMMITTING", "CLEANUP_PENDING"}:
        try:
            dynamo_client.get_receipt(plan["image_id"], int(plan["source_receipt_id"]))
            source_exists = True
        except EntityNotFoundError:
            source_exists = False
        visible_output_ids = set()
        for output_id in output_ids:
            try:
                dynamo_client.get_receipt(plan["image_id"], output_id)
                visible_output_ids.add(output_id)
            except EntityNotFoundError:
                pass
            except OperationError:
                # A row that exists but cannot parse as a Receipt is a
                # leftover RESEGMENT_RESERVATION, so the output is not
                # visible yet.
                pass
        if not source_exists and visible_output_ids == set(output_ids):
            result = _finish_cleanup(
                plan=plan,
                dynamo_client=dynamo_client,
                image_entity=image_entity,
            )
            plan["status"] = "APPLIED"
            plan["result"] = result
            _save_plan(s3_client, raw_bucket, plan, if_match=plan_etag)
            return {"status": "APPLIED", **result}
        if source_exists:
            # Claim the recovery exclusively before touching DynamoDB: the
            # conditional reset is a compare-and-swap, so a second worker that
            # also loaded this COMMITTING plan loses here and bails before it
            # could release the winning worker's freshly-reserved rows.
            plan["status"] = "PLANNED"
            try:
                plan_etag = _save_plan(
                    s3_client, raw_bucket, plan, if_match=plan_etag
                )
            except PlanPreconditionError as error:
                raise ValueError(
                    "Another apply of this plan is already recovering it; "
                    "retrieve the latest plan and retry"
                ) from error
            dynamo_client.release_receipt_id_reservations(
                plan["image_id"], output_ids, plan["plan_id"]
            )

    details = dynamo_client.get_receipt_details(
        plan["image_id"], int(plan["source_receipt_id"])
    )
    current_type_counts = dynamo_client.get_receipt_item_type_counts(
        plan["image_id"], int(plan["source_receipt_id"])
    )
    if current_type_counts != plan["source_type_counts"]:
        raise ValueError("The source receipt dependents changed; create a new plan")
    schema_version = int(plan.get("schema_version", plan.get("version", 1)))
    current_letters = (
        dynamo_client.list_receipt_letters_from_receipt(
            plan["image_id"], int(plan["source_receipt_id"])
        )
        if schema_version == 2
        else []
    )
    original_image, current_source_object = _download_original_image_with_identity(
        s3_client, image_entity
    )
    current_fingerprint = build_source_fingerprint(
        receipt=details.receipt,
        words=details.words,
        labels=details.labels,
        place=details.place,
        image=image_entity if schema_version == 2 else None,
        lines=details.lines if schema_version == 2 else (),
        letters=current_letters,
        # Bind the source image object identity for v1 plans too (M4).
        source_object=current_source_object,
        source_type_counts=current_type_counts,
    )
    if current_fingerprint != plan["source_fingerprint"]:
        raise ValueError("The source receipt changed; create a new plan")

    combined_words = combine_receipt_words_to_image_coords(
        dynamo_client,
        plan["image_id"],
        [int(plan["source_receipt_id"])],
        image_entity.width,
        image_entity.height,
        deduplicate=False,
    )
    outputs = _build_outputs(
        plan=plan,
        details=details,
        image_entity=image_entity,
        original_image=original_image,
        combined_words=combined_words,
        dynamo_client=dynamo_client,
        raw_bucket=raw_bucket,
        site_bucket=site_bucket,
    )
    migrated_label_count = sum(len(output["labels"]) for output in outputs)
    if migrated_label_count != plan["totals"]["preserved_labels"]:
        raise RuntimeError("Label preservation invariant failed before staging")

    # Execution lock (C2): transition PLANNED -> COMMITTING with a conditional
    # S3 write BEFORE any destructive DynamoDB operation. This compare-and-swap
    # is the single serialization point for a plan's apply: a second concurrent
    # apply that also read the PLANNED head loses the swap and bails here,
    # so it can never reserve IDs, delete staged child rows, race the commit,
    # destroy the winner's committed outputs.
    plan["status"] = "COMMITTING"
    try:
        plan_etag = _save_plan(s3_client, raw_bucket, plan, if_match=plan_etag)
    except PlanPreconditionError as error:
        raise ValueError(
            "Another apply of this plan is already in progress; retry after "
            "it finishes"
        ) from error

    uploaded_keys: list[str] = []
    committed = False
    try:
        dynamo_client.reserve_receipt_ids(
            plan["image_id"], output_ids, plan["plan_id"]
        )
        for output_id in output_ids:
            dynamo_client.delete_receipt_items(
                plan["image_id"], output_id, include_parent=False
            )
        _stage_outputs(
            outputs=outputs,
            dynamo_client=dynamo_client,
            raw_bucket=raw_bucket,
            site_bucket=site_bucket,
            uploaded_keys=uploaded_keys,
        )
        run_ids = []
        if event.get("create_embeddings", True):
            run_ids = _embed_outputs(
                outputs=outputs,
                dynamo_client=dynamo_client,
                chromadb_bucket=chromadb_bucket,
                wait_for_embeddings=bool(event.get("wait_for_embeddings", True)),
            )

        # Re-verify the source fingerprint immediately before the commit (M2):
        # staging and embedding above widen the window in which an external
        # writer could edit a source word or label after the initial check.
        # This shrinks that window to the commit transaction itself; the
        # residual child-snapshot race is documented in the module docstring.
        if (
            _current_source_fingerprint(
                dynamo_client=dynamo_client,
                plan=plan,
                image_entity=image_entity,
                source_object=current_source_object,
                schema_version=schema_version,
            )
            != plan["source_fingerprint"]
        ):
            raise ValueError("The source receipt changed; create a new plan")

        try:
            dynamo_client.commit_receipt_resegmentation(
                details.receipt,
                [output["receipt"] for output in outputs],
                plan["plan_id"],
            )
        except Exception:
            # A commit exception is not proof the transaction failed: the
            # response can be lost after DynamoDB committed, and a
            # concurrent apply of the same plan may have won the race.
            # Rolling back in either case would destroy committed data.
            if not _commit_landed(dynamo_client, plan, output_ids):
                raise
            logger.warning(
                "Commit for plan %s raised but the transaction landed; "
                "continuing to cleanup",
                plan["plan_id"],
            )
        committed = True
        try:
            result = _finish_cleanup(
                plan=plan,
                dynamo_client=dynamo_client,
                image_entity=image_entity,
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception("Re-segmentation committed; cleanup is pending")
            plan["status"] = "CLEANUP_PENDING"
            # The data is already durably committed; the status write is
            # bookkeeping, so a lost precondition is logged but not fatal.
            try:
                plan_etag = _save_plan(
                    s3_client, raw_bucket, plan, if_match=plan_etag
                )
            except PlanPreconditionError:
                logger.exception(
                    "Failed to persist CLEANUP_PENDING for plan %s",
                    plan["plan_id"],
                )
            return {
                "status": "CLEANUP_PENDING",
                "plan_id": plan["plan_id"],
                "output_receipt_ids": output_ids,
            }

        result["compaction_run_ids"] = run_ids
        plan["status"] = "APPLIED"
        plan["result"] = result
        try:
            plan_etag = _save_plan(
                s3_client, raw_bucket, plan, if_match=plan_etag
            )
        except PlanPreconditionError:
            logger.exception(
                "Failed to persist APPLIED status for plan %s",
                plan["plan_id"],
            )
        return {"status": "APPLIED", **result}
    except Exception:
        if not committed:
            if plan["status"] == "COMMITTING":
                # The COMMITTING lock was acquired before staging; revert it so
                # a retry re-runs the full plan validation instead of the
                # resume path. The conditional write keeps this worker from
                # stamping over a status another worker has since advanced.
                plan["status"] = "PLANNED"
                try:
                    plan_etag = _save_plan(
                        s3_client, raw_bucket, plan, if_match=plan_etag
                    )
                except Exception:  # pylint: disable=broad-except
                    logger.exception(
                        "Failed to revert plan %s to PLANNED", plan["plan_id"]
                    )
            try:
                dynamo_client.release_receipt_id_reservations(
                    plan["image_id"], output_ids, plan["plan_id"]
                )
            finally:
                _delete_uploaded_objects(
                    s3_client,
                    raw_bucket,
                    site_bucket,
                    uploaded_keys,
                )
        raise


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Dispatch plan or apply operations."""
    del context
    try:
        mode = event.get("mode")
        if mode not in {"plan", "get", "revise", "apply"}:
            return {
                "status": "error",
                "error": "mode must be plan, get, revise, or apply",
            }

        table_name = os.environ["DYNAMODB_TABLE_NAME"]
        raw_bucket = os.environ["RAW_BUCKET"]
        site_bucket = os.environ["SITE_BUCKET"]
        chromadb_bucket = os.environ["CHROMADB_BUCKET"]

        from receipt_dynamo import DynamoClient

        dynamo_client = DynamoClient(table_name=table_name)
        s3_client = boto3.client("s3")
        if mode == "plan":
            return create_plan(
                event,
                dynamo_client=dynamo_client,
                s3_client=s3_client,
                raw_bucket=raw_bucket,
            )
        if mode == "get":
            return get_plan(
                {
                    "plan_id": event.get("plan_id"),
                    "revision": event.get("revision"),
                },
                s3_client=s3_client,
                raw_bucket=raw_bucket,
            )
        if mode == "revise":
            return revise_plan(
                {
                    "plan_id": event.get("plan_id"),
                    "base_revision": event.get("base_revision"),
                    "base_plan_hash": event.get("base_plan_hash"),
                    "segments": event.get("segments"),
                    "assignments": event.get("assignments"),
                    "visualization": event.get("visualization"),
                    "revision_reason": event.get("revision_reason"),
                },
                dynamo_client=dynamo_client,
                s3_client=s3_client,
                raw_bucket=raw_bucket,
            )
        return apply_plan(
            {
                "plan_id": event.get("plan_id"),
                "plan_hash": event.get("plan_hash"),
            },
            dynamo_client=dynamo_client,
            s3_client=s3_client,
            raw_bucket=raw_bucket,
            site_bucket=site_bucket,
            chromadb_bucket=chromadb_bucket,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Receipt re-segmentation failed")
        return {"status": "error", "error": str(exc)}

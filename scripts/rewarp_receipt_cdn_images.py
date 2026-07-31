#!/usr/bin/env python3
"""Detect and repair receipt CDN images that disagree with their entity.

A published CDN crop is supposed to be the receipt warped upright. When
the served asset was built from different geometry than the Receipt row
now carries -- a stale crop left behind after re-segmentation, two
receipts' assets swapped, or a crop stored rotated -- the rendered image
no longer lines up with the OCR coordinate frame every downstream
consumer assumes.

The Receipt entity is the source of truth. Its four image-normalised,
y-up corners plus ``width``/``height`` define the upright warp that the
receipt-relative OCR coordinates live in. Repairs re-derive that warp
with the same transform the FIRST_PASS ingest path uses
(``ocr_processor._get_perspective_coeffs`` ->
``Image.transform(PERSPECTIVE)``, shared here via
``ocr_migration_publish_crops.warp_receipt_crop``), so a repaired asset
lands in the coordinate frame the words were recorded in.

Scan mode measures every published size variant independently -- they
are uploaded separately and have been observed to disagree -- and grades
each one's pixel aspect ratio against the entity's declared dimensions.
Apply mode rebuilds the crop from the original upload and republishes
every variant to the same keys, but only after a self-check projects
real OCR word boxes onto the new canvas and confirms the text lands
inside them.

Usage::

    # scan (default; never writes)
    python scripts/rewarp_receipt_cdn_images.py \
        --table ReceiptsTable-dc5be22 --report-json /tmp/scan.json

    # preview what a repair would do, uploading nothing
    python scripts/rewarp_receipt_cdn_images.py \
        --table ReceiptsTable-dc5be22 --dry-run-apply

    # repair a single image's receipts
    python scripts/rewarp_receipt_cdn_images.py \
        --table ReceiptsTable-dc5be22 --image-id <uuid> --apply
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image as PIL_Image
from PIL import ImageOps, ImageStat, UnidentifiedImageError

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import Image as ImageEntity
from receipt_dynamo.entities import Receipt, ReceiptWord
from receipt_upload.utils import upload_all_cdn_formats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# isort: off
from scripts.ocr_migration_publish_crops import (  # noqa: E402
    CDN_KEY_FIELDS,
    expected_cdn_keys,
    warp_receipt_crop,
)

# isort: on

logger = logging.getLogger("rewarp_receipt_cdn_images")

# Aspect ratios within this relative tolerance are treated as equal. CDN
# variants are resized with integer rounding, so a thumbnail's ratio
# never matches the full-size ratio exactly.
ASPECT_TOLERANCE = 0.04

# A quad whose implied aspect ratio disagrees with the entity's declared
# width/height by more than this is reported as suspect entity geometry.
QUAD_ASPECT_TOLERANCE = 0.20

# Mean 0-255 luminance gap (surrounding ring minus word interior) that a
# rebuilt crop must clear for its OCR boxes to count as aligned.
DEFAULT_DARKNESS_MARGIN = 6.0

# Bytes pulled for a ranged probe. Enough for any JPEG/WebP/AVIF header,
# and usually the whole thumbnail.
HEADER_PROBE_BYTES = 65536


# Size variants probed during a scan, worst-first for reporting. Each
# variant is checked independently because they are uploaded separately
# and have been observed to disagree with each other.
PROBED_VARIANTS: tuple[tuple[str, str], ...] = (
    ("full", "cdn_s3_key"),
    ("medium", "cdn_medium_s3_key"),
    ("small", "cdn_small_s3_key"),
    ("thumbnail", "cdn_thumbnail_s3_key"),
)

# Verdicts ordered by severity, so a receipt's overall status is the
# worst of its variants.
VERDICT_SEVERITY = {
    "ok": 0,
    "cdn_unreadable": 1,
    "aspect_mismatch": 2,
    "rotated_90": 3,
}


@dataclass
class VariantProbe:
    """One CDN size variant's measured dimensions and verdict."""

    name: str
    key: str
    verdict: str
    width: Optional[int] = None
    height: Optional[int] = None
    aspect: Optional[float] = None
    note: Optional[str] = None


@dataclass
class ScanResult:
    """One receipt's scan verdict across all CDN size variants."""

    image_id: str
    receipt_id: int
    status: str
    entity_width: int = 0
    entity_height: int = 0
    entity_aspect: Optional[float] = None
    quad_aspect: Optional[float] = None
    variants: list[VariantProbe] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_mismatch(self) -> bool:
        return self.status in ("rotated_90", "aspect_mismatch")

    @property
    def bad_variants(self) -> list[VariantProbe]:
        return [
            v
            for v in self.variants
            if v.verdict in ("rotated_90", "aspect_mismatch")
        ]


def _aspect(width: float, height: float) -> Optional[float]:
    if width <= 0 or height <= 0:
        return None
    return width / height


def _close(a: float, b: float, tolerance: float) -> bool:
    """Relative comparison in log space, so a/b and b/a are symmetric."""
    if a <= 0 or b <= 0:
        return False
    return abs(math.log(a / b)) <= math.log1p(tolerance)


def _corner_pixels(
    receipt: Receipt, image_width: int, image_height: int
) -> list[tuple[float, float]]:
    """The receipt's four corners as PIL pixels on the original image.

    Entity corners are image-normalised and y-up (OCR convention); PIL
    counts rows downward, hence the ``1 - y``.
    """
    return [
        (
            receipt.top_left["x"] * image_width,
            (1.0 - receipt.top_left["y"]) * image_height,
        ),
        (
            receipt.top_right["x"] * image_width,
            (1.0 - receipt.top_right["y"]) * image_height,
        ),
        (
            receipt.bottom_right["x"] * image_width,
            (1.0 - receipt.bottom_right["y"]) * image_height,
        ),
        (
            receipt.bottom_left["x"] * image_width,
            (1.0 - receipt.bottom_left["y"]) * image_height,
        ),
    ]


def _quad_aspect(
    receipt: Receipt, image_width: int, image_height: int
) -> Optional[float]:
    """Aspect ratio implied by the corner quad itself, in image pixels.

    Compared against the declared width/height this catches entities
    whose stored dimensions never matched their own geometry.
    """
    tl, tr, br, bl = _corner_pixels(receipt, image_width, image_height)
    top = math.dist(tl, tr)
    bottom = math.dist(bl, br)
    left = math.dist(tl, bl)
    right = math.dist(tr, br)
    return _aspect((top + bottom) / 2.0, (left + right) / 2.0)


def _open_image_size(data: bytes) -> Optional[tuple[int, int]]:
    """Read pixel dimensions from (possibly partial) encoded bytes."""
    try:
        with PIL_Image.open(io.BytesIO(data)) as img:
            return img.size
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def _probe_cdn_size(
    s3_client: Any, bucket: str, key: str
) -> tuple[Optional[tuple[int, int]], Optional[str]]:
    """Pixel dimensions of a CDN object, or an error string.

    A ranged GET of the header is enough for JPEG/WebP/AVIF; the full
    object is fetched only if the header alone will not parse.
    """
    try:
        response = s3_client.get_object(
            Bucket=bucket,
            Key=key,
            Range=f"bytes=0-{HEADER_PROBE_BYTES - 1}",
        )
        head = response["Body"].read()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NoSuchBucket", "AccessDenied"):
            return None, f"s3_error:{code}"
        return None, f"s3_error:{code or exc}"
    except BotoCoreError as exc:
        return None, f"s3_error:{exc}"

    size = _open_image_size(head)
    if size is not None:
        return size, None

    try:
        full = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    except (ClientError, BotoCoreError) as exc:
        return None, f"s3_error:{exc}"
    size = _open_image_size(full)
    if size is None:
        return None, "undecodable"
    return size, None


def scan_receipt(
    receipt: Receipt,
    image_entity: Optional[ImageEntity],
    s3_client: Any,
) -> ScanResult:
    """Compare a receipt's published CDN asset against its entity."""
    result = ScanResult(
        image_id=receipt.image_id,
        receipt_id=receipt.receipt_id,
        status="ok",
        entity_width=receipt.width,
        entity_height=receipt.height,
    )
    result.entity_aspect = _aspect(receipt.width, receipt.height)

    if image_entity is not None:
        try:
            result.quad_aspect = _quad_aspect(
                receipt, image_entity.width, image_entity.height
            )
        except (TypeError, KeyError, ValueError) as exc:
            result.notes.append(f"quad_aspect_failed:{exc}")
        if (
            result.quad_aspect is not None
            and result.entity_aspect is not None
            and not _close(
                result.quad_aspect,
                result.entity_aspect,
                QUAD_ASPECT_TOLERANCE,
            )
        ):
            result.notes.append("entity_geometry_suspect")
    else:
        result.notes.append("image_entity_missing")

    bucket = receipt.cdn_s3_bucket
    if not bucket:
        result.status = "no_cdn_asset"
        return result

    for name, field_name in PROBED_VARIANTS:
        key = getattr(receipt, field_name, None)
        if not key:
            continue
        result.variants.append(
            _probe_variant(s3_client, bucket, name, key, result.entity_aspect)
        )

    if not result.variants:
        result.status = "no_cdn_asset"
        return result

    result.status = max(
        (v.verdict for v in result.variants),
        key=lambda verdict: VERDICT_SEVERITY.get(verdict, 0),
    )
    return result


def _probe_variant(
    s3_client: Any,
    bucket: str,
    name: str,
    key: str,
    entity_aspect: Optional[float],
) -> VariantProbe:
    """Measure one CDN variant and grade it against the entity aspect."""
    probe = VariantProbe(name=name, key=key, verdict="ok")
    size, error = _probe_cdn_size(s3_client, bucket, key)
    if size is None:
        probe.verdict = "cdn_unreadable"
        probe.note = error or "unknown"
        return probe

    probe.width, probe.height = size
    probe.aspect = _aspect(*size)
    if probe.aspect is None or entity_aspect is None:
        probe.verdict = "cdn_unreadable"
        probe.note = "degenerate_dimensions"
        return probe

    if _close(probe.aspect, entity_aspect, ASPECT_TOLERANCE):
        probe.verdict = "ok"
    elif _close(probe.aspect, 1.0 / entity_aspect, ASPECT_TOLERANCE):
        probe.verdict = "rotated_90"
    else:
        probe.verdict = "aspect_mismatch"
    return probe


def _word_boxes_in_pixels(
    words: list[ReceiptWord], canvas_width: int, canvas_height: int
) -> list[tuple[int, int, int, int]]:
    """Receipt-relative y-up word boxes as PIL pixel boxes."""
    boxes: list[tuple[int, int, int, int]] = []
    for word in words:
        bbox = getattr(word, "bounding_box", None)
        if not bbox:
            continue
        try:
            x = float(bbox["x"])
            y = float(bbox["y"])
            w = float(bbox["width"])
            h = float(bbox["height"])
        except (KeyError, TypeError, ValueError):
            continue
        left = int(round(x * canvas_width))
        right = int(round((x + w) * canvas_width))
        # y is the bottom edge in y-up space; PIL rows count downward.
        top = int(round((1.0 - (y + h)) * canvas_height))
        bottom = int(round((1.0 - y) * canvas_height))
        left, right = max(0, min(left, right)), min(
            canvas_width, max(left, right)
        )
        top, bottom = max(0, min(top, bottom)), min(
            canvas_height, max(top, bottom)
        )
        if right - left < 4 or bottom - top < 4:
            continue
        boxes.append((left, top, right, bottom))
    return boxes


def _select_probe_boxes(
    words: list[ReceiptWord],
    canvas_width: int,
    canvas_height: int,
    sample: int,
) -> list[tuple[int, int, int, int]]:
    """Pick text-bearing boxes spread down the receipt.

    Sampling across the vertical span matters: a rotated or otherwise
    wrong warp can still align near one edge by luck.
    """
    candidates = [
        word
        for word in words
        if len((getattr(word, "text", "") or "").strip()) >= 3
    ]
    if not candidates:
        candidates = list(words)
    candidates.sort(
        key=lambda w: float((w.bounding_box or {}).get("y", 0.0)),
        reverse=True,
    )
    boxes = _word_boxes_in_pixels(candidates, canvas_width, canvas_height)
    if len(boxes) <= sample:
        return boxes
    step = len(boxes) / float(sample)
    return [boxes[int(i * step)] for i in range(sample)]


def _mean_luminance(gray: PIL_Image.Image, box: tuple[int, int, int, int]):
    """(mean, pixel count) for a crop, or None when the crop is empty."""
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return None
    crop = gray.crop(box)
    count = (right - left) * (bottom - top)
    return ImageStat.Stat(crop).mean[0], count


def text_alignment_score(
    warped: PIL_Image.Image,
    words: list[ReceiptWord],
    sample: int = 5,
) -> tuple[Optional[float], int]:
    """How much darker word interiors are than their surroundings.

    Projects OCR boxes onto the rebuilt canvas and measures the 0-255
    luminance gap between each box and the ring just outside it. Text is
    dark on light paper, so a correct warp yields a positive gap; a warp
    that does not match the OCR frame yields roughly zero.

    Returns ``(mean gap, boxes measured)``; the gap is ``None`` when no
    box could be measured.
    """
    boxes = _select_probe_boxes(words, warped.width, warped.height, sample)
    if not boxes:
        return None, 0

    gray = warped.convert("L")
    gaps: list[float] = []
    for box in boxes:
        left, top, right, bottom = box
        pad_x = max(2, (right - left) // 2)
        pad_y = max(2, (bottom - top))
        outer = (
            max(0, left - pad_x),
            max(0, top - pad_y),
            min(warped.width, right + pad_x),
            min(warped.height, bottom + pad_y),
        )
        inner_stat = _mean_luminance(gray, box)
        outer_stat = _mean_luminance(gray, outer)
        if inner_stat is None or outer_stat is None:
            continue
        inner_mean, inner_count = inner_stat
        outer_mean, outer_count = outer_stat
        ring_count = outer_count - inner_count
        if ring_count <= 0:
            continue
        ring_mean = (
            outer_mean * outer_count - inner_mean * inner_count
        ) / ring_count
        gaps.append(ring_mean - inner_mean)

    if not gaps:
        return None, 0
    return sum(gaps) / len(gaps), len(gaps)


def _derive_base_key(receipt: Receipt) -> Optional[str]:
    """Base CDN key (no size suffix, no extension) for this receipt."""
    if not receipt.cdn_s3_key:
        return None
    base, _ext = os.path.splitext(receipt.cdn_s3_key)
    return base


def _expected_key_conflicts(
    receipt: Receipt, base_key: str
) -> list[tuple[str, str, str]]:
    """Entity CDN keys that a republish would not overwrite in place.

    ``upload_all_cdn_formats`` derives every variant key from the base
    key. If the entity points somewhere else, republishing would orphan
    the live asset instead of fixing it, so the caller must not proceed.
    """
    derived = expected_cdn_keys(base_key)
    conflicts = []
    for field_name, dict_key in CDN_KEY_FIELDS:
        current = getattr(receipt, field_name, None)
        if not current:
            continue
        expected = derived[dict_key]
        if current != expected:
            conflicts.append((field_name, current, expected))
    return conflicts


def _load_original(
    s3_client: Any, bucket: str, key: str
) -> tuple[Optional[PIL_Image.Image], list[str]]:
    """Fetch the original upload, oriented the way OCR saw it.

    Nothing in the ingest path applies EXIF orientation, but Vision
    does, so the entity's normalised corners live in the transposed
    frame. Re-warping without this step is what produces a sideways
    crop.
    """
    notes: list[str] = []
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    except (ClientError, BotoCoreError) as exc:
        notes.append(f"original_download_failed:{exc}")
        return None, notes

    try:
        image = PIL_Image.open(io.BytesIO(body))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        notes.append(f"original_undecodable:{exc}")
        return None, notes

    before = image.size
    oriented = ImageOps.exif_transpose(image) or image
    if oriented.size != before:
        notes.append(f"exif_transposed:{before}->{oriented.size}")
    return oriented, notes


def _backup_existing_variants(
    s3_client: Any,
    bucket: str,
    receipt: Receipt,
    backup_prefix: str,
) -> tuple[list[str], Optional[str]]:
    """Server-side copy the live CDN objects aside before overwriting.

    The site bucket has no versioning, so a republish is otherwise
    unrecoverable. Copies are server-side (no download) and land at
    ``{backup_prefix}{original_key}``, which is also exactly the
    argument order ``aws s3 cp`` needs to put them back.

    Returns ``(copied keys, error)``; a non-None error means the caller
    must not overwrite anything for this receipt.
    """
    copied: list[str] = []
    for field_name, _dict_key in CDN_KEY_FIELDS:
        key = getattr(receipt, field_name, None)
        if not key:
            continue
        try:
            s3_client.copy_object(
                Bucket=bucket,
                Key=f"{backup_prefix}{key}",
                CopySource={"Bucket": bucket, "Key": key},
            )
            copied.append(key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404"):
                # Nothing published at this key yet; nothing to lose.
                continue
            return copied, f"backup_failed:{key}:{code or exc}"
        except BotoCoreError as exc:
            return copied, f"backup_failed:{key}:{exc}"
    return copied, None


def rewarp_receipt(
    dynamo: DynamoClient,
    s3_client: Any,
    receipt: Receipt,
    image_entity: ImageEntity,
    darkness_margin: float,
    dry_run: bool,
    backup_prefix: Optional[str] = None,
) -> dict[str, Any]:
    """Rebuild and republish one receipt's CDN variants.

    Returns a record describing what happened. The upload only runs once
    the rebuilt crop passes the OCR text-alignment self-check.
    """
    record: dict[str, Any] = {
        "image_id": receipt.image_id,
        "receipt_id": receipt.receipt_id,
        "status": "skipped",
        "notes": [],
    }

    base_key = _derive_base_key(receipt)
    if not base_key or not receipt.cdn_s3_bucket:
        record["status"] = "skipped_no_cdn_asset"
        return record

    conflicts = _expected_key_conflicts(receipt, base_key)
    if conflicts:
        record["status"] = "skipped_key_mismatch"
        record["notes"] = [
            f"{name}: entity={current} derived={expected}"
            for name, current, expected in conflicts
        ]
        return record

    original, notes = _load_original(
        s3_client, image_entity.raw_s3_bucket, image_entity.raw_s3_key
    )
    record["notes"].extend(notes)
    if original is None:
        record["status"] = "failed_original"
        return record

    image_width, image_height = original.size
    entity_aspect = _aspect(image_entity.width, image_entity.height)
    actual_aspect = _aspect(image_width, image_height)
    if (
        entity_aspect
        and actual_aspect
        and not _close(entity_aspect, actual_aspect, ASPECT_TOLERANCE)
    ):
        record["status"] = "skipped_original_aspect"
        record["notes"].append(
            f"image entity {image_entity.width}x{image_entity.height} "
            f"disagrees with original {image_width}x{image_height}"
        )
        return record

    try:
        warped = warp_receipt_crop(original, receipt)
    except (ValueError, OSError) as exc:
        record["status"] = "failed_warp"
        record["notes"].append(str(exc))
        return record

    words = dynamo.list_receipt_words_from_receipt(
        receipt.image_id, receipt.receipt_id
    )
    gap, measured = text_alignment_score(warped, words)
    record["alignment_gap"] = None if gap is None else round(gap, 2)
    record["alignment_boxes"] = measured
    if gap is None:
        record["status"] = "skipped_no_words"
        return record
    if gap < darkness_margin:
        record["status"] = "skipped_alignment_check"
        record["notes"].append(
            f"text/background gap {gap:.2f} below margin {darkness_margin}"
        )
        return record

    if dry_run:
        record["status"] = "would_apply"
        return record

    # Back up before the first overwrite, never after: a partial upload
    # with no backup is the one outcome there is no way back from.
    if backup_prefix:
        copied, error = _backup_existing_variants(
            s3_client, receipt.cdn_s3_bucket, receipt, backup_prefix
        )
        record["backed_up"] = len(copied)
        if error:
            record["status"] = "skipped_backup_failed"
            record["notes"].append(error)
            return record

    try:
        upload_all_cdn_formats(
            warped,
            receipt.cdn_s3_bucket,
            base_key,
            generate_thumbnails=True,
        )
    except (ClientError, BotoCoreError, OSError) as exc:
        record["status"] = "failed_upload"
        record["notes"].append(str(exc))
        return record

    record["status"] = "applied"
    return record


def _parse_excludes(raw: list[str]) -> set[tuple[str, int]]:
    """Parse ``image_id:receipt_id`` exclusions into a lookup set."""
    excludes: set[tuple[str, int]] = set()
    for item in raw:
        image_id, _, receipt_id = item.partition(":")
        if not image_id or not receipt_id.isdigit():
            raise ValueError(
                f"--exclude expects IMAGE_ID:RECEIPT_ID, got {item!r}"
            )
        excludes.add((image_id, int(receipt_id)))
    return excludes


def _load_receipts(
    dynamo: DynamoClient, image_ids: Optional[set[str]]
) -> list[Receipt]:
    receipts, _ = dynamo.list_receipts()
    if image_ids:
        receipts = [r for r in receipts if r.image_id in image_ids]
    return receipts


def _load_images(dynamo: DynamoClient) -> dict[str, ImageEntity]:
    images: dict[str, ImageEntity] = {}
    last_key = None
    while True:
        page, last_key = dynamo.list_images(last_evaluated_key=last_key)
        for image in page:
            images[image.image_id] = image
        if not last_key:
            break
    return images


def _summarize(results: list[ScanResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return dict(sorted(counts.items()))


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan for (and optionally repair) receipt CDN images whose "
            "warp does not match the Receipt entity's geometry."
        )
    )
    parser.add_argument(
        "--table",
        required=True,
        help="DynamoDB table name (e.g. ReceiptsTable-dc5be22).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--scan-only",
        action="store_true",
        default=True,
        help="Report mismatches without writing (default).",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Rebuild and republish CDN assets for mismatched receipts.",
    )
    mode.add_argument(
        "--dry-run-apply",
        action="store_true",
        help=(
            "Rebuild each mismatched crop and run the alignment "
            "self-check, but upload nothing. Use this to preview what "
            "--apply would do."
        ),
    )
    parser.add_argument(
        "--apply-all",
        action="store_true",
        help=(
            "Required with --apply when no --image-id filter is given, "
            "so an unfiltered rewrite is never a fat-finger away."
        ),
    )
    parser.add_argument(
        "--image-id",
        action="append",
        dest="image_ids",
        help="Restrict to this image id. Repeatable.",
    )
    parser.add_argument(
        "--image-ids-file",
        help=(
            "File of image ids, one per line (blank lines and '#' "
            "comments ignored). Merged with any --image-id values."
        ),
    )
    parser.add_argument(
        "--exclude",
        action="append",
        dest="excludes",
        default=[],
        metavar="IMAGE_ID:RECEIPT_ID",
        help=(
            "Never repair this receipt, even if it scans as mismatched. "
            "Repeatable. Use for receipts whose entity geometry is known "
            "to be wrong, where a re-warp would bake in the error."
        ),
    )
    parser.add_argument(
        "--backup-prefix",
        help=(
            "S3 key prefix (same bucket) to copy live CDN objects to "
            "before overwriting them. Defaults to a timestamped "
            "'backups/rewarp-<UTC>/' prefix."
        ),
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help=(
            "Overwrite without backing up first. The site bucket is not "
            "versioned, so this makes --apply unrecoverable."
        ),
    )
    parser.add_argument(
        "--report-json",
        help="Write the full scan/apply report to this path.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Parallel S3 probes during scanning (default: 16).",
    )
    parser.add_argument(
        "--darkness-margin",
        type=float,
        default=DEFAULT_DARKNESS_MARGIN,
        help=(
            "Minimum 0-255 luminance gap between OCR word interiors and "
            "their surroundings for a rebuilt crop to be published "
            f"(default: {DEFAULT_DARKNESS_MARGIN})."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log every receipt, not just mismatches.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    # --verbose is about per-receipt detail, not boto's wire logging.
    for noisy in ("boto3", "botocore", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if (
        args.apply
        and not args.image_ids
        and not args.image_ids_file
        and not args.apply_all
    ):
        logger.error(
            "--apply without an image filter requires --apply-all; "
            "refusing to rewrite every receipt in %s by accident.",
            args.table,
        )
        return 2

    try:
        excludes = _parse_excludes(args.excludes)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    backup_prefix: Optional[str] = None
    if args.apply and not args.no_backup:
        backup_prefix = args.backup_prefix or (
            "backups/rewarp-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "/"
        )
        if not backup_prefix.endswith("/"):
            backup_prefix += "/"
        logger.info("Backing up replaced objects under %s", backup_prefix)
    elif args.apply:
        logger.warning(
            "--no-backup: overwrites will be unrecoverable on an "
            "unversioned bucket."
        )

    dynamo = DynamoClient(args.table)
    s3_client = boto3.client(
        "s3",
        config=BotoConfig(max_pool_connections=max(args.workers, 10)),
    )

    image_ids = set(args.image_ids or [])
    if args.image_ids_file:
        with open(args.image_ids_file, encoding="utf-8") as handle:
            for line in handle:
                entry = line.split("#", 1)[0].strip()
                if entry:
                    image_ids.add(entry)
    if not image_ids:
        image_ids = None
    else:
        logger.info("Restricted to %d image ids", len(image_ids))
    receipts = _load_receipts(dynamo, image_ids)
    images = _load_images(dynamo)
    logger.info("Scanning %d receipts in %s", len(receipts), args.table)

    results: list[ScanResult] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                scan_receipt, receipt, images.get(receipt.image_id), s3_client
            ): receipt
            for receipt in receipts
        }
        for future in as_completed(futures):
            receipt = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # pylint: disable=broad-except
                results.append(
                    ScanResult(
                        image_id=receipt.image_id,
                        receipt_id=receipt.receipt_id,
                        status="scan_error",
                        notes=[str(exc)],
                    )
                )

    results.sort(key=lambda r: (r.image_id, r.receipt_id))
    counts = _summarize(results)
    mismatches = [r for r in results if r.is_mismatch]
    suspect = [r for r in results if "entity_geometry_suspect" in r.notes]

    logger.info("Scan summary for %s: %s", args.table, counts)
    for result in results:
        logger.debug(
            "%s/%d %s entity=%dx%d variants: %s",
            result.image_id,
            result.receipt_id,
            result.status,
            result.entity_width,
            result.entity_height,
            ", ".join(
                f"{v.name}={v.width}x{v.height}({v.verdict})"
                for v in result.variants
            ),
        )
    for result in mismatches:
        detail = ", ".join(
            f"{v.name}={v.width}x{v.height}({v.verdict})"
            for v in result.bad_variants
        )
        logger.info(
            "MISMATCH %s/%d status=%s entity=%dx%d bad_variants: %s",
            result.image_id,
            result.receipt_id,
            result.status,
            result.entity_width,
            result.entity_height,
            detail,
        )
    for result in suspect:
        logger.info(
            "ENTITY-SUSPECT %s/%d entity_aspect=%.3f quad_aspect=%.3f",
            result.image_id,
            result.receipt_id,
            result.entity_aspect or 0.0,
            result.quad_aspect or 0.0,
        )

    report: dict[str, Any] = {
        "table": args.table,
        "receipts_scanned": len(results),
        "counts": counts,
        "mismatches": [asdict(r) for r in mismatches],
        "entity_geometry_suspect": [asdict(r) for r in suspect],
    }

    if args.apply or args.dry_run_apply:
        by_key = {(r.image_id, r.receipt_id): r for r in receipts}
        applied: list[dict[str, Any]] = []
        for result in mismatches:
            if (result.image_id, result.receipt_id) in excludes:
                applied.append(
                    {
                        "image_id": result.image_id,
                        "receipt_id": result.receipt_id,
                        "status": "skipped_excluded",
                        "notes": ["excluded on the command line"],
                    }
                )
                continue
            receipt = by_key.get((result.image_id, result.receipt_id))
            image_entity = images.get(result.image_id)
            if receipt is None or image_entity is None:
                applied.append(
                    {
                        "image_id": result.image_id,
                        "receipt_id": result.receipt_id,
                        "status": "skipped_missing_entity",
                    }
                )
                continue
            record = rewarp_receipt(
                dynamo,
                s3_client,
                receipt,
                image_entity,
                args.darkness_margin,
                dry_run=args.dry_run_apply,
                backup_prefix=backup_prefix,
            )
            logger.info(
                "%s %s/%d gap=%s %s",
                record["status"].upper(),
                record["image_id"],
                record["receipt_id"],
                record.get("alignment_gap"),
                "; ".join(record.get("notes", [])),
            )
            applied.append(record)
        report["applied"] = applied
        report["apply_counts"] = _summarize(
            [
                ScanResult(
                    image_id=r["image_id"],
                    receipt_id=r["receipt_id"],
                    status=r["status"],
                )
                for r in applied
            ]
        )
        logger.info("Apply summary: %s", report["apply_counts"])

    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        logger.info("Wrote report to %s", args.report_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())

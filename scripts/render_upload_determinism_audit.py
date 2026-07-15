#!/usr/bin/env python3
"""Render private, local review sheets for the preregistered 20-receipt audit.

The sheets can contain receipt PII and must stay outside the repository.  This
helper reads only the environment-owned dev table and S3 objects named by its
Receipt entities.  Every S3 bucket read must also be listed in the
``UPLOAD_DETERMINISM_AUDIT_S3_BUCKETS`` environment variable.
"""

# pylint: disable=wrong-import-position,duplicate-code

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

import boto3
from PIL import Image, ImageDraw, ImageFont, ImageOps

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
for _package in ("receipt_dynamo", "receipt_chroma", "receipt_upload"):
    sys.path.insert(0, str(_ROOT / _package))

from receipt_dynamo import DynamoClient
from scripts.evaluate_upload_determinism import (
    _create_private_directories,
    _leaf_fields,
    assert_dev_table,
    assert_private_destination,
)

_BUCKET_ALLOWLIST_ENV = "UPLOAD_DETERMINISM_AUDIT_S3_BUCKETS"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--environment", default=os.environ.get("DEPLOYMENT_ENVIRONMENT")
    )
    return parser.parse_args()


def _bucket_allowlist(value: str | None) -> frozenset[str]:
    """Parse the required, environment-owned S3 bucket allowlist."""

    allowed = frozenset(
        bucket.strip() for bucket in (value or "").split(",") if bucket.strip()
    )
    if not allowed:
        raise RuntimeError(f"{_BUCKET_ALLOWLIST_ENV} is required")
    return allowed


def _prepare_private_directory(path: Path) -> Path:
    """Create an owner-only audit directory outside the repository."""

    destination = assert_private_destination(path)
    _create_private_directories(destination)
    destination.chmod(0o700)
    return destination


def _image(receipt: Any, allowed_buckets: frozenset[str]) -> Image.Image:
    s3 = boto3.client("s3")
    candidates = [
        (receipt.cdn_s3_bucket, receipt.cdn_s3_key),
        (receipt.raw_s3_bucket, receipt.raw_s3_key),
    ]
    for bucket, key in candidates:
        if not bucket or not key:
            continue
        if bucket not in allowed_buckets:
            continue
        try:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            return Image.open(io.BytesIO(body)).convert("RGB")
        except s3.exceptions.NoSuchKey:
            continue
    raise RuntimeError(
        "receipt image unavailable for "
        f"{receipt.image_id}#{receipt.receipt_id}"
    )


def _save_private_png(image: Image.Image, path: Path) -> None:
    """Save an audit sheet owner-only without following a final symlink."""

    destination = assert_private_destination(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            image.save(output, format="PNG")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _summary(sample: dict[str, Any]) -> list[str]:
    lines = [
        f"{sample['sample_stratum']} | {sample.get('merchant') or 'Unknown'}",
        f"receipt {sample['image_id']}#{sample['receipt_id']}",
        "",
        "D4 fields",
    ]
    document = sample["d4"]
    for path, field in _leaf_fields(
        {
            "merchant": document["merchant"],
            "transaction": document["transaction"],
            "items": document["items"],
            "totals": document["totals"],
            "tender": document["tender"],
        }
    ):
        lines.append(
            f"{path}: {field.get('value')!r} "
            f"[{field.get('provenance')}, {field.get('validation_status')}]"
        )
    lines.extend(["", "D3 events"])
    for event in sample["d3"]["corrections"]:
        lines.append(
            f"L{event.get('line_id')} W{event.get('word_id')} "
            f"{event.get('action')} {event.get('original_label')} -> "
            f"{event.get('corrected_label')} ({event.get('rule_id')})"
        )
    lines.extend(["", "Resolver conflicts"])
    for conflict in document["conflicts"]:
        lines.append(f"{conflict.get('field')}: {conflict.get('rule_id')}")
    lines.extend(["", "OCR lines"])
    for line in sample["ocr_lines"]:
        lines.append(f"L{line['line_id']}: {line['text']}")
    return lines


def _sheet(image: Image.Image, sample: dict[str, Any]) -> Image.Image:
    height = 1800
    receipt_width = 800
    panel_width = 1000
    contained = ImageOps.contain(image, (receipt_width - 40, height - 40))
    canvas = Image.new("RGB", (receipt_width + panel_width, height), "white")
    canvas.paste(
        contained,
        ((receipt_width - contained.width) // 2, 20),
    )
    draw = ImageDraw.Draw(canvas)
    draw.line((receipt_width, 0, receipt_width, height), fill="gray", width=2)
    font = ImageFont.load_default(size=18)
    y = 20
    for raw_line in _summary(sample):
        for line in textwrap.wrap(raw_line, width=88) or [""]:
            draw.text((receipt_width + 20, y), line, fill="black", font=font)
            y += 23
            if y > height - 25:
                draw.text(
                    (receipt_width + 20, height - 25),
                    "[truncated; inspect JSON for remaining evidence]",
                    fill="red",
                    font=font,
                )
                return canvas
    return canvas


def main() -> int:
    args = _args()
    report_path = assert_private_destination(args.report)
    output_dir = _prepare_private_directory(args.output_dir)
    allowed_buckets = _bucket_allowlist(os.environ.get(_BUCKET_ALLOWLIST_ENV))
    table = assert_dev_table(
        args.environment, os.environ.get("DYNAMODB_TABLE_NAME")
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    samples = report["manual_sample"]
    client = DynamoClient(table)
    for index, sample in enumerate(samples, start=1):
        receipt = client.get_receipt(
            sample["image_id"], int(sample["receipt_id"])
        )
        rendered = _sheet(_image(receipt, allowed_buckets), sample)
        _save_private_png(rendered, output_dir / f"{index:02d}.png")
    print(f"rendered {len(samples)} private audit sheets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

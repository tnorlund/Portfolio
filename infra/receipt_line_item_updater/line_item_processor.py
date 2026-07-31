"""Business logic for recomputing RECEIPT_LINE_ITEM rows for one receipt.

Triggered when a receipt's summary changes -- the moment at which words,
sections, summary and merchant all exist -- so extraction always sees a
complete receipt. This also regenerates line items after resegmentation for
free: resegmentation rewrites the summary, which re-fires this stage.

Extraction is the canonical band-block decoder from
``receipt_upload.line_items``; those modules (and the priors asset) are
bundled into this Lambda's archive at deploy time as FileAssets referencing
the canonical sources -- no copies live in the repo, so the logic cannot
fork the way the shadowed ``build_receipt_rows`` once did.

Unlike the backfill script (which gates on VALID ITEMS sections), this
stage extracts on PENDING sections too and records
``source_section_status`` -- consumers filter on VALID when they want
precision; gating production on VALID is what once limited coverage to
hand-repaired receipts.
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_line_item import ReceiptLineItem
from receipt_upload.line_items.geometry import extract_items, reconcile

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")
dynamo_client = DynamoClient(TABLE_NAME) if TABLE_NAME else None

EXTRACTOR_VERSION = "line-items-blocks-v2"


def update_receipt_line_items(
    image_id: str, receipt_id: int
) -> dict[str, Any]:
    """Recompute and rewrite RECEIPT_LINE_ITEM rows for one receipt."""
    if dynamo_client is None:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    words = dynamo_client.list_receipt_words_from_receipt(image_id, receipt_id)
    sections = dynamo_client.get_receipt_sections_from_receipt(
        image_id, receipt_id
    )

    # ITEMS zone: any non-INVALID canonical ITEMS section; prefer VALID
    # over PENDING when both exist so provenance reflects the strongest
    # source. Exact match (like the backfill script): legacy
    # ITEMS_VALUE / ITEMS_DESCRIPTION zones are partial (prices-only or
    # names-only) and must not masquerade as a full ITEMS section.
    items_section = None
    for s in sections:
        if str(getattr(s, "section_type", "") or "").upper() != "ITEMS":
            continue
        status = str(getattr(s, "validation_status", "") or "").upper()
        if status == "INVALID":
            continue
        if items_section is None or status == "VALID":
            items_section = s
        if status == "VALID":
            break
    if items_section is None or not words:
        # Nothing to extract; clear stale rows so a receipt whose ITEMS
        # section was invalidated does not keep phantom items.
        deleted = dynamo_client.delete_receipt_line_items_for_receipt(
            image_id, receipt_id
        )
        return {"items": 0, "deleted": deleted, "reason": "no-items-zone"}

    line_ids = {int(x) for x in (items_section.line_ids or [])}
    word_dicts = [
        {
            "line_id": w.line_id,
            "word_id": w.word_id,
            "text": w.text,
            "x": w.bounding_box.get("x", 0.0),
            "y_mid": w.bounding_box.get("y", 0.0)
            + w.bounding_box.get("height", 0.0) / 2,
            "h": w.bounding_box.get("height", 0.0),
        }
        for w in words
        if w.line_id in line_ids
    ]

    # Summary is fetched BEFORE extraction: the decoder's non-product
    # band filter needs the printed subtotal/tax/grand_total to recognize
    # summary figures leaking into the ITEMS zone.
    summary_dict = None
    merchant = None
    try:
        record = dynamo_client.get_receipt_summary(image_id, receipt_id)
        inner = getattr(record, "summary", None)
        if inner is not None:
            summary_dict = {
                "subtotal": getattr(inner, "subtotal", None),
                "grand_total": getattr(inner, "grand_total", None),
                "tax": getattr(inner, "tax", None),
            }
            merchant = getattr(inner, "merchant_name", None)
    except EntityNotFoundError:
        # Only "summary does not exist yet" is a valid no-baseline case.
        # Operational failures (throttling, access, malformed data) must
        # propagate so SQS retries instead of acking rows with a silently
        # wrong no-baseline status and a missing merchant.
        logger.info(
            "no summary for %s:%d; reconciliation is no-baseline",
            image_id[:8],
            receipt_id,
        )

    items, collapsed = extract_items(
        word_dicts, line_ids, summary=summary_dict
    )

    status, _, _ = reconcile(
        [x for x in items if not x.get("is_discount")], summary_dict
    )

    now = datetime.now(timezone.utc)
    entities = []
    for idx, it in enumerate(items):
        name = it.get("name") or ""
        quality = (
            "low" if it.get("name_quality") == "low" or not name else "ok"
        )
        entities.append(
            ReceiptLineItem(
                receipt_id=receipt_id,
                image_id=image_id,
                item_index=idx,
                name=name,
                price=f"{it['price']:.2f}",
                line_ids=[int(x) for x in it["line_ids"]],
                extractor_version=EXTRACTOR_VERSION,
                extracted_at=now,
                quantity=it.get("quantity"),
                unit_price=it.get("unit_price"),
                is_discount=bool(it.get("is_discount")),
                raw_text=it.get("raw_text") or "",
                name_quality=quality,
                merchant_name=merchant,
                source_section_status=getattr(
                    items_section, "validation_status", None
                ),
                source_model_source=getattr(
                    items_section, "model_source", None
                ),
                reconciliation_status=status,
                collapsed_banding=bool(collapsed),
            )
        )

    deleted = dynamo_client.delete_receipt_line_items_for_receipt(
        image_id, receipt_id
    )
    if entities:
        dynamo_client.add_receipt_line_items(entities)

    reocr = _maybe_trigger_items_reocr(
        image_id, receipt_id, items, summary_dict, word_dicts
    )
    return {
        "items": len(entities),
        "deleted": deleted,
        "reconciliation": status,
        "reocr_triggered": reocr,
    }


REOCR_REASON = "line_items_recon"
REOCR_MAX_ATTEMPTS = 2


def _maybe_trigger_items_reocr(
    image_id: str,
    receipt_id: int,
    items: list[dict],
    summary_dict: dict | None,
    zone_words: list[dict],
) -> bool:
    """Fire a capped REGIONAL_REOCR of the ITEMS zone on reconciliation
    mismatch (the digit-misread signature no downstream logic can fix).

    Failure here must never fail the message: line-item rows are already
    written, and re-OCR is an improvement pass, not a correctness
    requirement. The attempt cap exists because some glyphs are
    unreadable at any resolution (Twin Peaks) -- without it, every
    recompute of a permanently-mismatched receipt would re-fire.
    """
    fn = os.environ.get("TRIGGER_REOCR_FUNCTION_NAME")
    if not fn or dynamo_client is None:
        return False
    try:
        from receipt_upload.line_items.blocks import should_reocr_items_zone

        subtotal = None
        if summary_dict and summary_dict.get("subtotal") is not None:
            subtotal = float(summary_dict["subtotal"])
        if not should_reocr_items_zone(items, subtotal):
            return False

        jobs, _ = dynamo_client.list_ocr_jobs_for_image(image_id)
        prior = [
            j
            for j in jobs
            if getattr(j, "job_type", "") == "REGIONAL_REOCR"
            and getattr(j, "receipt_id", None) == receipt_id
            and getattr(j, "reocr_reason", "") == REOCR_REASON
        ]
        if len(prior) >= REOCR_MAX_ATTEMPTS:
            logger.info(
                "re-OCR cap reached for %s:%d (%d attempts)",
                image_id[:8],
                receipt_id,
                len(prior),
            )
            return False

        from receipt_upload.line_items.reocr import items_zone_reocr_region

        receipt = dynamo_client.get_receipt(image_id, receipt_id)
        image = dynamo_client.get_image(image_id)
        region = items_zone_reocr_region(
            zone_words, receipt, image.width, image.height
        )
        if region is None:
            return False

        import boto3

        boto3.client("lambda").invoke(
            FunctionName=fn,
            InvocationType="Event",
            Payload=json.dumps(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "reocr_region": region,
                    "reocr_reason": REOCR_REASON,
                }
            ).encode(),
        )
        logger.info(
            "triggered items-zone re-OCR for %s:%d region=%s",
            image_id[:8],
            receipt_id,
            region,
        )
        return True
    except Exception:  # noqa: BLE001 - best-effort improvement pass
        logger.exception(
            "re-OCR trigger failed for %s:%d (non-fatal)",
            image_id[:8],
            receipt_id,
        )
        return False


def deduplicate_messages(
    records: list[dict[str, Any]],
) -> tuple[dict[tuple[str, int], list[str]], list[str]]:
    """Deduplicate SQS records by (image_id, receipt_id).

    Returns (unique_receipts -> message_ids, malformed_message_ids).
    """
    unique: dict[tuple[str, int], list[str]] = defaultdict(list)
    malformed: list[str] = []
    for record in records:
        msg_id = record.get("messageId", "")
        try:
            body = json.loads(record.get("body", "{}"))
            data = body.get("entity_data") or body
            image_id = data["image_id"]
            receipt_id = int(data["receipt_id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("malformed message %s", msg_id)
            malformed.append(msg_id)
            continue
        unique[(image_id, receipt_id)].append(msg_id)
    return dict(unique), malformed

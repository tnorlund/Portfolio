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

CONSISTENCY CHECKER
-------------------
The Mac worker now runs the same decoder on device and ships its rows with
the OCR upload (``receipt_upload.line_items.provenance``). When this stage
finds worker-written rows already in the table it COMPARES instead of
blindly overwriting:

* the recompute wins by default -- byte-identical to the pre-worker
  behavior, and the only outcome possible for a receipt with no worker rows;
* the worker's rows are preserved only when they are *strictly better* by
  ``items_boundary_extension_guard`` -- the same arithmetic guard the ITEMS
  boundary repair uses (smaller |delta| AND a better reconciliation status,
  never against an already-matching baseline, never across a no-baseline
  side). Preserved rows are re-stamped with the merchant/summary context the
  cloud has and the worker did not.

Every receipt that arrives with worker rows emits one queryable log line
prefixed ``LINE_ITEM_DIVERGENCE`` carrying a JSON body (counts, names,
prices, both reconciliation statuses and deltas, and the decision), so
divergence is a CloudWatch Insights filter, not an archaeology exercise::

    fields @timestamp, @message
    | filter @message like /LINE_ITEM_DIVERGENCE/
    | filter divergent = 1
"""

import json
import logging
import os
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

# Warning #6 requires one first-party block in infra files.
# isort: off
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_line_item import ReceiptLineItem
from receipt_upload.line_items.geometry import (
    extract_items,
    items_boundary_extension_guard,
    propose_items_boundary_extension,
    reconcile_detailed,
)
from receipt_upload.line_items.provenance import (
    is_worker_extractor_version,
)

# isort: on

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")
dynamo_client = DynamoClient(TABLE_NAME) if TABLE_NAME else None

EXTRACTOR_VERSION = "line-items-blocks-v2"
BOUNDARY_EXTENSION_SOURCE = "zone-gap-extend-v1"


def update_receipt_line_items(
    image_id: str,
    receipt_id: int,
    reocr_mechanism: str | None = None,
) -> dict[str, Any]:
    """Recompute and rewrite RECEIPT_LINE_ITEM rows for one receipt.

    ``reocr_mechanism`` is an optional diagnosed OCR-failure mechanism
    (e.g. "reverse-video-total" from a triage dossier) threaded through
    to the re-OCR trigger so the strategy ladder can pick a targeted
    capture strategy. The SQS path never sets it; direct callers
    (scripts, agents) may.
    """
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

    extension = None
    if summary_dict is not None:
        items_section, extension = _maybe_extend_items_section(
            image_id,
            receipt_id,
            items_section,
            sections,
            word_dicts,
            summary_dict,
        )

    line_ids = {int(x) for x in (items_section.line_ids or [])}

    items, collapsed = extract_items(
        word_dicts, line_ids, summary=summary_dict
    )

    recon = reconcile_detailed(
        [x for x in items if not x.get("is_discount")], summary_dict
    )
    status = recon.status

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
                baseline_figures_agreeing=recon.baseline_figures_agreeing,
            )
        )

    # Consistency check against rows the Mac worker already decoded on
    # device. No worker rows -> `entities`/`items`/`recon` are used verbatim
    # and this stage behaves exactly as it did before the worker produced
    # anything.
    entities, items, recon, divergence = _reconcile_with_worker_rows(
        image_id, receipt_id, entities, items, recon, summary_dict, merchant
    )
    status = recon.status

    deleted = dynamo_client.delete_receipt_line_items_for_receipt(
        image_id, receipt_id
    )
    if entities:
        dynamo_client.add_receipt_line_items(entities)

    reocr = _maybe_trigger_items_reocr(
        image_id,
        receipt_id,
        items,
        summary_dict,
        [word for word in word_dicts if word["line_id"] in line_ids],
        reocr_mechanism=reocr_mechanism,
    )
    return {
        "items": len(entities),
        "deleted": deleted,
        "reconciliation": status,
        "baseline_source": recon.baseline_source,
        "baseline_figures_agreeing": recon.baseline_figures_agreeing,
        "section_extension": extension,
        "reocr_triggered": reocr,
        "worker_divergence": divergence,
    }


DIVERGENCE_MARKER = "LINE_ITEM_DIVERGENCE"


def _delta(result: Any) -> float | None:
    """|item sum - baseline| in the shape ``evaluate_items_zone`` returns."""

    if result.item_sum is None or result.baseline is None:
        return None
    return round(result.item_sum - result.baseline, 2)


def _row_to_item(row: Any) -> dict[str, Any]:
    """A stored ReceiptLineItem as the decoder's item dict."""

    try:
        price = float(row.price)
    except (TypeError, ValueError):
        price = 0.0
    return {
        "name": row.name,
        "price": price,
        "quantity": row.quantity,
        "unit_price": row.unit_price,
        "is_discount": bool(row.is_discount),
        "name_quality": row.name_quality,
        "line_ids": list(row.line_ids or []),
        "raw_text": row.raw_text,
    }


def _reconcile_with_worker_rows(
    image_id: str,
    receipt_id: int,
    entities: list[ReceiptLineItem],
    items: list[dict],
    recon: Any,
    summary_dict: dict | None,
    merchant: str | None,
) -> tuple[list[ReceiptLineItem], list[dict], Any, dict[str, Any] | None]:
    """Compare a fresh recompute against worker-written rows.

    Returns the rows to persist, their item dicts (for the re-OCR trigger),
    their reconciliation result, and a divergence record (``None`` when the
    receipt carries no worker rows -- the pre-worker path).

    The recompute wins unless the worker's rows pass
    ``items_boundary_extension_guard`` with the recompute as ``before``: the
    worker is preserved only when it strictly shrinks |delta| AND strictly
    improves reconciliation status. Reusing that guard verbatim means the
    conservative cases fall out for free -- a matching recompute is never
    displaced, and a no-baseline side is never ranked against a baselined
    one (which is exactly the common case, since the worker decodes before
    any summary exists).
    """
    try:
        stored = dynamo_client.get_receipt_line_items_from_receipt(
            image_id, receipt_id
        )
    except EntityNotFoundError:
        stored = []
    worker_rows = [
        row
        for row in stored
        if is_worker_extractor_version(getattr(row, "extractor_version", None))
    ]
    if not worker_rows:
        return entities, items, recon, None

    worker_rows = sorted(worker_rows, key=lambda row: row.item_index)
    worker_items = [_row_to_item(row) for row in worker_rows]
    worker_recon = reconcile_detailed(
        [item for item in worker_items if not item["is_discount"]],
        summary_dict,
    )

    before = {"status": recon.status, "delta": _delta(recon)}
    after = {"status": worker_recon.status, "delta": _delta(worker_recon)}
    keep_worker, guard_reason = items_boundary_extension_guard(before, after)

    cloud_names = [entity.name for entity in entities]
    cloud_prices = [entity.price for entity in entities]
    worker_names = [row.name for row in worker_rows]
    worker_prices = [f"{item['price']:.2f}" for item in worker_items]
    divergent = (
        cloud_names != worker_names
        or cloud_prices != worker_prices
        or recon.status != worker_recon.status
    )

    record = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "divergent": int(divergent),
        "decision": "keep-worker" if keep_worker else "keep-recompute",
        "guard_reason": guard_reason,
        "worker_extractor_version": worker_rows[0].extractor_version,
        "worker_count": len(worker_rows),
        "cloud_count": len(entities),
        "worker_status": worker_recon.status,
        "cloud_status": recon.status,
        "worker_delta": after["delta"],
        "cloud_delta": before["delta"],
        "name_mismatches": sum(
            1
            for cloud, worker in zip(cloud_names, worker_names)
            if cloud != worker
        ),
        "price_mismatches": sum(
            1
            for cloud, worker in zip(cloud_prices, worker_prices)
            if cloud != worker
        ),
    }
    logger.log(
        logging.WARNING if divergent else logging.INFO,
        "%s %s",
        DIVERGENCE_MARKER,
        json.dumps(record, sort_keys=True),
    )
    if not keep_worker:
        return entities, items, recon, record

    # Preserve the worker's DECODE, refresh its CONTEXT: merchant and the
    # graded reconciliation only exist in the cloud, and the worker had no
    # summary to reconcile against when it wrote these rows.
    now = datetime.now(timezone.utc)
    kept = [
        replace(
            row,
            extracted_at=now,
            merchant_name=merchant,
            reconciliation_status=worker_recon.status,
            baseline_figures_agreeing=(worker_recon.baseline_figures_agreeing),
        )
        for row in worker_rows
    ]
    return kept, worker_items, worker_recon, record


def _maybe_extend_items_section(
    image_id: str,
    receipt_id: int,
    items_section: Any,
    sections: list[Any],
    words: list[dict],
    summary: dict,
) -> tuple[Any, dict[str, Any] | None]:
    """Persist a reconciliation-verified adjacent-row ITEMS extension."""

    try:
        rows = dynamo_client.get_receipt_rows_from_receipt(
            image_id, receipt_id
        )
    except EntityNotFoundError:
        rows = []
    proposal = propose_items_boundary_extension(
        words=words,
        summary=summary,
        current_line_ids={int(x) for x in (items_section.line_ids or [])},
        sections=sections,
        rows=rows,
        current_row_ids=getattr(items_section, "row_ids", None),
    )
    if proposal is None:
        return items_section, None

    model_source = str(getattr(items_section, "model_source", "") or "")
    sources = [part for part in model_source.split("+") if part]
    if BOUNDARY_EXTENSION_SOURCE not in sources:
        sources.append(BOUNDARY_EXTENSION_SOURCE)
    updated = replace(
        items_section,
        line_ids=proposal["line_ids"],
        row_ids=proposal["row_ids"],
        model_source="+".join(sources),
    )
    # replace() preserves validation_status and all verifier provenance.  In
    # particular, a VALID section must never be demoted by automatic repair.
    dynamo_client.update_receipt_section(updated)
    logger.info(
        "extended ITEMS boundary for %s:%d with lines=%s (%s/%s -> %s/%s)",
        image_id[:8],
        receipt_id,
        proposal["added_line_ids"],
        proposal["before"]["status"],
        proposal["before"]["delta"],
        proposal["after"]["status"],
        proposal["after"]["delta"],
    )
    return updated, {
        "added_line_ids": proposal["added_line_ids"],
        "added_row_ids": proposal["added_row_ids"],
        "before": proposal["before"],
        "after": proposal["after"],
        "model_source": updated.model_source,
    }


REOCR_REASON = "line_items_recon"
REOCR_MAX_ATTEMPTS = 2


def _maybe_trigger_items_reocr(
    image_id: str,
    receipt_id: int,
    items: list[dict],
    summary_dict: dict | None,
    zone_words: list[dict],
    reocr_mechanism: str | None = None,
) -> bool:
    """Fire a capped REGIONAL_REOCR of the ITEMS zone on reconciliation
    mismatch (the digit-misread signature no downstream logic can fix).

    Failure here must never fail the message: line-item rows are already
    written, and re-OCR is an improvement pass, not a correctness
    requirement. The attempt cap exists because some glyphs are
    unreadable at any resolution (Twin Peaks) -- without it, every
    recompute of a permanently-mismatched receipt would re-fire.

    Each attempt climbs the SMART strategy ladder: attempt 1 gets the
    mechanism's best strategy, attempt 2 a DIFFERENT one (never a
    repeat of a capture that already failed).
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
        from receipt_upload.line_items.reocr_strategy import choose_strategy

        receipt = dynamo_client.get_receipt(image_id, receipt_id)
        image = dynamo_client.get_image(image_id)
        region = items_zone_reocr_region(
            zone_words, receipt, image.width, image.height
        )
        if region is None:
            return False

        attempt_number = len(prior) + 1
        strategy = choose_strategy(reocr_mechanism, attempt_number)

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
                    "reocr_strategy": strategy,
                    "reocr_mechanism": reocr_mechanism,
                }
            ).encode(),
        )
        logger.info(
            "triggered items-zone re-OCR for %s:%d region=%s "
            "strategy=%s mechanism=%s attempt=%d",
            image_id[:8],
            receipt_id,
            region,
            strategy,
            reocr_mechanism,
            attempt_number,
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

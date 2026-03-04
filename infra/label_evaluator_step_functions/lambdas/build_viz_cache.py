"""Lambda to build viz-cache directly from evaluator S3 outputs.

Replaces the LangSmith export + EMR Spark pipeline. Reads three S3 prefixes
written by unified_receipt_evaluator.py and writes two viz-cache prefixes
matching the TypeScript frontend types.

Input (from Step Function):
    execution_id: str
    batch_bucket: str
    cache_bucket: str
    total_receipts: int

S3 Reads:
    {batch_bucket}/unified/{execution_id}/*.json    - evaluation decisions
    {batch_bucket}/data/{execution_id}/*.json       - words, labels, place info
    {batch_bucket}/receipts_lookup/{execution_id}/*.json - CDN keys, dimensions

S3 Writes:
    {cache_bucket}/financial-math/{image_id}_{receipt_id}.json
    {cache_bucket}/financial-math/metadata.json
    {cache_bucket}/within-receipt/{image_id}_{receipt_id}.json
    {cache_bucket}/within-receipt/metadata.json
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

# ---------------------------------------------------------------------------
# Label sets for within-receipt verification
# ---------------------------------------------------------------------------

PLACE_LABELS = frozenset({
    "MERCHANT_NAME",
    "ADDRESS_LINE",
    "PHONE_NUMBER",
    "WEBSITE",
    "STORE_HOURS",
})

FORMAT_LABELS = frozenset({
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "LOYALTY_ID",
})


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------


def _list_keys(bucket: str, prefix: str) -> list[str]:
    """List all .json keys under a prefix (excluding metadata.json)."""
    keys: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json") and not key.endswith("metadata.json"):
                keys.append(key)
    return keys


def _read_json(bucket: str, key: str) -> dict[str, Any] | None:
    """Read and parse a single JSON file from S3."""
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except Exception:
        logger.warning("Failed to read s3://%s/%s", bucket, key, exc_info=True)
        return None


def _write_json(bucket: str, key: str, data: dict[str, Any]) -> None:
    """Write a JSON file to S3."""
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def _read_all_parallel(
    bucket: str, keys: list[str], max_workers: int = 20
) -> list[dict[str, Any]]:
    """Read multiple S3 JSON files in parallel."""
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_read_json, bucket, key): key for key in keys
        }
        for future in as_completed(futures):
            data = future.result()
            if data is not None:
                results.append(data)
    return results


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _extract_bbox(word: dict) -> dict[str, float]:
    """Extract normalized bounding box from a word dict."""
    bb = word.get("bounding_box") or word.get("bbox") or {}
    return {
        "x": bb.get("x", 0.0),
        "y": bb.get("y", 0.0),
        "width": bb.get("width", 0.0),
        "height": bb.get("height", 0.0),
    }


def _build_word_lookup(
    words: list[dict],
) -> dict[tuple[int, int], dict]:
    """Build (line_id, word_id) -> word dict lookup."""
    lookup: dict[tuple[int, int], dict] = {}
    for w in words:
        lid = w.get("line_id")
        wid = w.get("word_id")
        if lid is not None and wid is not None:
            lookup[(lid, wid)] = w
    return lookup


def _build_decision_entry(
    decision: dict,
    word_lookup: dict[tuple[int, int], dict],
) -> dict[str, Any]:
    """Convert a single all_decisions entry to output format with bbox."""
    issue = decision.get("issue", {})
    llm_review = decision.get("llm_review", {})
    lid = issue.get("line_id")
    wid = issue.get("word_id")

    word: dict[str, Any] = {}
    if lid is not None and wid is not None:
        word = word_lookup.get((lid, wid), {})

    entry: dict[str, Any] = {
        "line_id": lid,
        "word_id": wid,
        "word_text": issue.get("word_text", ""),
        "current_label": issue.get("current_label", ""),
        "decision": llm_review.get("decision"),
        "confidence": llm_review.get("confidence"),
        "reasoning": llm_review.get("reasoning"),
        "bbox": (
            _extract_bbox(word)
            if word
            else {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
        ),
    }
    suggested = llm_review.get("suggested_label")
    if suggested:
        entry["suggested_label"] = suggested
    return entry


def _build_cdn_fields(
    lookup_row: dict[str, Any] | None,
) -> tuple[dict[str, str], int, int]:
    """Extract CDN keys and dimensions from a lookup row."""
    cdn: dict[str, str] = {"cdn_s3_key": ""}
    width = 0
    height = 0
    if lookup_row:
        for key in (
            "cdn_s3_key",
            "cdn_webp_s3_key",
            "cdn_avif_s3_key",
            "cdn_medium_s3_key",
            "cdn_medium_webp_s3_key",
            "cdn_medium_avif_s3_key",
        ):
            val = lookup_row.get(key)
            if val is not None:
                cdn[key] = val
        width = lookup_row.get("width", 0) or 0
        height = lookup_row.get("height", 0) or 0
    return cdn, width, height


# ---------------------------------------------------------------------------
# Financial math cache builder
# ---------------------------------------------------------------------------


def _parse_numeric(text: str) -> float | None:
    """Parse a numeric value from word text, stripping currency symbols."""
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _build_equations(
    decisions: list[dict],
    word_lookup: dict[tuple[int, int], dict],
) -> list[dict[str, Any]]:
    """Reconstruct equations from per-word financial decisions.

    The unified evaluator outputs individual word decisions (line_id, word_id,
    current_label, word_text + llm_review). We reconstruct equation-level data
    by grouping words by their financial label roles:
      - GRAND_TOTAL = SUBTOTAL + TAX + TIP - DISCOUNT
      - GRAND_TOTAL_DIRECT = sum(LINE_TOTAL) + TAX + TIP - DISCOUNT (no SUBTOTAL)
      - SUBTOTAL = sum(LINE_TOTAL)
      - LINE_ITEM_BALANCED = QUANTITY × UNIT_PRICE = LINE_TOTAL (per line)
    """
    if not decisions:
        return []

    # Build word entries and group by label
    by_label: dict[str, list[dict[str, Any]]] = {}
    by_line: dict[int, dict[str, list[dict[str, Any]]]] = {}

    for d in decisions:
        entry = _build_decision_entry(d, word_lookup)
        label = entry.get("current_label", "")
        by_label.setdefault(label, []).append(entry)

        # Also group by line for line-item equations
        lid = entry.get("line_id")
        if lid is not None and label in ("QUANTITY", "UNIT_PRICE", "LINE_TOTAL"):
            by_line.setdefault(lid, {}).setdefault(label, []).append(entry)

    equations: list[dict[str, Any]] = []

    has_subtotal = "SUBTOTAL" in by_label
    has_grand_total = "GRAND_TOTAL" in by_label
    line_totals = by_label.get("LINE_TOTAL", [])
    subtotals = by_label.get("SUBTOTAL", [])
    taxes = by_label.get("TAX", [])
    tips = by_label.get("TIP", [])
    discounts = by_label.get("DISCOUNT", [])
    grand_totals = by_label.get("GRAND_TOTAL", [])

    # --- LINE_ITEM_BALANCED: QUANTITY × UNIT_PRICE = LINE_TOTAL per line ---
    for lid, labels in sorted(by_line.items()):
        qtys = labels.get("QUANTITY", [])
        ups = labels.get("UNIT_PRICE", [])
        lts = labels.get("LINE_TOTAL", [])
        if qtys and ups and lts:
            qty_val = _parse_numeric(qtys[0].get("word_text", ""))
            up_val = _parse_numeric(ups[0].get("word_text", ""))
            lt_val = _parse_numeric(lts[0].get("word_text", ""))
            expected = qty_val * up_val if qty_val is not None and up_val is not None else None
            diff = round(lt_val - expected, 2) if lt_val is not None and expected is not None else None
            involved = qtys + ups + lts
            equations.append({
                "issue_type": "LINE_ITEM_BALANCED",
                "description": (
                    f"Line {lid}: LINE_TOTAL ({lts[0]['word_text']}) "
                    f"= QTY ({qtys[0]['word_text']}) × UNIT_PRICE ({ups[0]['word_text']})"
                ),
                "expected_value": expected,
                "actual_value": lt_val,
                "difference": diff,
                "involved_words": involved,
            })

    # --- SUBTOTAL = sum(LINE_TOTAL) ---
    if has_subtotal and line_totals:
        lt_sum = sum(_parse_numeric(w["word_text"]) or 0 for w in line_totals)
        discount_sum = sum(_parse_numeric(w["word_text"]) or 0 for w in discounts)
        expected = round(lt_sum - discount_sum, 2)
        st_val = _parse_numeric(subtotals[0]["word_text"]) if subtotals else None
        diff = round(st_val - expected, 2) if st_val is not None else None
        lt_desc = " + ".join(w["word_text"] for w in line_totals)
        desc = f"SUBTOTAL ({subtotals[0]['word_text']}) = sum(LINE_TOTAL) ({lt_desc})"
        if discounts:
            desc += f" - DISCOUNT ({discounts[0]['word_text']})"
        equations.append({
            "issue_type": "SUBTOTAL",
            "description": desc,
            "expected_value": expected,
            "actual_value": st_val,
            "difference": diff,
            "involved_words": subtotals + line_totals + discounts,
        })

    # --- GRAND_TOTAL equation ---
    if has_grand_total:
        gt_val = _parse_numeric(grand_totals[0]["word_text"]) if grand_totals else None
        tax_sum = sum(_parse_numeric(w["word_text"]) or 0 for w in taxes)
        tip_sum = sum(_parse_numeric(w["word_text"]) or 0 for w in tips)
        discount_sum = sum(_parse_numeric(w["word_text"]) or 0 for w in discounts)

        if has_subtotal:
            # GRAND_TOTAL = SUBTOTAL + TAX + TIP - DISCOUNT
            st_val = _parse_numeric(subtotals[0]["word_text"]) if subtotals else 0
            expected = round((st_val or 0) + tax_sum + tip_sum - discount_sum, 2)
            parts = [f"SUBTOTAL ({subtotals[0]['word_text']})"] if subtotals else []
            if taxes:
                parts.append(f"TAX ({taxes[0]['word_text']})")
            if tips:
                parts.append(f"TIP ({tips[0]['word_text']})")
            desc = f"GRAND_TOTAL ({grand_totals[0]['word_text']}) = {' + '.join(parts)}"
            if discounts:
                desc += f" - DISCOUNT ({discounts[0]['word_text']})"
            issue_type = "GRAND_TOTAL"
            involved = grand_totals + subtotals + taxes + tips + discounts
        else:
            # GRAND_TOTAL_DIRECT = sum(LINE_TOTAL) + TAX + TIP - DISCOUNT
            lt_sum = sum(_parse_numeric(w["word_text"]) or 0 for w in line_totals)
            expected = round(lt_sum + tax_sum + tip_sum - discount_sum, 2)
            lt_desc = " + ".join(w["word_text"] for w in line_totals) if line_totals else "0"
            parts = [f"sum(LINE_TOTAL) ({lt_desc})"]
            if taxes:
                parts.append(f"TAX ({taxes[0]['word_text']})")
            if tips:
                parts.append(f"TIP ({tips[0]['word_text']})")
            desc = f"GRAND_TOTAL ({grand_totals[0]['word_text']}) = {' + '.join(parts)}"
            if discounts:
                desc += f" - DISCOUNT ({discounts[0]['word_text']})"
            issue_type = "GRAND_TOTAL_DIRECT"
            involved = grand_totals + line_totals + taxes + tips + discounts

        diff = round(gt_val - expected, 2) if gt_val is not None else None
        equations.append({
            "issue_type": issue_type,
            "description": desc,
            "expected_value": expected,
            "actual_value": gt_val,
            "difference": diff,
            "involved_words": involved,
        })

    # --- HAS_TOTAL: only when no other equations were built ---
    if not equations and has_grand_total:
        gt_val = _parse_numeric(grand_totals[0]["word_text"]) if grand_totals else None
        equations.append({
            "issue_type": "HAS_TOTAL",
            "description": f"GRAND_TOTAL = {grand_totals[0]['word_text']}" if grand_totals else "GRAND_TOTAL",
            "expected_value": gt_val,
            "actual_value": gt_val,
            "difference": 0,
            "involved_words": grand_totals,
        })

    return equations


def _build_equation_summary(equations: list[dict[str, Any]]) -> dict[str, Any]:
    """Build summary stats from equation list."""
    has_invalid = False
    has_needs_review = False
    for eq in equations:
        for w in eq.get("involved_words", []):
            decision = (w.get("decision") or "").upper()
            if decision == "INVALID":
                has_invalid = True
            elif decision == "NEEDS_REVIEW":
                has_needs_review = True
    return {
        "total_equations": len(equations),
        "has_invalid": has_invalid,
        "has_needs_review": has_needs_review,
    }


def build_financial_math_entry(
    unified_row: dict[str, Any],
    data_row: dict[str, Any] | None,
    lookup_row: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a single financial-math cache entry for one receipt."""
    financial_decisions = unified_row.get("financial_all_decisions")
    if not isinstance(financial_decisions, list):
        financial_decisions = []

    if not financial_decisions:
        return None

    # Word lookup from data row
    raw_words = data_row.get("words", []) if data_row else []
    word_lookup = _build_word_lookup(raw_words)

    equations = _build_equations(financial_decisions, word_lookup)
    summary = _build_equation_summary(equations)

    cdn, width, height = _build_cdn_fields(lookup_row)

    return {
        "image_id": unified_row.get("image_id"),
        "receipt_id": unified_row.get("receipt_id"),
        "merchant_name": unified_row.get("merchant_name"),
        "trace_id": unified_row.get("trace_id", ""),
        "equations": equations,
        "summary": summary,
        "width": width,
        "height": height,
        **cdn,
    }


# ---------------------------------------------------------------------------
# Within-receipt cache builder
# ---------------------------------------------------------------------------


def _split_metadata_decisions(
    all_decisions: list[dict],
    word_lookup: dict[tuple[int, int], dict],
) -> tuple[list[dict], list[dict]]:
    """Split metadata decisions into place and format groups."""
    place: list[dict] = []
    fmt: list[dict] = []
    for d in all_decisions:
        issue = d.get("issue", {})
        label = (issue.get("current_label") or "").upper()
        entry = _build_decision_entry(d, word_lookup)
        if label in PLACE_LABELS:
            place.append(entry)
        elif label in FORMAT_LABELS:
            fmt.append(entry)
    return place, fmt


def _build_decision_summary(decisions: list[dict]) -> dict[str, int]:
    """Count V/I/R decisions."""
    total = len(decisions)
    valid = sum(1 for d in decisions if (d.get("decision") or "").upper() == "VALID")
    invalid = sum(1 for d in decisions if (d.get("decision") or "").upper() == "INVALID")
    needs_review = sum(
        1 for d in decisions if (d.get("decision") or "").upper() == "NEEDS_REVIEW"
    )
    return {
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "needs_review": needs_review,
    }


def _extract_place_info(place: dict | None) -> dict[str, Any] | None:
    """Extract relevant Place fields for the frontend card."""
    if not place:
        return None
    return {
        "merchant_name": place.get("merchant_name"),
        "formatted_address": place.get("formatted_address"),
        "phone_number": place.get("phone_number"),
        "website": place.get("website"),
        "maps_url": place.get("maps_url"),
        "validation_status": place.get("validation_status"),
        "confidence": place.get("confidence"),
        "business_status": place.get("business_status"),
        "latitude": place.get("latitude"),
        "longitude": place.get("longitude"),
    }


def _build_word_list(words: list[dict]) -> list[dict[str, Any]]:
    """Build simplified word list with bboxes for frontend overlay."""
    result: list[dict[str, Any]] = []
    for w in words:
        result.append({
            "text": w.get("text", ""),
            "label": w.get("label"),
            "line_id": w.get("line_id"),
            "word_id": w.get("word_id"),
            "bbox": _extract_bbox(w),
        })
    return result


def build_within_receipt_entry(
    unified_row: dict[str, Any],
    data_row: dict[str, Any] | None,
    lookup_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a single within-receipt cache entry for one receipt."""
    # Words and bbox lookup
    raw_words = data_row.get("words", []) if data_row else []
    word_lookup = _build_word_lookup(raw_words)

    # Place data
    place_raw = data_row.get("place") if data_row else None
    if isinstance(place_raw, str):
        try:
            place_raw = json.loads(place_raw)
        except (json.JSONDecodeError, TypeError):
            place_raw = None
    place_info = _extract_place_info(place_raw)

    # Metadata decisions -> place + format
    metadata_decisions = unified_row.get("metadata_all_decisions")
    if not isinstance(metadata_decisions, list):
        metadata_decisions = []
    place_decisions, format_decisions = _split_metadata_decisions(
        metadata_decisions, word_lookup
    )

    # Financial decisions -> equations
    financial_decisions = unified_row.get("financial_all_decisions")
    if not isinstance(financial_decisions, list):
        financial_decisions = []
    equations = _build_equations(financial_decisions, word_lookup)

    # Duration seconds
    metadata_duration = unified_row.get("metadata_duration_seconds")
    financial_duration = unified_row.get("financial_duration_seconds")

    # Split metadata duration proportionally
    if metadata_duration and (place_decisions or format_decisions):
        total_meta = len(place_decisions) + len(format_decisions)
        if total_meta > 0:
            place_duration = metadata_duration * len(place_decisions) / total_meta
            format_duration = metadata_duration * len(format_decisions) / total_meta
        else:
            place_duration = metadata_duration / 2
            format_duration = metadata_duration / 2
    else:
        place_duration = None
        format_duration = None

    cdn, width, height = _build_cdn_fields(lookup_row)

    return {
        "image_id": unified_row.get("image_id"),
        "receipt_id": unified_row.get("receipt_id"),
        "merchant_name": unified_row.get("merchant_name"),
        "trace_id": unified_row.get("trace_id", ""),
        "place_validation": {
            "place": place_info,
            "decisions": place_decisions,
            "summary": _build_decision_summary(place_decisions),
            "duration_seconds": place_duration,
            "is_llm": True,
        },
        "format_validation": {
            "decisions": format_decisions,
            "summary": _build_decision_summary(format_decisions),
            "duration_seconds": format_duration,
            "is_llm": True,
        },
        "financial_math": {
            "equations": equations,
            "summary": _build_equation_summary(equations),
            "duration_seconds": financial_duration,
            "is_llm": True,
        },
        "words": _build_word_list(raw_words),
        "width": width,
        "height": height,
        **cdn,
    }


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Build viz-cache from evaluator S3 outputs."""
    logger.info("Event: %s", json.dumps(event))

    execution_id = event["execution_id"]
    batch_bucket = event["batch_bucket"]
    cache_bucket = event["cache_bucket"]

    t0 = time.time()

    # 1. List all keys from the three source prefixes in parallel
    unified_prefix = f"unified/{execution_id}/"
    data_prefix = f"data/{execution_id}/"
    lookup_prefix = f"receipts_lookup/{execution_id}/"

    with ThreadPoolExecutor(max_workers=3) as executor:
        f_unified = executor.submit(_list_keys, batch_bucket, unified_prefix)
        f_data = executor.submit(_list_keys, batch_bucket, data_prefix)
        f_lookup = executor.submit(_list_keys, batch_bucket, lookup_prefix)

    unified_keys = f_unified.result()
    data_keys = f_data.result()
    lookup_keys = f_lookup.result()

    logger.info(
        "Found %d unified, %d data, %d lookup keys",
        len(unified_keys),
        len(data_keys),
        len(lookup_keys),
    )

    if not unified_keys:
        logger.warning("No unified results found for %s", execution_id)
        return {
            "status": "no_data",
            "execution_id": execution_id,
            "financial_math_count": 0,
            "within_receipt_count": 0,
        }

    # 2. Read all files in parallel
    all_keys = unified_keys + data_keys + lookup_keys
    logger.info("Reading %d total S3 files", len(all_keys))

    all_data = _read_all_parallel(batch_bucket, all_keys)

    # Index by (image_id, receipt_id)
    unified_lookup: dict[tuple[str, int], dict] = {}
    data_lookup: dict[tuple[str, int], dict] = {}
    receipt_lookup: dict[tuple[str, int], dict] = {}

    for item in all_data:
        image_id = item.get("image_id")
        receipt_id = item.get("receipt_id")
        if not image_id or receipt_id is None:
            continue
        key = (str(image_id), int(receipt_id))

        # Classify by content: unified rows have decision fields,
        # lookup rows have cdn_s3_key, data rows have words
        if "financial_all_decisions" in item or "metadata_all_decisions" in item:
            unified_lookup[key] = item
        elif "cdn_s3_key" in item and "words" not in item:
            receipt_lookup[key] = item
        else:
            data_lookup[key] = item

    logger.info(
        "Indexed: %d unified, %d data, %d lookup",
        len(unified_lookup),
        len(data_lookup),
        len(receipt_lookup),
    )

    # 3. Build cache entries
    financial_math_entries: list[tuple[str, dict]] = []  # (key, data)
    within_receipt_entries: list[tuple[str, dict]] = []

    for receipt_key, urow in unified_lookup.items():
        image_id, receipt_id = receipt_key
        data_row = data_lookup.get(receipt_key)
        lookup_row = receipt_lookup.get(receipt_key)

        # Financial math (only for receipts with financial decisions)
        fm_entry = build_financial_math_entry(urow, data_row, lookup_row)
        if fm_entry:
            s3_name = f"financial-math/{image_id}_{receipt_id}.json"
            financial_math_entries.append((s3_name, fm_entry))

        # Within-receipt (for all receipts)
        wr_entry = build_within_receipt_entry(urow, data_row, lookup_row)
        s3_name = f"within-receipt/{image_id}_{receipt_id}.json"
        within_receipt_entries.append((s3_name, wr_entry))

    logger.info(
        "Built %d financial-math, %d within-receipt entries",
        len(financial_math_entries),
        len(within_receipt_entries),
    )

    # 4. Write all cache entries + metadata in parallel
    now_iso = datetime.now(timezone.utc).isoformat()

    fm_metadata = {
        "execution_id": execution_id,
        "cached_at": now_iso,
        "total_receipts": len(financial_math_entries),
    }
    wr_metadata = {
        "execution_id": execution_id,
        "cached_at": now_iso,
        "total_receipts": len(within_receipt_entries),
    }

    write_tasks: list[tuple[str, dict]] = []
    write_tasks.extend(financial_math_entries)
    write_tasks.append(("financial-math/metadata.json", fm_metadata))
    write_tasks.extend(within_receipt_entries)
    write_tasks.append(("within-receipt/metadata.json", wr_metadata))

    logger.info("Writing %d files to s3://%s/", len(write_tasks), cache_bucket)

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            executor.submit(_write_json, cache_bucket, key, data): key
            for key, data in write_tasks
        }
        errors = 0
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                errors += 1
                logger.warning(
                    "Failed to write %s", futures[future], exc_info=True
                )

    elapsed = time.time() - t0
    logger.info(
        "Completed in %.1fs: %d financial-math, %d within-receipt, %d errors",
        elapsed,
        len(financial_math_entries),
        len(within_receipt_entries),
        errors,
    )

    return {
        "status": "completed",
        "execution_id": execution_id,
        "financial_math_count": len(financial_math_entries),
        "within_receipt_count": len(within_receipt_entries),
        "write_errors": errors,
        "duration_seconds": round(elapsed, 1),
    }

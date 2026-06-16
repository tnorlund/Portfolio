"""Lambda to build viz-cache directly from evaluator S3 outputs.

Replaces the LangSmith export + EMR Spark pipeline. Reads three S3 prefixes
written by unified_receipt_evaluator.py and writes three viz-cache prefixes
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
    {cache_bucket}/receipt-health/{image_id}_{receipt_id}.json
    {cache_bucket}/receipt-health/metadata.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

RECEIPT_HEALTH_LEDGER_KEY = "receipt-health/issues/ledger.json"
RECEIPT_HEALTH_ELIGIBLE_KEY = "receipt-health/issues/eligible.json"
RECEIPT_HEALTH_CURRENT_KEY = "receipt-health/issues/current.json"
MAX_LEDGER_ATTEMPTS = 2
PREFLIGHT_CLASSIFIER_VERSION = "receipt-health-preflight-v2"
AUTOMATION_READY_PREFLIGHT_CLASSES = frozenset({"safe_exact_plan"})
PREFLIGHT_SUMMARY_FIELDS = frozenset(
    {"classification", "lane", "root_cause"}
)

STRICT_MONEY_RE = re.compile(r"^\$?\d{1,4}(?:,\d{3})*\.\d{2}$")
STRICT_MONEY_INLINE_RE = re.compile(
    r"(?<![\w.])\$?\d{1,4}(?:,\d{3})*\.\d{2}(?![\w.])"
)
PRICE_FRAGMENT_RE = re.compile(
    r"(?<!\w)(?:\$?\d{1,3}\.|[,.]\d{2}|\d{1,3}/\d+\.\d{2})(?!\w)"
)
AMOUNTISH_RE = re.compile(r"^\$?[\d.,]+$")
MISSING_LINE_TOTAL_RE = re.compile(
    r"sum\(LINE_TOTAL\)\s*\(\s*\$?0(?:\.0{1,2})?\s*\)",
    re.IGNORECASE,
)
PERCENT_AMOUNT_RE = re.compile(
    r"\b\d{1,3}(?:\.\d+)?\s*%.*(?:\$|\btip\b|\btotal\b)",
    re.IGNORECASE,
)
NEGATIVE_AMOUNT_RE = re.compile(r"-\s*\$?\d")

LINE_TOTAL_CONFLICT_LABELS = frozenset(
    {
        "GRAND_TOTAL",
        "SUBTOTAL",
        "TAX",
        "TIP",
        "DISCOUNT",
        "CASH_BACK",
        "PAYMENT_METHOD",
        "LOYALTY_ID",
    }
)
FINANCIAL_AMOUNT_LABELS = frozenset(
    {
        "LINE_TOTAL",
        "UNIT_PRICE",
        "SUBTOTAL",
        "TAX",
        "TIP",
        "DISCOUNT",
        "GRAND_TOTAL",
    }
)

# ---------------------------------------------------------------------------
# Label sets for within-receipt verification
# ---------------------------------------------------------------------------

PLACE_LABELS = frozenset(
    {
        "MERCHANT_NAME",
        "ADDRESS_LINE",
        "PHONE_NUMBER",
        "WEBSITE",
        "STORE_HOURS",
    }
)

FORMAT_LABELS = frozenset(
    {
        "DATE",
        "TIME",
        "PAYMENT_METHOD",
        "COUPON",
        "LOYALTY_ID",
    }
)

PREFLIGHT_FINGERPRINT_LABELS = (
    FINANCIAL_AMOUNT_LABELS | PLACE_LABELS | FORMAT_LABELS
)


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
            "cdn_small_s3_key",
            "cdn_small_webp_s3_key",
            "cdn_small_avif_s3_key",
            "cdn_thumbnail_s3_key",
            "cdn_thumbnail_webp_s3_key",
            "cdn_thumbnail_avif_s3_key",
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

# Service/terminal receipts emit pre-formed equations from text-scan rather
# than per-word label decisions. The financial_subagent stamps issue_type
# directly on these and they need no reconstruction.
TEXT_SCAN_ISSUE_TYPES = frozenset({"HAS_TOTAL", "TOTAL_CHECK", "TIP_CHECK"})


def _parse_numeric(text: str) -> float | None:
    """Parse a numeric value from word text, stripping currency symbols."""
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _extract_receipt_type(decisions: list[dict]) -> str:
    """Detect receipt_type from financial decisions. Defaults to 'itemized'."""
    if not decisions:
        return "itemized"
    rt = decisions[0].get("receipt_type")
    if rt in ("service", "terminal"):
        return rt
    return "itemized"


def _build_text_scan_equations(
    decisions: list[dict],
    word_lookup: dict[tuple[int, int], dict],
) -> list[dict[str, Any]]:
    """Wrap pre-formed text-scan issues as equations for service/terminal receipts."""
    equations: list[dict[str, Any]] = []
    for d in decisions:
        issue = d.get("issue", {})
        issue_type = issue.get("issue_type")
        if issue_type not in TEXT_SCAN_ISSUE_TYPES:
            continue
        entry = _build_decision_entry(d, word_lookup)
        equations.append(
            {
                "issue_type": issue_type,
                "description": issue.get("description", ""),
                "expected_value": issue.get("expected_value"),
                "actual_value": issue.get("actual_value"),
                "difference": issue.get("difference"),
                "involved_words": [entry],
            }
        )
    return equations


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

    Service/terminal receipts skip reconstruction — their decisions already
    carry issue_type (HAS_TOTAL/TOTAL_CHECK/TIP_CHECK) from text-scan.
    """
    if not decisions:
        return []

    if _extract_receipt_type(decisions) in ("service", "terminal"):
        return _build_text_scan_equations(decisions, word_lookup)

    # Build word entries and group by label
    by_label: dict[str, list[dict[str, Any]]] = {}
    by_line: dict[int, dict[str, list[dict[str, Any]]]] = {}

    for d in decisions:
        entry = _build_decision_entry(d, word_lookup)
        label = entry.get("current_label", "")
        by_label.setdefault(label, []).append(entry)

        # Also group by line for line-item equations
        lid = entry.get("line_id")
        if lid is not None and label in (
            "QUANTITY",
            "UNIT_PRICE",
            "LINE_TOTAL",
        ):
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
            expected = (
                qty_val * up_val
                if qty_val is not None and up_val is not None
                else None
            )
            diff = (
                round(lt_val - expected, 2)
                if lt_val is not None and expected is not None
                else None
            )
            involved = qtys + ups + lts
            equations.append(
                {
                    "issue_type": "LINE_ITEM_BALANCED",
                    "description": (
                        f"Line {lid}: LINE_TOTAL ({lts[0]['word_text']}) "
                        f"= QTY ({qtys[0]['word_text']}) × UNIT_PRICE ({ups[0]['word_text']})"
                    ),
                    "expected_value": expected,
                    "actual_value": lt_val,
                    "difference": diff,
                    "involved_words": involved,
                }
            )

    # --- SUBTOTAL = sum(LINE_TOTAL) ---
    if has_subtotal and line_totals:
        lt_sum = sum(_parse_numeric(w["word_text"]) or 0 for w in line_totals)
        discount_sum = sum(
            _parse_numeric(w["word_text"]) or 0 for w in discounts
        )
        expected = round(lt_sum - discount_sum, 2)
        st_val = (
            _parse_numeric(subtotals[0]["word_text"]) if subtotals else None
        )
        diff = round(st_val - expected, 2) if st_val is not None else None
        lt_desc = " + ".join(w["word_text"] for w in line_totals)
        desc = f"SUBTOTAL ({subtotals[0]['word_text']}) = sum(LINE_TOTAL) ({lt_desc})"
        if discounts:
            desc += f" - DISCOUNT ({discounts[0]['word_text']})"
        equations.append(
            {
                "issue_type": "SUBTOTAL",
                "description": desc,
                "expected_value": expected,
                "actual_value": st_val,
                "difference": diff,
                "involved_words": subtotals + line_totals + discounts,
            }
        )

    # --- GRAND_TOTAL equation ---
    if has_grand_total:
        gt_val = (
            _parse_numeric(grand_totals[0]["word_text"])
            if grand_totals
            else None
        )
        tax_sum = sum(_parse_numeric(w["word_text"]) or 0 for w in taxes)
        tip_sum = sum(_parse_numeric(w["word_text"]) or 0 for w in tips)
        discount_sum = sum(
            _parse_numeric(w["word_text"]) or 0 for w in discounts
        )

        if has_subtotal:
            # GRAND_TOTAL = SUBTOTAL + TAX + TIP - DISCOUNT
            st_val = (
                _parse_numeric(subtotals[0]["word_text"]) if subtotals else 0
            )
            expected = round(
                (st_val or 0) + tax_sum + tip_sum - discount_sum, 2
            )
            parts = (
                [f"SUBTOTAL ({subtotals[0]['word_text']})"]
                if subtotals
                else []
            )
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
            lt_sum = sum(
                _parse_numeric(w["word_text"]) or 0 for w in line_totals
            )
            expected = round(lt_sum + tax_sum + tip_sum - discount_sum, 2)
            lt_desc = (
                " + ".join(w["word_text"] for w in line_totals)
                if line_totals
                else "0"
            )
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
        equations.append(
            {
                "issue_type": issue_type,
                "description": desc,
                "expected_value": expected,
                "actual_value": gt_val,
                "difference": diff,
                "involved_words": involved,
            }
        )

    # --- HAS_TOTAL: only when no other equations were built ---
    if not equations and has_grand_total:
        gt_val = (
            _parse_numeric(grand_totals[0]["word_text"])
            if grand_totals
            else None
        )
        equations.append(
            {
                "issue_type": "HAS_TOTAL",
                "description": (
                    f"GRAND_TOTAL = {grand_totals[0]['word_text']}"
                    if grand_totals
                    else "GRAND_TOTAL"
                ),
                "expected_value": gt_val,
                "actual_value": gt_val,
                "difference": 0,
                "involved_words": grand_totals,
            }
        )

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
        "receipt_type": _extract_receipt_type(financial_decisions),
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
    valid = sum(
        1 for d in decisions if (d.get("decision") or "").upper() == "VALID"
    )
    invalid = sum(
        1 for d in decisions if (d.get("decision") or "").upper() == "INVALID"
    )
    needs_review = sum(
        1
        for d in decisions
        if (d.get("decision") or "").upper() == "NEEDS_REVIEW"
    )
    return {
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "needs_review": needs_review,
    }


def _check_status_from_summary(summary: dict[str, int]) -> str:
    """Convert a decision summary to a frontend check status."""
    total = int(summary.get("total") or 0)
    if total == 0:
        return "not_applicable"
    if int(summary.get("invalid") or 0) > 0:
        return "fail"
    if int(summary.get("needs_review") or 0) > 0:
        return "review"
    return "pass"


def _equation_has_mismatch(equation: dict[str, Any]) -> bool:
    """Return true when an equation materially fails numeric reconciliation."""
    diff = equation.get("difference")
    if diff is None:
        return False
    try:
        return abs(float(diff)) > 0.05
    except (TypeError, ValueError):
        return False


def _financial_status(financial_math: dict[str, Any]) -> str:
    """Derive a health status from financial equations and word decisions."""
    equations = financial_math.get("equations") or []
    if not equations:
        return "not_applicable"

    summary = financial_math.get("summary") or {}
    if summary.get("has_invalid") or any(
        _equation_has_mismatch(eq) for eq in equations
    ):
        return "fail"
    if summary.get("has_needs_review"):
        return "review"
    return "pass"


def _status_priority(status: str) -> int:
    """Order health statuses from least to most severe."""
    return {
        "not_applicable": 0,
        "pass": 1,
        "review": 2,
        "fail": 3,
    }.get(status, 0)


def _overall_status(checks: list[dict[str, Any]]) -> str:
    """Return the most severe applicable status across checks."""
    if not checks:
        return "not_applicable"
    return max(
        (check.get("status", "not_applicable") for check in checks),
        key=_status_priority,
    )


def _check_counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    """Count check statuses for the receipt health summary."""
    statuses = ["pass", "review", "fail", "not_applicable"]
    return {
        status: sum(1 for check in checks if check.get("status") == status)
        for status in statuses
    }


def _issue_count_for_check(check: dict[str, Any]) -> int:
    """Count concrete issues represented by one check."""
    status = check.get("status")
    if status == "pass":
        return 0
    summary = check.get("summary") or {}
    if "invalid" in summary or "needs_review" in summary:
        return int(summary.get("invalid") or 0) + int(
            summary.get("needs_review") or 0
        )
    if "mismatched_equations" in summary:
        return int(summary.get("mismatched_equations") or 0)
    return 1 if status in {"fail", "review"} else 0


def _build_primary_issues(
    checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build compact issue list ordered by severity for the frontend."""
    issues: list[dict[str, Any]] = []
    for check in checks:
        if check.get("status") not in {"fail", "review"}:
            continue
        issue_count = _issue_count_for_check(check)
        result = check.get("result") or "Needs review"
        issues.append(
            {
                "check_id": check.get("id"),
                "status": check.get("status"),
                "title": check.get("title"),
                "message": result,
                "issue_count": issue_count,
                "summary": result,
            }
        )
    return sorted(
        issues,
        key=lambda item: (
            -_status_priority(str(item.get("status"))),
            -int(item.get("issue_count") or 0),
        ),
    )


def build_receipt_health_entry(
    within_entry: dict[str, Any],
) -> dict[str, Any]:
    """Build unified receipt-health cache entry from within-receipt data."""
    place_validation = within_entry["place_validation"]
    format_validation = within_entry["format_validation"]
    financial_math = within_entry["financial_math"]

    place_summary = place_validation.get("summary") or {}
    format_summary = format_validation.get("summary") or {}
    equations = financial_math.get("equations") or []
    mismatched_equations = sum(
        1 for equation in equations if _equation_has_mismatch(equation)
    )

    checks = [
        {
            "id": "merchant_identity",
            "title": "Merchant Identity",
            "question": (
                "Do the merchant, address, phone, and website words agree "
                "with the stored ReceiptPlace?"
            ),
            "status": _check_status_from_summary(place_summary),
            "validator": "place_validation",
            "is_llm": place_validation.get("is_llm", False),
            "duration_seconds": place_validation.get("duration_seconds"),
            "summary": place_summary,
            "result": (
                f"{place_summary.get('valid', 0)} valid, "
                f"{place_summary.get('invalid', 0)} invalid, "
                f"{place_summary.get('needs_review', 0)} review"
            ),
            "evidence_count": len(place_validation.get("decisions") or []),
            "what_it_validates": [
                "merchant name",
                "street address",
                "phone number",
                "website or store hours",
            ],
        },
        {
            "id": "receipt_format",
            "title": "Receipt Format",
            "question": (
                "Do date, time, payment, coupon, and loyalty labels make "
                "sense within this receipt?"
            ),
            "status": _check_status_from_summary(format_summary),
            "validator": "format_validation",
            "is_llm": format_validation.get("is_llm", False),
            "duration_seconds": format_validation.get("duration_seconds"),
            "summary": format_summary,
            "result": (
                f"{format_summary.get('valid', 0)} valid, "
                f"{format_summary.get('invalid', 0)} invalid, "
                f"{format_summary.get('needs_review', 0)} review"
            ),
            "evidence_count": len(format_validation.get("decisions") or []),
            "what_it_validates": [
                "date",
                "time",
                "payment method",
                "coupon",
                "loyalty ID",
            ],
        },
        {
            "id": "financial_math",
            "title": "Financial Math",
            "question": (
                "Do line totals, subtotal, tax, tip, discounts, and grand "
                "total reconcile?"
            ),
            "status": _financial_status(financial_math),
            "validator": "financial_math",
            "is_llm": False,
            "duration_seconds": financial_math.get("duration_seconds"),
            "summary": {
                **(financial_math.get("summary") or {}),
                "mismatched_equations": mismatched_equations,
            },
            "result": (
                f"{len(equations)} equations, "
                f"{mismatched_equations} mismatches"
            ),
            "evidence_count": sum(
                len(eq.get("involved_words") or []) for eq in equations
            ),
            "what_it_validates": [
                "line item arithmetic",
                "subtotal",
                "tax",
                "tip",
                "discount",
                "grand total",
            ],
        },
    ]

    status = _overall_status(checks)
    counts = _check_counts(checks)
    issue_count = sum(_issue_count_for_check(check) for check in checks)

    return {
        "image_id": within_entry.get("image_id"),
        "receipt_id": within_entry.get("receipt_id"),
        "merchant_name": within_entry.get("merchant_name"),
        "trace_id": within_entry.get("trace_id"),
        "receipt_type": within_entry.get("receipt_type"),
        "overall_status": status,
        "summary": {
            "total_checks": len(checks),
            "passed": counts["pass"],
            "needs_review": counts["review"],
            "failed": counts["fail"],
            "not_applicable": counts["not_applicable"],
            "issue_count": issue_count,
        },
        "checks": checks,
        "primary_issues": _build_primary_issues(checks),
        "place_validation": place_validation,
        "format_validation": format_validation,
        "financial_math": financial_math,
        "words": within_entry.get("words", []),
        "width": within_entry.get("width", 0),
        "height": within_entry.get("height", 0),
        **{
            key: value
            for key, value in within_entry.items()
            if key.startswith("cdn_")
        },
    }


def _stable_issue_hash(parts: dict[str, Any]) -> str:
    """Build a short deterministic hash for an issue fingerprint."""
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _word_ref(word: dict[str, Any]) -> dict[str, Any]:
    """Return only stable word identity fields for issue fingerprinting."""
    return {
        "line_id": word.get("line_id"),
        "word_id": word.get("word_id"),
        "label": word.get("current_label") or word.get("label"),
    }


def _compact_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Keep the fields a cleanup agent needs without duplicating whole receipts."""
    result = {
        "line_id": decision.get("line_id"),
        "word_id": decision.get("word_id"),
        "word_text": decision.get("word_text"),
        "current_label": decision.get("current_label"),
        "decision": decision.get("decision"),
        "confidence": decision.get("confidence"),
        "reasoning": decision.get("reasoning"),
        "bbox": decision.get("bbox"),
    }
    if decision.get("suggested_label"):
        result["suggested_label"] = decision.get("suggested_label")
    return result


def _issue_base(
    receipt: dict[str, Any],
    check: dict[str, Any],
    *,
    execution_id: str,
    observed_at: str,
    status: str,
    issue_type: str,
    message: str,
    fingerprint_parts: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the shared issue payload used by run snapshots and the ledger."""
    issue_hash = _stable_issue_hash(fingerprint_parts)
    image_id = str(receipt.get("image_id"))
    receipt_id = receipt.get("receipt_id")
    check_id = str(check.get("id"))
    return {
        "issue_id": f"{image_id}:{receipt_id}:{check_id}:{issue_hash}",
        "fingerprint": issue_hash,
        "execution_id": execution_id,
        "observed_at": observed_at,
        "image_id": receipt.get("image_id"),
        "receipt_id": receipt_id,
        "merchant_name": receipt.get("merchant_name"),
        "receipt_type": receipt.get("receipt_type"),
        "check_id": check_id,
        "check_title": check.get("title"),
        "validator": check.get("validator"),
        "status": status,
        "issue_type": issue_type,
        "message": message,
        "result": check.get("result"),
        "evidence": evidence,
    }


def _normalized_status(value: Any) -> str:
    """Return a normalized validation status string."""
    if value is None:
        return "NONE"
    if hasattr(value, "value"):
        value = value.value
    return str(value).upper()


def _money_cents(text: Any, *, strict: bool = False) -> int | None:
    """Parse receipt amount text into cents."""
    if text is None:
        return None
    raw = str(text).strip()
    if strict and not STRICT_MONEY_RE.fullmatch(raw):
        return None
    cleaned = raw.replace("$", "").replace(",", "")
    try:
        dollars, cents = cleaned.split(".", 1)
    except ValueError:
        if strict:
            return None
        try:
            return int(round(float(cleaned) * 100))
        except (TypeError, ValueError):
            return None
    if len(cents) < 2:
        cents = cents.ljust(2, "0")
    if len(cents) > 2:
        cents = cents[:2]
    try:
        sign = -1 if dollars.startswith("-") else 1
        absolute_dollars = dollars.lstrip("+-")
        if absolute_dollars == "":
            return None
        return sign * (int(absolute_dollars) * 100 + int(cents))
    except ValueError:
        return None


def _amount_label(cents: int) -> str:
    """Format cents as display currency without adding locale behavior."""
    return f"{cents / 100:.2f}"


def _word_text_lookup(
    words: list[dict[str, Any]],
) -> dict[tuple[int, int], str]:
    """Build word text lookup from raw word rows."""
    lookup: dict[tuple[int, int], str] = {}
    for word in words:
        line_id = word.get("line_id")
        word_id = word.get("word_id")
        if line_id is None or word_id is None:
            continue
        lookup[(int(line_id), int(word_id))] = str(word.get("text") or "")
    return lookup


def _word_bbox(word: dict[str, Any]) -> dict[str, float]:
    """Return a normalized bbox dict from a raw word row."""
    bbox = word.get("bounding_box") or word.get("bbox") or {}
    return {
        "x": float(bbox.get("x") or 0.0),
        "y": float(bbox.get("y") or 0.0),
        "width": float(bbox.get("width") or 0.0),
        "height": float(bbox.get("height") or 0.0),
    }


def _group_words_into_visual_rows(
    words: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group OCR words into visual rows using Y coordinates.

    OCR line IDs can split one printed row into multiple line IDs when text
    and right-aligned amounts are far apart. This approximates the Chroma
    row_line_ids signal from raw word coordinates.
    """
    positioned: list[dict[str, Any]] = []
    heights: list[float] = []
    for word in words:
        line_id = word.get("line_id")
        word_id = word.get("word_id")
        if line_id is None or word_id is None:
            continue
        bbox = _word_bbox(word)
        center_y = bbox["y"] + bbox["height"] / 2
        positioned.append(
            {
                "line_id": int(line_id),
                "word_id": int(word_id),
                "text": str(word.get("text") or ""),
                "x": bbox["x"],
                "width": bbox["width"],
                "center_y": center_y,
                "height": bbox["height"],
            }
        )
        if bbox["height"] > 0:
            heights.append(bbox["height"])

    if not positioned:
        return []

    heights.sort()
    median_height = heights[len(heights) // 2] if heights else 0.012
    row_threshold = max(0.006, median_height * 0.65)
    rows: list[dict[str, Any]] = []
    for word in sorted(positioned, key=lambda item: -item["center_y"]):
        matched: dict[str, Any] | None = None
        for row in rows:
            if abs(float(row["center_y"]) - word["center_y"]) <= row_threshold:
                matched = row
                break
        if matched is None:
            rows.append(
                {
                    "center_y": word["center_y"],
                    "words": [word],
                }
            )
            continue

        matched["words"].append(word)
        matched["center_y"] = sum(
            float(item["center_y"]) for item in matched["words"]
        ) / len(matched["words"])

    result: list[dict[str, Any]] = []
    for idx, row in enumerate(sorted(rows, key=lambda item: -item["center_y"])):
        row_words = sorted(
            row["words"],
            key=lambda item: (float(item["x"]), int(item["word_id"])),
        )
        line_ids = sorted({int(item["line_id"]) for item in row_words})
        text = " ".join(item["text"] for item in row_words if item["text"])
        result.append(
            {
                "row_index": idx,
                "line_ids": line_ids,
                "text": text,
                "center_y": row["center_y"],
                "words": row_words,
            }
        )
    return result


def _section_for_row_text(text: str) -> str:
    """Classify one visual row into a coarse receipt section."""
    lowered = text.lower()
    if (
        "gratuity suggestion" in lowered
        or "suggested tip" in lowered
        or "add tips" in lowered
        or "add tip" in lowered
        or "custom tips" in lowered
        or "tip percentages are based" in lowered
    ):
        return "tip_suggestions"
    if re.search(r"\btip\s*:", lowered):
        return "tip_entry_area"
    if re.search(r"\bvoid(?:ed)?\b", lowered):
        return "void_discount"
    if re.search(r"\bdiscount\b|coupon|promo|reward", lowered):
        return "discount_or_negative"
    if re.search(
        r"\b(card|visa|mastercard|debit|credit|auth|approved|aid:|"
        r"entry method|transaction|invoice|sequence|app label|amount:)\b",
        lowered,
    ):
        return "payment_summary"
    if re.search(
        r"\bsubtotal\b|\btotal\b|balance due|amount due|change\b|tax\b",
        lowered,
    ):
        return "totals"
    if re.search(
        r"customer copy|merchant copy|signature|thank you|feedback|survey|"
        r"returns?|cashier|store:|pos:",
        lowered,
    ):
        return "footer"
    if NEGATIVE_AMOUNT_RE.search(lowered):
        return "discount_or_negative"
    return "item_or_header"


def _receipt_section_rows(
    words: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return visual rows with deterministic section labels."""
    rows = _group_words_into_visual_rows(words)
    in_tip_suggestions = False
    for idx, row in enumerate(rows):
        text = str(row.get("text") or "")
        section = _section_for_row_text(text)
        lowered = text.lower()
        if section == "tip_suggestions":
            in_tip_suggestions = True
        elif in_tip_suggestions and PERCENT_AMOUNT_RE.search(text):
            section = "tip_suggestions"
        elif in_tip_suggestions and re.search(
            r"customer copy|merchant copy|thank you|signature|"
            r"\bpayment\b|\bauth\b",
            lowered,
        ):
            in_tip_suggestions = False
        context = " ".join(
            str(candidate.get("text") or "")
            for candidate in rows[max(0, idx - 2) : min(len(rows), idx + 3)]
        ).lower()
        if (
            "voided item" in context
            and NEGATIVE_AMOUNT_RE.search(str(row.get("text") or ""))
        ):
            section = "void_discount"
        row["section"] = section
    return rows


def _issue_section_evidence(
    issue: dict[str, Any],
    words: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build section evidence for the words referenced by an issue."""
    rows = _receipt_section_rows(words)
    line_to_rows: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        for line_id in row.get("line_ids") or []:
            line_to_rows.setdefault(int(line_id), []).append(row)

    issue_line_ids = {
        int(word["line_id"])
        for word in _issue_involved_words(issue)
        if word.get("line_id") is not None
    }
    section_counts: dict[str, int] = {}
    context_section_counts: dict[str, int] = {}
    row_examples: list[dict[str, Any]] = []
    seen_rows: set[int] = set()
    seen_context_rows: set[int] = set()
    for line_id in sorted(issue_line_ids):
        for row in line_to_rows.get(line_id, []):
            row_index = int(row.get("row_index") or 0)
            if row_index in seen_rows:
                continue
            seen_rows.add(row_index)
            section = str(row.get("section") or "unknown")
            section_counts[section] = section_counts.get(section, 0) + 1
            for context_row in rows[
                max(0, row_index - 2) : min(len(rows), row_index + 3)
            ]:
                context_index = int(context_row.get("row_index") or 0)
                if context_index in seen_context_rows:
                    continue
                seen_context_rows.add(context_index)
                context_section = str(context_row.get("section") or "unknown")
                context_section_counts[context_section] = (
                    context_section_counts.get(context_section, 0) + 1
                )
            if len(row_examples) < 6:
                row_examples.append(
                    {
                        "row_index": row_index,
                        "line_ids": row.get("line_ids") or [],
                        "section": section,
                        "text": row.get("text") or "",
                    }
                )

    return {
        "issue_sections": section_counts,
        "context_sections": context_section_counts,
        "issue_rows": row_examples,
        "has_tip_suggestions": section_counts.get("tip_suggestions", 0) > 0,
        "has_tip_entry_area": (
            section_counts.get("tip_entry_area", 0) > 0
            or context_section_counts.get("tip_entry_area", 0) > 0
        ),
        "has_void_discount": section_counts.get("void_discount", 0) > 0,
        "has_payment_summary": (
            section_counts.get("payment_summary", 0) > 0
            or context_section_counts.get("payment_summary", 0) > 0
        ),
    }


def _with_section_evidence(
    evidence: dict[str, Any] | None,
    section_evidence: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge compact section evidence into a preflight evidence block."""
    if not section_evidence:
        return evidence
    return {
        **(evidence or {}),
        "section_evidence": section_evidence,
    }


def _price_tokens_for_row(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return clean and fragmented price-like tokens from a visual row."""
    clean_tokens: list[str] = []
    fragment_tokens: list[str] = []
    for word in row.get("words") or []:
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        if STRICT_MONEY_RE.fullmatch(text):
            clean_tokens.append(text)
            continue
        if PRICE_FRAGMENT_RE.search(text):
            fragment_tokens.append(text)
    return clean_tokens, fragment_tokens


def _line_item_amount_evidence(
    rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize visual item rows whose price tokens are incomplete."""
    first_total_index = len(rows)
    for row in rows:
        if row.get("section") in {"totals", "payment_summary"}:
            first_total_index = int(row.get("row_index") or 0)
            break

    candidate_lines = {
        int(record["line_id"])
        for record in candidates
        if record.get("line_id") is not None
    }
    item_rows: list[dict[str, Any]] = []
    for row in rows:
        row_index = int(row.get("row_index") or 0)
        if row_index >= first_total_index:
            continue
        if row.get("section") != "item_or_header":
            continue
        clean_tokens, fragment_tokens = _price_tokens_for_row(row)
        if not clean_tokens and not fragment_tokens:
            continue
        text = str(row.get("text") or "")
        if re.search(r"\b(?:phone|cashier|west|street|ave|road)\b", text, re.I):
            continue
        line_ids = [int(value) for value in row.get("line_ids") or []]
        item_rows.append(
            {
                "row_index": row_index,
                "line_ids": line_ids,
                "text": text,
                "clean_amount_tokens": clean_tokens[:4],
                "fragment_amount_tokens": fragment_tokens[:4],
                "has_labeled_line_total_candidate": bool(
                    candidate_lines.intersection(line_ids)
                ),
            }
        )

    clean_count = sum(
        1 for row in item_rows if row.get("clean_amount_tokens")
    )
    fragmented_count = sum(
        1 for row in item_rows if row.get("fragment_amount_tokens")
    )
    labeled_count = sum(
        1 for row in item_rows if row.get("has_labeled_line_total_candidate")
    )
    return {
        "item_amount_row_count": len(item_rows),
        "clean_amount_row_count": clean_count,
        "fragmented_amount_row_count": fragmented_count,
        "labeled_line_total_row_count": labeled_count,
        "rows": item_rows[:8],
    }


def _label_records(
    words: list[dict[str, Any]],
    labels: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join raw label rows to word text and parsed amounts."""
    texts = _word_text_lookup(words)
    records: list[dict[str, Any]] = []
    for label in labels:
        line_id = label.get("line_id")
        word_id = label.get("word_id")
        label_name = label.get("label")
        if line_id is None or word_id is None or not label_name:
            continue
        key = (int(line_id), int(word_id))
        text = texts.get(key, "")
        records.append(
            {
                "line_id": int(line_id),
                "word_id": int(word_id),
                "label": str(label_name).upper(),
                "validation_status": _normalized_status(
                    label.get("validation_status")
                ),
                "label_proposed_by": label.get("label_proposed_by"),
                "text": text,
                "amount_cents": _money_cents(text),
                "strict_amount_cents": _money_cents(text, strict=True),
            }
        )
    return records


def _records_by_word(
    records: list[dict[str, Any]],
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """Group label records by word identity."""
    by_word: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for record in records:
        key = (int(record["line_id"]), int(record["word_id"]))
        by_word.setdefault(key, []).append(record)
    return by_word


def _preflight_data_fingerprint(
    issue: dict[str, Any],
    records: list[dict[str, Any]],
    fingerprint_context: dict[str, Any] | None = None,
) -> str:
    """Fingerprint the data relevant to a preflight decision."""
    compact_records = [
        {
            "line_id": record.get("line_id"),
            "word_id": record.get("word_id"),
            "text": record.get("text"),
            "label": record.get("label"),
            "validation_status": record.get("validation_status"),
        }
        for record in records
        if record.get("label") in PREFLIGHT_FINGERPRINT_LABELS
    ]
    compact_records.sort(
        key=lambda item: (
            int(item.get("line_id") or 0),
            int(item.get("word_id") or 0),
            str(item.get("label") or ""),
        )
    )
    return _stable_issue_hash(
        {
            "classifier_version": PREFLIGHT_CLASSIFIER_VERSION,
            "issue_id": issue.get("issue_id"),
            "message": issue.get("message"),
            "records": compact_records,
            "context": fingerprint_context or {},
        }
    )


def _preflight(
    classification: str,
    *,
    summary: str,
    records: list[dict[str, Any]],
    issue: dict[str, Any],
    automation_lane: str = "none",
    lane: str | None = None,
    root_cause: str = "unclassified",
    recommended_next_step: str = "Do not apply automated label writes.",
    proposed_actions: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    fingerprint_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the persisted deterministic preflight block."""
    actions = proposed_actions or []
    resolved_lane = lane or automation_lane
    return {
        "version": PREFLIGHT_CLASSIFIER_VERSION,
        "classification": classification,
        "automation_lane": automation_lane,
        "lane": resolved_lane,
        "root_cause": root_cause,
        "recommended_next_step": recommended_next_step,
        "is_automation_ready": classification
        in AUTOMATION_READY_PREFLIGHT_CLASSES,
        "summary": summary,
        "proposed_actions": actions,
        "action_count": len(actions),
        "evidence": evidence or {},
        "data_fingerprint": _preflight_data_fingerprint(
            issue, records, fingerprint_context
        ),
    }


def _amounts_for_label(
    records: list[dict[str, Any]],
    label_name: str,
    *,
    valid_only: bool = True,
) -> list[dict[str, Any]]:
    """Return amount records for a label, optionally requiring VALID status."""
    result = []
    for record in records:
        if record.get("label") != label_name:
            continue
        if valid_only and record.get("validation_status") != "VALID":
            continue
        amount_cents = record.get("amount_cents")
        if amount_cents is None:
            continue
        result.append(record)
    return result


def _candidate_line_total_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Find invalid LINE_TOTAL records that are safe status-flip candidates."""
    by_word = _records_by_word(records)
    valid_grands = {
        record.get("amount_cents")
        for record in _amounts_for_label(records, "GRAND_TOTAL")
    }
    candidates: list[dict[str, Any]] = []
    rejected = 0
    for record in records:
        if record.get("label") != "LINE_TOTAL":
            continue
        if record.get("validation_status") != "INVALID":
            continue
        amount_cents = record.get("strict_amount_cents")
        if amount_cents is None or amount_cents <= 0:
            rejected += 1
            continue
        word_records = by_word.get(
            (int(record["line_id"]), int(record["word_id"])), []
        )
        labels_on_word = {str(item.get("label")) for item in word_records}
        if labels_on_word & LINE_TOTAL_CONFLICT_LABELS:
            rejected += 1
            continue
        if amount_cents in valid_grands:
            rejected += 1
            continue
        candidates.append(record)
    return candidates, rejected


def _status_flip_action(
    issue: dict[str, Any],
    record: dict[str, Any],
    new_status: str,
    reasoning: str,
) -> dict[str, Any]:
    """Build an exact update_word_label action for the MCP executor."""
    return {
        "tool": "update_word_label",
        "image_id": issue.get("image_id"),
        "receipt_id": issue.get("receipt_id"),
        "line_id": record.get("line_id"),
        "word_id": record.get("word_id"),
        "label": record.get("label"),
        "new_status": new_status,
        "reasoning": reasoning,
    }


def _line_total_actions(
    issue: dict[str, Any],
    candidates: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build actions for LINE_TOTAL flips and conflicting UNIT_PRICE labels."""
    by_word = _records_by_word(records)
    actions: list[dict[str, Any]] = []
    for candidate in candidates:
        actions.append(
            _status_flip_action(
                issue,
                candidate,
                "VALID",
                "Preflight math proves this item amount is a line total.",
            )
        )
        word_records = by_word.get(
            (int(candidate["line_id"]), int(candidate["word_id"])), []
        )
        for record in word_records:
            if (
                record.get("label") == "UNIT_PRICE"
                and record.get("validation_status") == "VALID"
            ):
                actions.append(
                    _status_flip_action(
                        issue,
                        record,
                        "INVALID",
                        "Same amount is used as a proven line total, not a unit price.",
                    )
                )
    return actions


def _supporting_record_actions(
    issue: dict[str, Any],
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build actions for invalid supporting subtotal/tax/tip/discount labels."""
    actions: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for record in records:
        key = (
            int(record["line_id"]),
            int(record["word_id"]),
            str(record["label"]),
        )
        if key in seen:
            continue
        seen.add(key)
        if record.get("validation_status") != "VALID":
            label_name = str(record.get("label"))
            actions.append(
                _status_flip_action(
                    issue,
                    record,
                    "VALID",
                    f"Preflight math requires this {label_name} amount.",
                )
            )
    return actions


def _first_record_with_amount(
    records: list[dict[str, Any]],
    amount_cents: int,
) -> dict[str, Any] | None:
    """Return the first amount record matching the selected arithmetic path."""
    for record in records:
        if record.get("amount_cents") == amount_cents:
            return record
    return None


def _unique_cents(records: list[dict[str, Any]]) -> list[int]:
    """Return unique amount cents in input order."""
    seen: set[int] = set()
    values: list[int] = []
    for record in records:
        amount = record.get("amount_cents")
        if amount is None or amount in seen:
            continue
        seen.add(amount)
        values.append(int(amount))
    return values


def _issue_evidence(issue: dict[str, Any]) -> dict[str, Any]:
    """Return the first evidence block attached to an issue."""
    evidence = issue.get("evidence") or []
    if evidence and isinstance(evidence[0], dict):
        return evidence[0]
    return {}


def _issue_involved_words(issue: dict[str, Any]) -> list[dict[str, Any]]:
    """Return compact word evidence for a ledger issue."""
    evidence = _issue_evidence(issue)
    involved = evidence.get("involved_words")
    if isinstance(involved, list):
        return [word for word in involved if isinstance(word, dict)]
    if evidence:
        return [evidence]
    return []


def _issue_word_labels(issue: dict[str, Any]) -> set[str]:
    """Return labels referenced by an issue's compact word evidence."""
    labels: set[str] = set()
    for word in _issue_involved_words(issue):
        label = word.get("current_label") or word.get("label")
        if label:
            labels.add(str(label).upper())
    return labels


def _issue_contains_label(issue: dict[str, Any], label_name: str) -> bool:
    """Return true when an issue references a specific label role."""
    target = label_name.upper()
    if target in _issue_word_labels(issue):
        return True
    return target in str(issue.get("message") or "").upper()


def _issue_bad_decisions(issue: dict[str, Any]) -> list[dict[str, Any]]:
    """Return word evidence whose validation decision is not cleanly valid."""
    return [
        word
        for word in _issue_involved_words(issue)
        if str(word.get("decision") or "").upper()
        in {"INVALID", "NEEDS_REVIEW"}
    ]


def _amountish_text_is_malformed(text: Any) -> bool:
    """Return true for amount-like tokens that are not strict money values."""
    if text is None:
        return False
    raw = str(text).strip()
    if not raw or not AMOUNTISH_RE.fullmatch(raw):
        return False
    if STRICT_MONEY_RE.fullmatch(raw):
        return False
    return _money_cents(raw) is None or "." not in raw


def _issue_has_malformed_amount(
    issue: dict[str, Any],
    records: list[dict[str, Any]],
) -> bool:
    """Return true when issue evidence points at malformed amount text."""
    issue_words = {
        (word.get("line_id"), word.get("word_id"))
        for word in _issue_involved_words(issue)
        if word.get("line_id") is not None and word.get("word_id") is not None
    }
    for record in records:
        if record.get("label") not in FINANCIAL_AMOUNT_LABELS:
            continue
        if issue_words and (
            record.get("line_id"),
            record.get("word_id"),
        ) not in issue_words:
            continue
        if _amountish_text_is_malformed(record.get("text")):
            return True
    for word in _issue_involved_words(issue):
        label = str(
            word.get("current_label") or word.get("label") or ""
        ).upper()
        if label in FINANCIAL_AMOUNT_LABELS and _amountish_text_is_malformed(
            word.get("word_text") or word.get("text")
        ):
            return True
    return False


def _issue_has_multiple_grand_totals(
    issue: dict[str, Any],
    records: list[dict[str, Any]],
) -> bool:
    """Return true when more than one grand-total amount is in play."""
    issue_grand_words = [
        word
        for word in _issue_involved_words(issue)
        if str(word.get("current_label") or word.get("label") or "").upper()
        == "GRAND_TOTAL"
    ]
    if len(issue_grand_words) > 1:
        return True
    valid_grands = _amounts_for_label(records, "GRAND_TOTAL")
    return len(_unique_cents(valid_grands)) > 1


def _metadata_root_cause(issue: dict[str, Any]) -> str:
    """Choose a concrete root-cause code for metadata label issues."""
    evidence = _issue_evidence(issue)
    label = str(evidence.get("current_label") or "").upper()
    text = str(evidence.get("word_text") or "")
    text_has_digit = any(ch.isdigit() for ch in text)

    if issue.get("check_id") == "merchant_identity":
        if label == "STORE_HOURS" and not text_has_digit:
            return "business_name_token_mislabeled_store_hours"
        if label == "MERCHANT_NAME":
            return "generic_or_extra_merchant_name_token"
        if label == "PHONE_NUMBER":
            return "partial_phone_token"
        if label == "WEBSITE":
            return "truncated_website_token"
        return "merchant_metadata_context_mismatch"

    if issue.get("check_id") == "receipt_format":
        if label == "TIME":
            return "partial_time_token"
        if label == "LOYALTY_ID":
            return "numeric_token_mislabeled_loyalty_id"
        if label == "DATE":
            return "date_token_context_mismatch"
        if label == "PAYMENT_METHOD":
            return "payment_method_context_mismatch"
        return "format_metadata_context_mismatch"

    return "metadata_context_mismatch"


def _classify_metadata_issue_preflight(
    issue: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify merchant/format issues without proposing writes."""
    root_cause = _metadata_root_cause(issue)
    return _preflight(
        "needs_ai_review",
        summary=(
            "Metadata label context failed, but no exact deterministic "
            "write plan is stored yet."
        ),
        records=records,
        issue=issue,
        automation_lane="ai_review",
        lane="safe_label_edit_candidate",
        root_cause=root_cause,
        recommended_next_step=(
            "Review neighboring receipt text and store an exact label-status "
            "action only when the correction is unambiguous."
        ),
        evidence={"root_cause": root_cause},
    )


def _terminal_financial_preflight(
    issue: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    classification: str,
    lane: str,
    root_cause: str,
    summary: str,
    evidence: dict[str, Any] | None = None,
    section_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a non-write financial preflight classification."""
    merged_evidence = _with_section_evidence(evidence, section_evidence)
    return _preflight(
        classification,
        summary=summary,
        records=records,
        issue=issue,
        automation_lane="none"
        if classification in {"known_limitation", "evaluator_rule_gap"}
        else "ai_review",
        lane=lane,
        root_cause=root_cause,
        recommended_next_step=(
            "Do not apply label writes. Route this issue according to its "
            "root cause before retrying automation."
        ),
        evidence=merged_evidence,
        fingerprint_context=(
            {"section_evidence": section_evidence} if section_evidence else None
        ),
    )


def _classify_financial_non_exact_preflight(
    issue: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    message: str,
    rejected_candidate_count: int | None = None,
    evidence: dict[str, Any] | None = None,
    section_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify financial issues that did not produce an exact write plan."""
    has_missing_line_total = bool(MISSING_LINE_TOTAL_RE.search(message))
    has_tip = _issue_contains_label(issue, "TIP")
    bad_decisions = _issue_bad_decisions(issue)
    has_tip_suggestions = bool(
        (section_evidence or {}).get("has_tip_suggestions")
    )
    has_tip_entry_area = bool(
        (section_evidence or {}).get("has_tip_entry_area")
    )
    has_void_discount = bool(
        (section_evidence or {}).get("has_void_discount")
    )

    if has_void_discount:
        return _terminal_financial_preflight(
            issue,
            records,
            classification="evaluator_rule_gap",
            lane="receipt_structure_rule",
            root_cause="void_discount_formula_rule",
            summary=(
                "Voided-item or discount section evidence changes the math "
                "semantics; this needs an evaluator formula rule before any "
                "label write."
            ),
            evidence=evidence,
            section_evidence=section_evidence,
        )

    if has_tip_suggestions:
        if has_tip and has_missing_line_total:
            return _terminal_financial_preflight(
                issue,
                records,
                classification="evaluator_rule_gap",
                lane="receipt_structure_rule",
                root_cause="missing_line_totals_with_tip_gratuity",
                summary=(
                    "Missing line totals intersect with tip/gratuity labels; "
                    "this needs a receipt-structure rule before any label "
                    "write."
                ),
                evidence=evidence,
                section_evidence=section_evidence,
            )
        return _terminal_financial_preflight(
            issue,
            records,
            classification="evaluator_rule_gap",
            lane="receipt_structure_rule",
            root_cause="tip_gratuity_ambiguity",
            summary=(
                "Amounts in a tip-suggestion section are selectable options, "
                "not proof of charged tips or totals."
            ),
            evidence=evidence,
            section_evidence=section_evidence,
        )

    if has_tip_entry_area:
        if has_tip and has_missing_line_total:
            return _terminal_financial_preflight(
                issue,
                records,
                classification="evaluator_rule_gap",
                lane="receipt_structure_rule",
                root_cause="missing_line_totals_with_tip_gratuity",
                summary=(
                    "Missing line totals intersect with tip/gratuity labels; "
                    "this needs a receipt-structure rule before any label "
                    "write."
                ),
                evidence=evidence,
                section_evidence=section_evidence,
            )
        return _terminal_financial_preflight(
            issue,
            records,
            classification="evaluator_rule_gap",
            lane="receipt_structure_rule",
            root_cause="tip_gratuity_ambiguity",
            summary=(
                "Payment/tip-entry section evidence is ambiguous without a "
                "confirmed charged tip or final total."
            ),
            evidence=evidence,
            section_evidence=section_evidence,
        )

    if _issue_has_malformed_amount(issue, records):
        return _terminal_financial_preflight(
            issue,
            records,
            classification="reocr_needed",
            lane="reocr_needed",
            root_cause="malformed_amount_text",
            summary=(
                "Financial evidence contains amount-like text that cannot be "
                "trusted as strict money."
            ),
            evidence=evidence,
            section_evidence=section_evidence,
        )

    if has_tip and has_missing_line_total:
        return _terminal_financial_preflight(
            issue,
            records,
            classification="evaluator_rule_gap",
            lane="receipt_structure_rule",
            root_cause="missing_line_totals_with_tip_gratuity",
            summary=(
                "Missing line totals intersect with tip/gratuity labels; this "
                "needs a receipt-structure rule before any label write."
            ),
            evidence=evidence,
            section_evidence=section_evidence,
        )

    if has_tip:
        return _terminal_financial_preflight(
            issue,
            records,
            classification="evaluator_rule_gap",
            lane="receipt_structure_rule",
            root_cause="tip_gratuity_ambiguity",
            summary=(
                "Tip/gratuity evidence can be an actual charged tip or a "
                "suggested option, so deterministic label writes are blocked."
            ),
            evidence=evidence,
            section_evidence=section_evidence,
        )

    if _issue_has_multiple_grand_totals(issue, records):
        return _terminal_financial_preflight(
            issue,
            records,
            classification="evaluator_rule_gap",
            lane="evaluator_disambiguation",
            root_cause="multiple_total_like_amounts",
            summary=(
                "More than one grand-total-like amount is present; the "
                "evaluator needs disambiguation before automation."
            ),
            evidence=evidence,
            section_evidence=section_evidence,
        )

    if has_missing_line_total:
        has_tax_or_discount = _issue_contains_label(
            issue, "TAX"
        ) or _issue_contains_label(issue, "DISCOUNT")
        root_cause = (
            "missing_line_totals_with_tax_discount"
            if has_tax_or_discount
            else "missing_line_totals"
        )
        return _terminal_financial_preflight(
            issue,
            records,
            classification="needs_ai_review",
            lane="preflight_candidate_search",
            root_cause=root_cause,
            summary=(
                "No exact existing LINE_TOTAL status-flip plan was found."
            ),
            evidence={
                **(evidence or {}),
                "rejected_candidate_count": rejected_candidate_count,
            },
            section_evidence=section_evidence,
        )

    if bad_decisions:
        return _terminal_financial_preflight(
            issue,
            records,
            classification="needs_ai_review",
            lane="safe_label_edit_candidate",
            root_cause="wrong_financial_label_role",
            summary=(
                "A financial label role is suspect, but no exact stored "
                "action proves the replacement."
            ),
            evidence=evidence,
            section_evidence=section_evidence,
        )

    return _terminal_financial_preflight(
        issue,
        records,
        classification="evaluator_rule_gap",
        lane="evaluator_rule_gap",
        root_cause="valid_labels_formula_mismatch",
        summary=(
            "The visible labels are valid, but the formula still does not "
            "reconcile."
        ),
        evidence=evidence,
        section_evidence=section_evidence,
    )


def classify_receipt_health_issue_preflight(
    issue: dict[str, Any],
    *,
    words: list[dict[str, Any]],
    labels: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify one receipt-health issue before an agent attempts it.

    Only the safe_exact_plan class may produce write actions. Every other
    classifier output is a terminal routing decision for humans, OCR repair, or
    evaluator-rule work.
    """
    records = _label_records(words, labels)
    if issue.get("check_id") != "financial_math":
        return _classify_metadata_issue_preflight(issue, records)

    section_rows = _receipt_section_rows(words)
    section_evidence = _issue_section_evidence(issue, words)
    message = str(issue.get("message") or "")
    if not MISSING_LINE_TOTAL_RE.search(message):
        return _classify_financial_non_exact_preflight(
            issue,
            records,
            message=message,
            section_evidence=section_evidence,
        )

    valid_line_totals = _amounts_for_label(records, "LINE_TOTAL")
    if valid_line_totals:
        return _preflight(
            "known_limitation",
            summary=(
                f"{len(valid_line_totals)} live valid LINE_TOTAL label(s) "
                "already exist, so the cached issue should not be retried."
            ),
            records=records,
            issue=issue,
            automation_lane="none",
            lane="known_limitation",
            root_cause="already_consistent_labels",
            recommended_next_step=(
                "Do not apply label writes unless this data fingerprint changes."
            ),
            evidence={
                "line_total_amounts": [
                    _amount_label(int(record["amount_cents"]))
                    for record in valid_line_totals
                ]
            },
        )

    if (
        section_evidence.get("has_tip_suggestions")
        or section_evidence.get("has_tip_entry_area")
        or section_evidence.get("has_void_discount")
        or _issue_contains_label(issue, "TIP")
    ):
        return _classify_financial_non_exact_preflight(
            issue,
            records,
            message=message,
            section_evidence=section_evidence,
        )

    candidates, rejected_count = _candidate_line_total_records(records)
    if not candidates:
        return _classify_financial_non_exact_preflight(
            issue,
            records,
            message=message,
            rejected_candidate_count=rejected_count,
            section_evidence=section_evidence,
        )

    line_sum = sum(int(record["strict_amount_cents"]) for record in candidates)
    valid_grands = _unique_cents(_amounts_for_label(records, "GRAND_TOTAL"))
    subtotal_records = _amounts_for_label(
        records, "SUBTOTAL", valid_only=False
    )
    tax_records = _amounts_for_label(records, "TAX", valid_only=False)
    tip_records = _amounts_for_label(records, "TIP", valid_only=False)
    discount_records = _amounts_for_label(
        records, "DISCOUNT", valid_only=False
    )

    evidence = {
        "line_total_amounts": [
            _amount_label(int(record["strict_amount_cents"]))
            for record in candidates
        ],
        "line_total_sum": _amount_label(line_sum),
        "grand_total_amounts": [
            _amount_label(value) for value in valid_grands
        ],
        "subtotal_amounts": [
            _amount_label(int(record["amount_cents"]))
            for record in subtotal_records
            if record.get("amount_cents") is not None
        ],
        "rejected_candidate_count": rejected_count,
    }
    line_item_evidence = _line_item_amount_evidence(
        section_rows,
        candidates,
    )
    if line_item_evidence["item_amount_row_count"] > 0:
        evidence["line_item_amount_evidence"] = line_item_evidence

    line_actions = _line_total_actions(issue, candidates, records)
    if line_sum in valid_grands:
        actions = line_actions
        return _preflight(
            "safe_exact_plan",
            summary=(
                "Existing LINE_TOTAL candidates sum to visible GRAND_TOTAL "
                f"{_amount_label(line_sum)}."
            ),
            records=records,
            issue=issue,
            automation_lane="deterministic",
            lane="safe_exact_plan",
            root_cause="missing_line_totals",
            recommended_next_step=(
                "Apply the stored update_word_label actions exactly."
            ),
            proposed_actions=actions,
            evidence=evidence,
        )

    for subtotal in subtotal_records:
        subtotal_amount = subtotal.get("amount_cents")
        if subtotal_amount != line_sum:
            continue
        taxes = _unique_cents(tax_records) or [0]
        tips = _unique_cents(tip_records) or [0]
        discounts = _unique_cents(discount_records) or [0]
        for tax_amount in taxes:
            for tip_amount in tips:
                for discount_amount in discounts:
                    total = (
                        int(subtotal_amount)
                        + int(tax_amount)
                        + int(tip_amount)
                        - int(discount_amount)
                    )
                    if total not in valid_grands:
                        continue
                    support_records = [subtotal]
                    if tax_amount:
                        tax_record = _first_record_with_amount(
                            tax_records, tax_amount
                        )
                        if not tax_record:
                            continue
                        support_records.append(tax_record)
                    if tip_amount:
                        tip_record = _first_record_with_amount(
                            tip_records, tip_amount
                        )
                        if not tip_record:
                            continue
                        support_records.append(tip_record)
                    if discount_amount:
                        discount_record = _first_record_with_amount(
                            discount_records, discount_amount
                        )
                        if not discount_record:
                            continue
                        support_records.append(discount_record)
                    actions = line_actions + _supporting_record_actions(
                        issue, support_records
                    )
                    evidence["resolved_total"] = _amount_label(total)
                    return _preflight(
                        "safe_exact_plan",
                        summary=(
                            "Existing LINE_TOTAL candidates match SUBTOTAL "
                            f"{_amount_label(line_sum)} and reconcile to "
                            f"GRAND_TOTAL {_amount_label(total)}."
                        ),
                        records=records,
                        issue=issue,
                        automation_lane="deterministic",
                        lane="safe_exact_plan",
                        root_cause=(
                            "missing_line_totals_with_tax_discount"
                        ),
                        recommended_next_step=(
                            "Apply the stored update_word_label actions exactly."
                        ),
                        proposed_actions=actions,
                        evidence=evidence,
                    )

    if (
        line_item_evidence["fragmented_amount_row_count"] > 0
        and line_item_evidence["item_amount_row_count"]
        > line_item_evidence["labeled_line_total_row_count"]
    ):
        return _preflight(
            "reocr_needed",
            summary=(
                "Visual item rows contain price-like fragments, but clean "
                "LINE_TOTAL tokens are incomplete."
            ),
            records=records,
            issue=issue,
            automation_lane="none",
            lane="reocr_needed",
            root_cause="line_item_price_tokenization_gap",
            recommended_next_step=(
                "Repair OCR/row price tokenization before attempting label "
                "cleanup."
            ),
            evidence=_with_section_evidence(evidence, section_evidence),
            fingerprint_context={"line_item_amount_evidence": line_item_evidence},
        )

    return _preflight(
        "evaluator_rule_gap",
        summary=(
            "Clean LINE_TOTAL candidates exist, but they do not reconcile "
            "to the visible subtotal/grand total path."
        ),
        records=records,
        issue=issue,
        automation_lane="none",
        lane="evaluator_rule_gap",
        root_cause="line_total_candidates_do_not_reconcile",
        recommended_next_step=(
            "Do not apply label writes. Mark as known limitation or update the "
            "evaluator rule after reviewing the receipt."
        ),
        evidence=_with_section_evidence(evidence, section_evidence),
    )


def attach_preflight_classifications(
    run_issues: list[dict[str, Any]],
    receipt_data_lookup: dict[tuple[str, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach deterministic preflight classification to run issues."""
    classified: list[dict[str, Any]] = []
    for issue in run_issues:
        key = (str(issue.get("image_id")), int(issue.get("receipt_id") or 0))
        data = receipt_data_lookup.get(key) or {}
        preflight = classify_receipt_health_issue_preflight(
            issue,
            words=data.get("words") or [],
            labels=data.get("labels") or [],
        )
        classified.append({**issue, "preflight": preflight})
    return classified


def summarize_preflight_classifications(
    issues: list[dict[str, Any]],
) -> dict[str, int]:
    """Count preflight classifications in a run or ledger."""
    return summarize_preflight_field(issues, "classification")


def summarize_preflight_field(
    issues: list[dict[str, Any]],
    field_name: str,
) -> dict[str, int]:
    """Count one persisted preflight field in a run or ledger."""
    if field_name not in PREFLIGHT_SUMMARY_FIELDS:
        raise ValueError(f"Unsupported preflight summary field: {field_name}")
    counts: dict[str, int] = {}
    for issue in issues:
        value = (issue.get("preflight") or {}).get(field_name)
        value = value or "unclassified"
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def _metadata_decision_issues(
    receipt: dict[str, Any],
    check: dict[str, Any],
    decisions: list[dict[str, Any]],
    *,
    execution_id: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    """Build ledger issues from merchant/format label decisions."""
    issues: list[dict[str, Any]] = []
    for decision in decisions:
        decision_status = str(decision.get("decision") or "").upper()
        if decision_status not in {"INVALID", "NEEDS_REVIEW"}:
            continue

        status = "fail" if decision_status == "INVALID" else "review"
        issue_type = (
            "invalid_label"
            if decision_status == "INVALID"
            else "label_needs_review"
        )
        label = decision.get("current_label") or "UNKNOWN_LABEL"
        word_text = decision.get("word_text") or ""
        message = f"{label} on {word_text!r} is {decision_status}"
        fingerprint_parts = {
            "image_id": receipt.get("image_id"),
            "receipt_id": receipt.get("receipt_id"),
            "check_id": check.get("id"),
            "issue_type": issue_type,
            "word": _word_ref(decision),
        }
        issues.append(
            _issue_base(
                receipt,
                check,
                execution_id=execution_id,
                observed_at=observed_at,
                status=status,
                issue_type=issue_type,
                message=message,
                fingerprint_parts=fingerprint_parts,
                evidence=[_compact_decision(decision)],
            )
        )
    return issues


def _financial_issue_message(equation: dict[str, Any]) -> str:
    """Return a compact human-readable financial issue summary."""
    description = equation.get("description")
    if description:
        return str(description)

    issue_type = equation.get("issue_type") or "financial_math"
    difference = equation.get("difference")
    if difference is None:
        return str(issue_type)
    return f"{issue_type} mismatch by {difference}"


def _financial_equation_issues(
    receipt: dict[str, Any],
    check: dict[str, Any],
    equations: list[dict[str, Any]],
    *,
    execution_id: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    """Build ledger issues from financial equations and their word evidence."""
    issues: list[dict[str, Any]] = []
    for equation in equations:
        involved_words = equation.get("involved_words") or []
        bad_decisions = [
            word
            for word in involved_words
            if str(word.get("decision") or "").upper()
            in {"INVALID", "NEEDS_REVIEW"}
        ]
        has_mismatch = _equation_has_mismatch(equation)
        if not has_mismatch and not bad_decisions:
            continue

        has_invalid = any(
            str(word.get("decision") or "").upper() == "INVALID"
            for word in bad_decisions
        )
        status = "fail" if has_mismatch or has_invalid else "review"
        issue_type = str(equation.get("issue_type") or "financial_math")
        word_refs = sorted(
            (_word_ref(word) for word in involved_words),
            key=lambda item: (
                item.get("line_id") is None,
                item.get("line_id") or -1,
                item.get("word_id") is None,
                item.get("word_id") or -1,
                item.get("label") or "",
            ),
        )
        fingerprint_parts = {
            "image_id": receipt.get("image_id"),
            "receipt_id": receipt.get("receipt_id"),
            "check_id": check.get("id"),
            "issue_type": issue_type,
            "word_refs": word_refs,
        }
        evidence = [
            {
                "issue_type": issue_type,
                "description": equation.get("description"),
                "expected_value": equation.get("expected_value"),
                "actual_value": equation.get("actual_value"),
                "difference": equation.get("difference"),
                "involved_words": [
                    _compact_decision(word) for word in involved_words
                ],
            }
        ]
        issues.append(
            _issue_base(
                receipt,
                check,
                execution_id=execution_id,
                observed_at=observed_at,
                status=status,
                issue_type=issue_type,
                message=_financial_issue_message(equation),
                fingerprint_parts=fingerprint_parts,
                evidence=evidence,
            )
        )
    return issues


def build_receipt_health_issues(
    receipt: dict[str, Any],
    *,
    execution_id: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    """Extract stable issue records from one receipt-health entry."""
    issues: list[dict[str, Any]] = []
    checks_by_id = {
        str(check.get("id")): check for check in receipt.get("checks", [])
    }

    place_check = checks_by_id.get("merchant_identity")
    if place_check:
        issues.extend(
            _metadata_decision_issues(
                receipt,
                place_check,
                receipt.get("place_validation", {}).get("decisions") or [],
                execution_id=execution_id,
                observed_at=observed_at,
            )
        )

    format_check = checks_by_id.get("receipt_format")
    if format_check:
        issues.extend(
            _metadata_decision_issues(
                receipt,
                format_check,
                receipt.get("format_validation", {}).get("decisions") or [],
                execution_id=execution_id,
                observed_at=observed_at,
            )
        )

    financial_check = checks_by_id.get("financial_math")
    if financial_check:
        issues.extend(
            _financial_equation_issues(
                receipt,
                financial_check,
                receipt.get("financial_math", {}).get("equations") or [],
                execution_id=execution_id,
                observed_at=observed_at,
            )
        )

    return sorted(
        issues,
        key=lambda issue: (
            -_status_priority(str(issue.get("status"))),
            str(issue.get("merchant_name") or ""),
            str(issue.get("image_id") or ""),
            int(issue.get("receipt_id") or 0),
            str(issue.get("issue_id") or ""),
        ),
    )


def build_receipt_health_run_artifacts(
    receipts: list[dict[str, Any]],
    *,
    execution_id: str,
    observed_at: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build immutable issue list and summary for a receipt-health run."""
    issues: list[dict[str, Any]] = []
    status_counts = {"pass": 0, "review": 0, "fail": 0, "not_applicable": 0}
    check_counts: dict[str, dict[str, int]] = {}

    for receipt in receipts:
        status = str(receipt.get("overall_status") or "not_applicable")
        if status not in status_counts:
            status = "not_applicable"
        status_counts[status] += 1

        for check in receipt.get("checks", []):
            check_id = str(check.get("id") or "unknown")
            check_status = str(check.get("status") or "not_applicable")
            check_counts.setdefault(
                check_id,
                {"pass": 0, "review": 0, "fail": 0, "not_applicable": 0},
            )
            if check_status not in check_counts[check_id]:
                check_status = "not_applicable"
            check_counts[check_id][check_status] += 1

        issues.extend(
            build_receipt_health_issues(
                receipt,
                execution_id=execution_id,
                observed_at=observed_at,
            )
        )

    summary = {
        "execution_id": execution_id,
        "cached_at": observed_at,
        "total_receipts": len(receipts),
        "total_issues": len(issues),
        "receipts_with_issues": len(
            {
                (issue.get("image_id"), issue.get("receipt_id"))
                for issue in issues
            }
        ),
        "by_status": status_counts,
        "by_check": check_counts,
        "by_issue_type": {},
    }
    by_issue_type: dict[str, int] = {}
    for issue in issues:
        issue_type = str(issue.get("issue_type") or "unknown")
        by_issue_type[issue_type] = by_issue_type.get(issue_type, 0) + 1
    summary["by_issue_type"] = dict(
        sorted(by_issue_type.items(), key=lambda item: (-item[1], item[0]))
    )
    return issues, summary


def _issue_is_automation_ready(issue: dict[str, Any]) -> bool:
    """Return true when deterministic preflight allows automation."""
    preflight = issue.get("preflight") or {}
    return bool(preflight.get("is_automation_ready"))


def _summarize_ledger(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Build compact ledger counts for API responses and dashboards."""
    by_state: dict[str, int] = {}
    by_check: dict[str, int] = {}
    for issue in issues:
        state = str(issue.get("state") or "open")
        check_id = str(issue.get("check_id") or "unknown")
        by_state[state] = by_state.get(state, 0) + 1
        by_check[check_id] = by_check.get(check_id, 0) + 1
    return {
        "total_issues": len(issues),
        "by_state": dict(sorted(by_state.items())),
        "by_check": dict(sorted(by_check.items())),
        "by_preflight_classification": summarize_preflight_classifications(
            issues
        ),
        "by_preflight_lane": summarize_preflight_field(issues, "lane"),
        "by_preflight_root_cause": summarize_preflight_field(
            issues, "root_cause"
        ),
        "eligible_issues": sum(
            1
            for issue in issues
            if issue.get("state") == "open"
            and int(issue.get("attempt_count") or 0) < MAX_LEDGER_ATTEMPTS
            and _issue_is_automation_ready(issue)
        ),
    }


def _known_limitation_should_reopen(
    previous: dict[str, Any],
    next_issue: dict[str, Any],
) -> bool:
    """Known limitations reopen when the data/classifier fingerprint changes."""
    previous_fingerprint = previous.get("suppression_fingerprint")
    next_fingerprint = (next_issue.get("preflight") or {}).get(
        "data_fingerprint"
    )
    if not previous_fingerprint or not next_fingerprint:
        return False
    return str(previous_fingerprint) != str(next_fingerprint)


def reconcile_receipt_health_ledger(
    previous_ledger: dict[str, Any] | None,
    run_issues: list[dict[str, Any]],
    *,
    execution_id: str,
    observed_at: str,
    max_attempts: int = MAX_LEDGER_ATTEMPTS,
) -> dict[str, Any]:
    """Reconcile current run observations with the mutable issue ledger."""
    previous_issues = {
        str(issue.get("issue_id")): issue
        for issue in (previous_ledger or {}).get("issues", [])
        if issue.get("issue_id")
    }
    current_ids = {str(issue.get("issue_id")) for issue in run_issues}
    reconciled: list[dict[str, Any]] = []

    for issue in run_issues:
        issue_id = str(issue.get("issue_id"))
        previous = previous_issues.get(issue_id)
        if previous:
            attempt_count = int(previous.get("attempt_count") or 0)
            prior_state = str(previous.get("state") or "open")
            next_issue = {
                **previous,
                **issue,
                "state": prior_state,
                "first_seen_at": previous.get("first_seen_at")
                or issue.get("observed_at"),
                "first_seen_execution_id": previous.get(
                    "first_seen_execution_id"
                )
                or issue.get("execution_id"),
                "last_seen_at": observed_at,
                "last_seen_execution_id": execution_id,
                "occurrence_count": int(previous.get("occurrence_count") or 1)
                + 1,
                "attempt_count": attempt_count,
                "attempts": previous.get("attempts") or [],
            }
            if prior_state == "resolved":
                next_issue["state"] = "open"
                next_issue["reopened_at"] = observed_at
                next_issue["reopened_execution_id"] = execution_id
                next_issue.pop("resolved_at", None)
                next_issue.pop("resolved_execution_id", None)
            elif prior_state in {"awaiting_validation", "claimed"}:
                next_issue["last_validation_execution_id"] = execution_id
                if attempt_count >= max_attempts:
                    next_issue["state"] = "manual_review"
                    next_issue["blocked_reason"] = (
                        "Still failing after automated attempts"
                    )
                else:
                    next_issue["state"] = "open"
            elif prior_state == "known_limitation":
                if _known_limitation_should_reopen(previous, next_issue):
                    next_issue["state"] = "open"
                    next_issue["reopened_at"] = observed_at
                    next_issue["reopened_execution_id"] = execution_id
                    next_issue["reopened_reason"] = (
                        "Known limitation fingerprint changed"
                    )
                else:
                    next_issue["state"] = prior_state
            elif prior_state in {"blocked", "manual_review"}:
                next_issue["state"] = prior_state
            else:
                next_issue["state"] = "open"

            if next_issue.get("state") != "claimed":
                next_issue.pop("claimed_at", None)
                next_issue.pop("claimed_by", None)
            reconciled.append(next_issue)
            continue

        reconciled.append(
            {
                **issue,
                "state": "open",
                "first_seen_at": observed_at,
                "first_seen_execution_id": execution_id,
                "last_seen_at": observed_at,
                "last_seen_execution_id": execution_id,
                "occurrence_count": 1,
                "attempt_count": 0,
                "attempts": [],
            }
        )

    for issue_id, previous in previous_issues.items():
        if issue_id in current_ids:
            continue
        state = str(previous.get("state") or "open")
        if state != "resolved":
            previous = {
                **previous,
                "state": "resolved",
                "resolved_at": observed_at,
                "resolved_execution_id": execution_id,
            }
        reconciled.append(previous)

    reconciled.sort(
        key=lambda issue: (
            str(issue.get("state") or ""),
            -_status_priority(str(issue.get("status") or "")),
            str(issue.get("merchant_name") or ""),
            str(issue.get("issue_id") or ""),
        )
    )
    ledger = {
        "version": 1,
        "updated_at": observed_at,
        "latest_execution_id": execution_id,
        "max_attempts": max_attempts,
        "summary": _summarize_ledger(reconciled),
        "issues": reconciled,
    }
    return ledger


def eligible_receipt_health_issues(
    ledger: dict[str, Any],
    *,
    limit: int = 10,
    check_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return open ledger issues that an automated routine may attempt."""
    max_attempts = int(ledger.get("max_attempts") or MAX_LEDGER_ATTEMPTS)
    issues = []
    for issue in ledger.get("issues", []):
        if issue.get("state") != "open":
            continue
        if int(issue.get("attempt_count") or 0) >= max_attempts:
            continue
        if not _issue_is_automation_ready(issue):
            continue
        if check_id and issue.get("check_id") != check_id:
            continue
        issues.append(issue)
    return issues[:limit]


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
        result.append(
            {
                "text": w.get("text", ""),
                "label": w.get("label"),
                "line_id": w.get("line_id"),
                "word_id": w.get("word_id"),
                "bbox": _extract_bbox(w),
            }
        )
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
            place_duration = (
                metadata_duration * len(place_decisions) / total_meta
            )
            format_duration = (
                metadata_duration * len(format_decisions) / total_meta
            )
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
        "receipt_type": _extract_receipt_type(financial_decisions),
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
            "is_llm": False,
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
            "receipt_health_count": 0,
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
        if (
            "financial_all_decisions" in item
            or "metadata_all_decisions" in item
        ):
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
    receipt_health_entries: list[tuple[str, dict]] = []

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

        # Receipt health (single unified view over place, format, and math)
        rh_entry = build_receipt_health_entry(wr_entry)
        s3_name = f"receipt-health/{image_id}_{receipt_id}.json"
        receipt_health_entries.append((s3_name, rh_entry))

    logger.info(
        "Built %d financial-math, %d within-receipt, %d receipt-health entries",
        len(financial_math_entries),
        len(within_receipt_entries),
        len(receipt_health_entries),
    )

    # 4. Write all cache entries + metadata in parallel
    now_iso = datetime.now(timezone.utc).isoformat()
    receipt_health_receipts = [entry for _, entry in receipt_health_entries]
    run_issues, run_summary = build_receipt_health_run_artifacts(
        receipt_health_receipts,
        execution_id=execution_id,
        observed_at=now_iso,
    )
    run_issues = attach_preflight_classifications(run_issues, data_lookup)
    run_summary["by_preflight_classification"] = (
        summarize_preflight_classifications(run_issues)
    )
    run_summary["by_preflight_lane"] = summarize_preflight_field(
        run_issues, "lane"
    )
    run_summary["by_preflight_root_cause"] = summarize_preflight_field(
        run_issues, "root_cause"
    )
    previous_ledger = _read_json(cache_bucket, RECEIPT_HEALTH_LEDGER_KEY) or {}
    issue_ledger = reconcile_receipt_health_ledger(
        previous_ledger,
        run_issues,
        execution_id=execution_id,
        observed_at=now_iso,
    )
    eligible_issues = eligible_receipt_health_issues(
        issue_ledger,
        limit=1000,
    )

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
    rh_metadata = {
        "execution_id": execution_id,
        "cached_at": now_iso,
        "total_receipts": len(receipt_health_entries),
        "total_issues": len(run_issues),
        "run_prefix": f"receipt-health/runs/{execution_id}/",
        "ledger_key": RECEIPT_HEALTH_LEDGER_KEY,
    }

    write_tasks: list[tuple[str, dict]] = []
    write_tasks.extend(financial_math_entries)
    write_tasks.append(("financial-math/metadata.json", fm_metadata))
    write_tasks.extend(within_receipt_entries)
    write_tasks.append(("within-receipt/metadata.json", wr_metadata))
    write_tasks.extend(receipt_health_entries)
    write_tasks.append(("receipt-health/metadata.json", rh_metadata))
    run_prefix = f"receipt-health/runs/{execution_id}"
    write_tasks.extend(
        (
            f"{run_prefix}/receipts/{key.split('/')[-1]}",
            data,
        )
        for key, data in receipt_health_entries
    )
    write_tasks.append((f"{run_prefix}/summary.json", run_summary))
    write_tasks.append(
        (
            f"{run_prefix}/issues.json",
            {
                "execution_id": execution_id,
                "cached_at": now_iso,
                "summary": run_summary,
                "issues": run_issues,
            },
        )
    )
    write_tasks.append((RECEIPT_HEALTH_LEDGER_KEY, issue_ledger))
    write_tasks.append(
        (
            RECEIPT_HEALTH_CURRENT_KEY,
            {
                "execution_id": execution_id,
                "cached_at": now_iso,
                "summary": run_summary,
                "issues": run_issues,
            },
        )
    )
    write_tasks.append(
        (
            RECEIPT_HEALTH_ELIGIBLE_KEY,
            {
                "execution_id": execution_id,
                "cached_at": now_iso,
                "summary": issue_ledger.get("summary", {}),
                "issues": eligible_issues,
            },
        )
    )

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
        (
            "Completed in %.1fs: %d financial-math, %d within-receipt, "
            "%d receipt-health, %d errors"
        ),
        elapsed,
        len(financial_math_entries),
        len(within_receipt_entries),
        len(receipt_health_entries),
        errors,
    )

    return {
        "status": "completed",
        "execution_id": execution_id,
        "financial_math_count": len(financial_math_entries),
        "within_receipt_count": len(within_receipt_entries),
        "receipt_health_count": len(receipt_health_entries),
        "receipt_health_issue_count": len(run_issues),
        "eligible_issue_count": len(eligible_issues),
        "write_errors": errors,
        "duration_seconds": round(elapsed, 1),
    }

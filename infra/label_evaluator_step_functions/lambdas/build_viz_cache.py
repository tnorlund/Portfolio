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
            "is_llm": financial_math.get("is_llm", False),
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
        "eligible_issues": sum(
            1
            for issue in issues
            if issue.get("state") == "open"
            and int(issue.get("attempt_count") or 0) < MAX_LEDGER_ATTEMPTS
        ),
    }


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

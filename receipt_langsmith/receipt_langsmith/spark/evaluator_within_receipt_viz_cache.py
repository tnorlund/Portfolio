"""Helper to build within-receipt verification visualization cache.

Combines three verification passes into a single cache entry per receipt:
  1. Place Validation  – Google Places confirms merchant info
  2. Format Validation – regex/LLM confirms dates, times, payment methods
  3. Financial Math     – equations confirm totals add up

Reads unified S3 data (metadata/financial decisions), data S3 files (place
info + word bboxes), and trace parquet (financial_validation spans).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Label sets for each verification pass
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


def _parse_json(raw: Any) -> Any:
    """Safely parse a JSON string, returning None on failure."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return raw
    if not raw or not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Word / bbox helpers
# ---------------------------------------------------------------------------


def _build_word_lookup(
    words: list[dict],
) -> dict[tuple[int, int], dict]:
    """Build (line_id, word_id) → word dict from a data-row words list."""
    lookup: dict[tuple[int, int], dict] = {}
    for w in words:
        lid = w.get("line_id")
        wid = w.get("word_id")
        if lid is not None and wid is not None:
            lookup[(lid, wid)] = w
    return lookup


def _extract_bbox(word: dict) -> dict[str, float]:
    """Extract a normalized bounding box from a word dict."""
    bb = word.get("bounding_box") or word.get("bbox") or {}
    return {
        "x": bb.get("x", 0.0),
        "y": bb.get("y", 0.0),
        "width": bb.get("width", 0.0),
        "height": bb.get("height", 0.0),
    }


# ---------------------------------------------------------------------------
# Decision splitting
# ---------------------------------------------------------------------------


def _build_decision_entry(
    decision: dict,
    word_lookup: dict[tuple[int, int], dict],
) -> dict[str, Any]:
    """Convert a single all_decisions entry to the output format with bbox."""
    issue = decision.get("issue", {})
    llm_review = decision.get("llm_review", {})
    lid = issue.get("line_id")
    wid = issue.get("word_id")

    word: dict[str, Any] = {}
    if lid is not None and wid is not None:
        word = word_lookup.get((lid, wid), {})

    return {
        "line_id": lid,
        "word_id": wid,
        "word_text": issue.get("word_text", ""),
        "current_label": issue.get("current_label", ""),
        "decision": llm_review.get("decision"),
        "confidence": llm_review.get("confidence"),
        "reasoning": llm_review.get("reasoning"),
        "bbox": _extract_bbox(word) if word else {
            "x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0,
        },
    }


def _split_metadata_decisions(
    all_decisions: list[dict],
    word_lookup: dict[tuple[int, int], dict],
) -> tuple[list[dict], list[dict]]:
    """Split metadata_all_decisions into place and format groups."""
    place_decisions: list[dict] = []
    format_decisions: list[dict] = []

    for d in all_decisions:
        issue = d.get("issue", {})
        label = (issue.get("current_label") or "").upper()
        entry = _build_decision_entry(d, word_lookup)

        if label in PLACE_LABELS:
            place_decisions.append(entry)
        elif label in FORMAT_LABELS:
            format_decisions.append(entry)
        # Decisions with labels outside both sets are skipped

    return place_decisions, format_decisions


def _build_decision_summary(
    decisions: list[dict],
) -> dict[str, int]:
    """Count V/I/R decisions."""
    total = len(decisions)
    valid = sum(
        1 for d in decisions
        if (d.get("decision") or "").upper() == "VALID"
    )
    invalid = sum(
        1 for d in decisions
        if (d.get("decision") or "").upper() == "INVALID"
    )
    needs_review = sum(
        1 for d in decisions
        if (d.get("decision") or "").upper() == "NEEDS_REVIEW"
    )
    return {
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "needs_review": needs_review,
    }


# ---------------------------------------------------------------------------
# Place extraction
# ---------------------------------------------------------------------------


def _extract_place_info(place: dict | None) -> dict[str, Any] | None:
    """Extract relevant Place fields for the right-panel card."""
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


# ---------------------------------------------------------------------------
# Financial math (reuse logic from financial_math_viz_cache)
# ---------------------------------------------------------------------------


def _build_financial_equations(
    all_decisions: list[dict],
    word_lookup: dict[tuple[int, int], dict],
) -> list[dict[str, Any]]:
    """Group financial decisions by description (equation)."""
    if not all_decisions:
        return []

    groups: dict[str, list[dict]] = {}
    for d in all_decisions:
        issue = d.get("issue", {})
        desc = issue.get("description", "<unknown>")
        groups.setdefault(desc, []).append(d)

    equations: list[dict[str, Any]] = []
    for desc, group in groups.items():
        first_issue = group[0].get("issue", {})
        involved_words = [_build_decision_entry(d, word_lookup) for d in group]
        equations.append({
            "issue_type": first_issue.get("issue_type", ""),
            "description": desc,
            "expected_value": first_issue.get("expected_value"),
            "actual_value": first_issue.get("actual_value"),
            "difference": first_issue.get("difference"),
            "involved_words": involved_words,
        })

    return equations


def _build_financial_summary(equations: list[dict]) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Word list for frontend overlay
# ---------------------------------------------------------------------------


def _build_word_list(words: list[dict]) -> list[dict[str, Any]]:
    """Build simplified word list with bboxes for the frontend."""
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_within_receipt_cache(
    parquet_dir: str | None = None,
    *,
    rows: list[dict[str, Any]] | None = None,
    unified_rows: list[dict[str, Any]] | None = None,
    data_rows: list[dict[str, Any]] | None = None,
) -> list[dict]:
    """Return within-receipt verification viz-cache dicts.

    Each dict represents one receipt with all three verification passes
    (place, format, financial math).

    Args:
        parquet_dir: Path containing LangSmith parquet exports.
        rows: Optional preloaded trace rows (for financial_validation spans).
        unified_rows: Unified S3 rows with metadata/financial all_decisions.
        data_rows: Data S3 rows with place info and word bboxes.

    Returns:
        List of viz-cache dicts, one per receipt.
    """
    if not unified_rows:
        logger.info("No unified rows provided; returning empty cache")
        return []

    # Build data lookup: (image_id, receipt_id) → data row
    data_lookup: dict[tuple[str, int], dict] = {}
    if data_rows:
        for dr in data_rows:
            image_id = dr.get("image_id")
            receipt_id = dr.get("receipt_id")
            if image_id and receipt_id is not None:
                data_lookup[(str(image_id), int(receipt_id))] = dr

    # Build financial validation lookup from trace rows: trace_id → equations
    financial_trace_lookup: dict[str, list[dict]] = {}
    if rows:
        # Build root metadata for fallback
        root_meta: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.get("name") == "ReceiptEvaluation":
                tid = row.get("trace_id")
                extra = _parse_json(row.get("extra"))
                if extra and tid:
                    meta = extra.get("metadata", {})
                    root_meta[tid] = {
                        "image_id": meta.get("image_id"),
                        "receipt_id": meta.get("receipt_id"),
                    }

        for row in rows:
            if row.get("name") != "financial_validation":
                continue
            outputs = _parse_json(row.get("outputs"))
            if not outputs:
                continue
            output_list = outputs.get("output", [])
            if not output_list:
                continue

            inputs = _parse_json(row.get("inputs"))
            if not inputs:
                continue

            trace_id = row.get("trace_id")
            visual_lines = inputs.get("visual_lines", [])
            vl_lookup: dict[tuple[int, int], dict] = {}
            for vl in visual_lines:
                for w in vl.get("words", []):
                    word = w.get("word", {})
                    lid = word.get("line_id")
                    wid = word.get("word_id")
                    if lid is not None and wid is not None:
                        vl_lookup[(lid, wid)] = word

            equations = _build_financial_equations(output_list, vl_lookup)
            if trace_id and equations:
                financial_trace_lookup[trace_id] = equations

    logger.info(
        "Building within-receipt cache: %d unified rows, %d data rows, "
        "%d financial traces",
        len(unified_rows),
        len(data_lookup),
        len(financial_trace_lookup),
    )

    results: list[dict] = []
    for urow in unified_rows:
        image_id = urow.get("image_id")
        receipt_id = urow.get("receipt_id")
        if not image_id:
            continue

        trace_id = urow.get("trace_id", "")
        merchant_name = urow.get("merchant_name", "")

        # Lookup data row for place + words + CDN keys
        data_row = data_lookup.get(
            (str(image_id), int(receipt_id))
        ) if receipt_id is not None else None

        # Words and bbox lookup
        raw_words = data_row.get("words", []) if data_row else []
        word_lookup = _build_word_lookup(raw_words)

        # Place data
        place_raw = data_row.get("place") if data_row else None
        place_parsed = _parse_json(place_raw) if isinstance(place_raw, str) else place_raw
        place_info = _extract_place_info(place_parsed)

        # Parse metadata_all_decisions
        metadata_raw = urow.get("metadata_all_decisions")
        metadata_decisions = _parse_json(metadata_raw) if metadata_raw else None
        if not isinstance(metadata_decisions, list):
            metadata_decisions = []

        # Split into place and format
        place_decisions, format_decisions = _split_metadata_decisions(
            metadata_decisions, word_lookup,
        )

        # Parse financial_all_decisions from unified row
        financial_raw = urow.get("financial_all_decisions")
        financial_decisions = _parse_json(financial_raw) if financial_raw else None
        if not isinstance(financial_decisions, list):
            financial_decisions = []

        # Build financial equations - prefer trace data, fall back to unified
        equations = financial_trace_lookup.get(trace_id, [])
        if not equations and financial_decisions:
            equations = _build_financial_equations(
                financial_decisions, word_lookup,
            )

        # Duration seconds
        metadata_duration = urow.get("metadata_duration_seconds")
        financial_duration = urow.get("financial_duration_seconds")

        # Split metadata duration proportionally between place and format
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

        # CDN keys from data row (cdn_s3_key is required)
        cdn_keys: dict[str, str] = {"cdn_s3_key": ""}
        if data_row:
            for key in (
                "cdn_s3_key", "cdn_webp_s3_key", "cdn_avif_s3_key",
                "cdn_medium_s3_key", "cdn_medium_webp_s3_key",
                "cdn_medium_avif_s3_key",
            ):
                val = data_row.get(key)
                if val is not None:
                    cdn_keys[key] = val

        # Width / height
        width = (data_row.get("width", 0) or 0) if data_row else 0
        height = (data_row.get("height", 0) or 0) if data_row else 0

        result: dict[str, Any] = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "trace_id": trace_id,
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
                "summary": _build_financial_summary(equations),
                "duration_seconds": financial_duration,
                "is_llm": True,
            },
            "words": _build_word_list(raw_words),
            "width": width,
            "height": height,
            **cdn_keys,
        }

        results.append(result)

    logger.info(
        "Built within-receipt cache for %d receipts",
        len(results),
    )
    return results

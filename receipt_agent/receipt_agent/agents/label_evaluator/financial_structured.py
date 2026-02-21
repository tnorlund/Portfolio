"""
Structured financial validation: two-tier approach for receipt math checking.

Tier 1 (fast path): Deterministic equation balancing via extract_financial_values_enhanced()
Tier 2 (fallback): Single structured-output LLM call to propose equations

This module is consumed by:
  - unified_receipt_evaluator.py (step function Lambda, Phase 2)
  - dev_financial_structured_test.py (local dev/test harness)

Output format matches evaluate_financial_math_async() so the apply logic
in the unified evaluator works unchanged.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from statistics import median
from typing import Any, Literal, Optional

from pydantic import BaseModel

from receipt_agent.agents.label_evaluator.financial_subagent import (
    FLOAT_EPSILON,
    _build_valid_decisions,
    _text_scan_to_financial_values,
    detect_math_issues_from_values,
    extract_financial_values_enhanced,
    extract_number,
    filter_junk_values,
    scan_receipt_text,
)
from receipt_agent.utils.structured_output import (
    StructuredOutputResult,
    ainvoke_structured_with_retry,
    get_structured_output_settings,
    invoke_structured_with_retry,
)

logger = logging.getLogger(__name__)

# Financial labels the structured agent cares about
FINANCIAL_LABELS = {
    "GRAND_TOTAL",
    "SUBTOTAL",
    "TAX",
    "TIP",
    "LINE_TOTAL",
    "DISCOUNT",
    "UNIT_PRICE",
    "QUANTITY",
    "CASH_BACK",
}


@dataclass
class TwoTierFinancialResult:
    """Result from the two-tier financial validation pipeline."""

    tier: str  # "fast_path" | "llm" | "no_data"
    status: str  # "balanced" | "issues" | "no_equation" | "single_label" | "no_data"
    decisions: list[dict]  # evaluator-format decisions
    duration_seconds: float
    text_scan_used: bool
    label_type_count: int
    subtotal_mismatch_gap: float = 0.0
    phantom_values_filtered: bool = False
    reocr_region: dict[str, float] | None = None


# ---------------------------------------------------------------------------
# Pydantic schemas for structured output
# ---------------------------------------------------------------------------


class EquationProposal(BaseModel):
    """LLM's proposal for one equation to check."""

    equation_type: Literal["grand_total", "subtotal", "grand_total_direct"]
    grand_total_ref: Optional[list[int]] = None  # [line_id, word_id]
    subtotal_ref: Optional[list[int]] = None
    tax_refs: list[list[int]] = []  # [[line_id, word_id], ...]
    tip_refs: list[list[int]] = []
    line_total_refs: list[list[int]] = []
    discount_refs: list[list[int]] = []
    reasoning: str


class FinancialProposalResponse(BaseModel):
    """Structured response from the LLM."""

    has_equation: bool
    proposals: list[EquationProposal] = []
    no_equation_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

STRUCTURED_AGENT_SYSTEM_PROMPT = """\
You are a financial receipt validator. Given a receipt's OCR text and financial labels,
determine which words form balanced equations.

Equations to check (priority order):
1. grand_total: GRAND_TOTAL = SUBTOTAL + TAX (+ TIP if present)
2. subtotal: SUBTOTAL = sum(LINE_TOTALs) - DISCOUNTs
3. grand_total_direct: GRAND_TOTAL = sum(LINE_TOTALs) + TAX (when no SUBTOTAL exists)

Reference words by [line_id, word_id] from the financial values table.
If no equation is possible (missing data, only 1 value), set has_equation=false.

Rules:
- INVALID labels may still be numerically correct
- Keywords ("SUBTOTAL") may be lines away from their values
- Single-item receipts: GT = LT (+ TAX) is valid
- Only use values present on the receipt
- Propose as many equations as you can identify (grand_total, subtotal, grand_total_direct)
"""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def render_receipt_text(words: list, labels: list, visual_lines: list) -> str:
    """Render the full receipt with inline labels."""
    label_map: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for lbl in labels:
        key = (lbl.line_id, lbl.word_id)
        status = getattr(lbl, "validation_status", "NONE") or "NONE"
        label_map.setdefault(key, []).append((lbl.label, status))

    lines_out = []
    for vl in visual_lines:
        parts = []
        for wc in vl.words:
            w = wc.word
            text = w.text
            key = (w.line_id, w.word_id)
            if key in label_map:
                tags = " ".join(
                    f"[{ln}:{st}]" for ln, st in label_map[key]
                )
                parts.append(f"{text}{tags}")
            else:
                parts.append(text)
        lines_out.append(f"L{vl.line_index:>3d}: {' '.join(parts)}")

    return "\n".join(lines_out)


def render_financial_values(words: list, labels: list) -> str:
    """Render the financial values table."""
    rows = []
    for lbl in labels:
        if lbl.label not in FINANCIAL_LABELS:
            continue
        word_text = "?"
        for w in words:
            if w.line_id == lbl.line_id and w.word_id == lbl.word_id:
                word_text = w.text
                break
        numeric = extract_number(word_text)
        status = getattr(lbl, "validation_status", "NONE") or "NONE"
        rows.append(
            f"  L{lbl.line_id:>3d} W{lbl.word_id:>2d} | "
            f"{word_text:>12s} | {lbl.label:<14s} | {status:<12s} | "
            f"{'%.2f' % numeric if numeric is not None else 'N/A'}"
        )

    if not rows:
        return "No financial labels found on this receipt."

    header = (
        "  Line  Wrd |         Text | Label          | Status       | Value"
    )
    sep = "  " + "-" * 72
    return "\n".join([header, sep] + rows)


def build_structured_prompt(
    receipt_text: str,
    financial_values: str,
    merchant: str,
    fast_path_summary: str,
) -> list[tuple[str, str]]:
    """Build the message list for the structured-output LLM call."""
    human_parts = [
        f"Merchant: {merchant}\n",
        "=== Receipt Text ===\n",
        receipt_text,
        "\n\n=== Financial Values ===\n",
        financial_values,
    ]
    if fast_path_summary:
        human_parts.append(
            f"\n\nThe automated fast-path extraction failed with:\n"
            f"  {fast_path_summary}\n"
            f"Look at the raw receipt data and find the correct equation."
        )

    return [
        ("system", STRUCTURED_AGENT_SYSTEM_PROMPT),
        ("human", "".join(human_parts)),
    ]


# ---------------------------------------------------------------------------
# Equation checker (deterministic)
# ---------------------------------------------------------------------------


def check_equation(
    proposal: EquationProposal,
    word_lookup: dict[tuple[int, int], str],
    label_lookup: dict[tuple[int, int], list[tuple[str, str]]],
) -> dict:
    """Check whether a proposed equation balances.

    Returns:
        {
            "balanced": bool,
            "equation_words": [(role, line_id, word_id, value, text), ...],
            "details": str,
        }
    """
    errors: list[str] = []
    resolved: list[tuple[str, int, int, float, str]] = []

    def resolve_single(ref, role):
        if ref is None or len(ref) != 2:
            return None
        lid, wid = ref[0], ref[1]
        text = word_lookup.get((lid, wid))
        if text is None:
            errors.append(f"{role}: No word at L{lid} W{wid}")
            return None
        numeric = extract_number(text)
        if numeric is None:
            errors.append(f"{role}: '{text}' at L{lid} W{wid} is not numeric")
            return None
        resolved.append((role, lid, wid, numeric, text))
        return numeric

    def resolve_list(refs, role):
        vals = []
        for ref in refs:
            v = resolve_single(ref, role)
            if v is not None:
                vals.append(v)
        return vals

    gt = resolve_single(proposal.grand_total_ref, "GRAND_TOTAL")
    st = resolve_single(proposal.subtotal_ref, "SUBTOTAL")
    taxes = resolve_list(proposal.tax_refs, "TAX")
    tips = resolve_list(proposal.tip_refs, "TIP")
    lts = resolve_list(proposal.line_total_refs, "LINE_TOTAL")
    discs = resolve_list(proposal.discount_refs, "DISCOUNT")

    tax_sum = sum(taxes)
    tip_sum = sum(tips)
    lt_sum = sum(lts)
    disc_sum = sum(abs(d) for d in discs)

    result_lines: list[str] = []

    result_lines.append("Resolved values:")
    for role, lid, wid, val, text in resolved:
        result_lines.append(f"  {role}: {text} = {val:.2f}  (L{lid} W{wid})")

    if errors:
        result_lines.append("Resolution errors:")
        for e in errors:
            result_lines.append(f"  - {e}")

    balanced = False
    eq_type = proposal.equation_type

    if eq_type == "grand_total":
        if gt is None or st is None:
            result_lines.append(
                "ERROR: grand_total requires both grand_total_ref and subtotal_ref."
            )
            return {
                "balanced": False,
                "equation_words": resolved,
                "details": "\n".join(result_lines),
            }
        expected = st + tax_sum + tip_sum
        diff = gt - expected
        balanced = abs(diff) <= FLOAT_EPSILON
        result_lines.append(
            f"\nGRAND_TOTAL check: {gt:.2f} = {st:.2f} "
            f"+ TAX({tax_sum:.2f}) + TIP({tip_sum:.2f}) = {expected:.2f}"
        )
        result_lines.append(
            f"Difference: {diff:.2f}  →  {'BALANCED' if balanced else 'MISMATCH'}"
        )

    elif eq_type == "subtotal":
        if st is None:
            result_lines.append("ERROR: subtotal requires subtotal_ref.")
            return {
                "balanced": False,
                "equation_words": resolved,
                "details": "\n".join(result_lines),
            }
        expected = lt_sum - disc_sum
        diff = st - expected
        balanced = abs(diff) <= FLOAT_EPSILON
        result_lines.append(
            f"\nSUBTOTAL check: {st:.2f} = sum(LT)({lt_sum:.2f}) "
            f"- DISC({disc_sum:.2f}) = {expected:.2f}"
        )
        result_lines.append(
            f"Difference: {diff:.2f}  →  {'BALANCED' if balanced else 'MISMATCH'}"
        )

    elif eq_type == "grand_total_direct":
        if gt is None:
            result_lines.append(
                "ERROR: grand_total_direct requires grand_total_ref."
            )
            return {
                "balanced": False,
                "equation_words": resolved,
                "details": "\n".join(result_lines),
            }
        expected = lt_sum + tax_sum
        diff = gt - expected
        balanced = abs(diff) <= FLOAT_EPSILON
        result_lines.append(
            f"\nGRAND_TOTAL_DIRECT check: {gt:.2f} = "
            f"sum(LT)({lt_sum:.2f}) + TAX({tax_sum:.2f}) = {expected:.2f}"
        )
        result_lines.append(
            f"Difference: {diff:.2f}  →  {'BALANCED' if balanced else 'MISMATCH'}"
        )

    else:
        result_lines.append(f"Unknown equation_type '{eq_type}'.")

    return {
        "balanced": balanced,
        "equation_words": resolved,
        "details": "\n".join(result_lines),
    }


# ---------------------------------------------------------------------------
# Format adapter: equation results → evaluator format
# ---------------------------------------------------------------------------


def _to_evaluator_format(
    equation_words: list[tuple],
    label_lookup: dict[tuple[int, int], list[tuple[str, str]]],
    balanced: bool,
    equation_type: str,
    image_id: str,
    receipt_id: int,
) -> list[dict]:
    """Convert equation check results to the unified evaluator decision format.

    Each word produces:
        {
            "image_id": str, "receipt_id": int,
            "issue": {"line_id": int, "word_id": int,
                      "current_label": str, "word_text": str},
            "llm_review": {"decision": "VALID"|"NEEDS_REVIEW",
                           "reasoning": str, "suggested_label": None,
                           "confidence": "high"|"medium"}
        }
    """
    decisions: list[dict] = []
    seen: set[tuple] = set()

    for role, lid, wid, val, text in equation_words:
        key = (lid, wid, role)
        if key in seen:
            continue
        seen.add(key)

        decisions.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "issue": {
                    "line_id": lid,
                    "word_id": wid,
                    "current_label": role,
                    "word_text": text,
                },
                "llm_review": {
                    "decision": "VALID" if balanced else "NEEDS_REVIEW",
                    "reasoning": (
                        f"Used as {role}={val:.2f} in {equation_type} "
                        f"equation (structured)"
                    ),
                    "suggested_label": None,
                    "confidence": "high" if balanced else "medium",
                },
            }
        )

    return decisions


# ---------------------------------------------------------------------------
# Runner functions
# ---------------------------------------------------------------------------


def _run_structured_core(
    so_result: StructuredOutputResult[FinancialProposalResponse],
    words: list,
    labels: list,
    image_id: str,
    receipt_id: int,
) -> list[dict]:
    """Shared logic for sync and async runners: process structured output result."""
    if not so_result.success or so_result.response is None:
        logger.warning(
            "Structured output failed after %d attempts: %s",
            so_result.attempts,
            so_result.error_message or "unknown",
        )
        return []

    response = so_result.response

    if not response.has_equation or not response.proposals:
        logger.info(
            "LLM says no equation: %s",
            response.no_equation_reason or "unknown",
        )
        return []

    # Build lookups
    word_lookup: dict[tuple[int, int], str] = {}
    for w in words:
        word_lookup[(w.line_id, w.word_id)] = w.text

    label_lookup: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for lbl in labels:
        key = (lbl.line_id, lbl.word_id)
        status = getattr(lbl, "validation_status", "NONE") or "NONE"
        label_lookup.setdefault(key, []).append((lbl.label, status))

    # Try each proposal — return first balanced, or best effort
    best_result: tuple[EquationProposal, dict] | None = None

    for proposal in response.proposals:
        eq_result = check_equation(proposal, word_lookup, label_lookup)

        if eq_result["balanced"]:
            return _to_evaluator_format(
                eq_result["equation_words"],
                label_lookup,
                balanced=True,
                equation_type=proposal.equation_type,
                image_id=image_id,
                receipt_id=receipt_id,
            )

        if best_result is None:
            best_result = (proposal, eq_result)

    # No equation balanced — return NEEDS_REVIEW from first proposal
    if best_result is not None:
        proposal, eq_result = best_result
        return _to_evaluator_format(
            eq_result["equation_words"],
            label_lookup,
            balanced=False,
            equation_type=proposal.equation_type,
            image_id=image_id,
            receipt_id=receipt_id,
        )

    return []


def run_structured_financial_validation(
    llm: Any,
    words: list,
    labels: list,
    visual_lines: list,
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
    fast_path_summary: str = "",
) -> list[dict]:
    """Run Tier 2 structured-output financial validation (sync).

    Returns list[dict] in the same format as evaluate_financial_math_async().
    """
    receipt_text = render_receipt_text(words, labels, visual_lines)
    financial_values = render_financial_values(words, labels)
    messages = build_structured_prompt(
        receipt_text, financial_values, merchant_name, fast_path_summary,
    )

    _, retries = get_structured_output_settings(logger_instance=logger)

    try:
        so_result: StructuredOutputResult[FinancialProposalResponse] = (
            invoke_structured_with_retry(
                llm=llm,
                schema=FinancialProposalResponse,
                input_payload=messages,
                retries=retries,
            )
        )
    except Exception as e:
        logger.warning("Structured output error: %s", e, exc_info=True)
        return []

    return _run_structured_core(
        so_result, words, labels, image_id, receipt_id,
    )


async def run_structured_financial_validation_async(
    llm: Any,
    words: list,
    labels: list,
    visual_lines: list,
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
    fast_path_summary: str = "",
) -> list[dict]:
    """Run Tier 2 structured-output financial validation (async).

    Returns list[dict] in the same format as evaluate_financial_math_async().
    """
    receipt_text = render_receipt_text(words, labels, visual_lines)
    financial_values = render_financial_values(words, labels)
    messages = build_structured_prompt(
        receipt_text, financial_values, merchant_name, fast_path_summary,
    )

    _, retries = get_structured_output_settings(logger_instance=logger)

    try:
        so_result: StructuredOutputResult[FinancialProposalResponse] = (
            await ainvoke_structured_with_retry(
                llm=llm,
                schema=FinancialProposalResponse,
                input_payload=messages,
                retries=retries,
            )
        )
    except Exception as e:
        logger.warning("Structured output error: %s", e, exc_info=True)
        return []

    return _run_structured_core(
        so_result, words, labels, image_id, receipt_id,
    )


# ---------------------------------------------------------------------------
# Two-tier orchestration
# ---------------------------------------------------------------------------


def _status_value(status: Any) -> str:
    """Normalize enum-or-string validation statuses to string values."""
    if hasattr(status, "value"):
        return str(status.value)
    return str(status or "NONE")


def _compute_reocr_region(words: list, labels: list) -> dict[str, float]:
    """Compute a right-side crop region for targeted price re-OCR.

    Region coordinates use Vision-style normalized image space
    (origin at bottom-left). We currently keep full-height crops
    (y=0, height=1) and only tune x/width for totals columns.
    """
    # Default: rightmost 30% plus 5% horizontal padding on each side.
    default_region = {"x": 0.65, "y": 0.0, "width": 0.35, "height": 1.0}

    if not words or not labels:
        return default_region

    word_lookup = {
        (getattr(w, "line_id", 0), getattr(w, "word_id", 0)): w
        for w in words
    }
    valid_line_total_x: list[float] = []
    for lbl in labels:
        if getattr(lbl, "label", "") != "LINE_TOTAL":
            continue
        if _status_value(getattr(lbl, "validation_status", "NONE")) != "VALID":
            continue
        key = (getattr(lbl, "line_id", 0), getattr(lbl, "word_id", 0))
        word = word_lookup.get(key)
        if word is None:
            continue
        bbox = getattr(word, "bounding_box", {}) or {}
        x_val = bbox.get("x")
        if isinstance(x_val, (int, float)):
            valid_line_total_x.append(float(x_val))

    if not valid_line_total_x:
        return default_region

    # Anchor the crop slightly left of the median LINE_TOTAL x-position,
    # then add 5% horizontal padding so both the Swift crop and Python
    # overlay use identical geometry (see PR #820 review).
    x_anchor = median(valid_line_total_x)
    raw_width = 0.30
    raw_x = max(0.0, min(x_anchor - 0.05, 1.0 - raw_width))
    padding = 0.05
    padded_x = max(0.0, raw_x - padding)
    padded_right = min(1.0, raw_x + raw_width + padding)
    padded_width = max(0.01, padded_right - padded_x)
    return {"x": padded_x, "y": 0.0, "width": padded_width, "height": 1.0}


def _subtotal_mismatch_gap(math_issues: list) -> float:
    """Return absolute subtotal mismatch gap if present."""
    for issue in math_issues:
        if getattr(issue, "issue_type", "") == "SUBTOTAL_MISMATCH":
            expected = float(getattr(issue, "expected_value", 0.0))
            actual = float(getattr(issue, "actual_value", 0.0))
            return abs(expected - actual)
    return 0.0


def _run_two_tier_core(
    words,
    labels,
    visual_lines,
    chroma_client,
    image_id: str,
    receipt_id: int,
    merchant_name: str,
    *,
    skip_fast_path: bool,
) -> tuple[
    str,
    str,
    list[dict],
    bool,
    int,
    str,
    float,
    bool,
    dict[str, float] | None,
]:
    """Shared Tier-1 logic for sync and async.

    Returns:
    (tier, status, decisions, text_scan_used, label_type_count, fast_summary,
    subtotal_mismatch_gap, phantom_values_filtered, reocr_region).
    When tier == "__need_llm__" the caller must run the Tier-2 LLM call.
    """
    if not words or not labels:
        return ("no_data", "no_data", [], False, 0, "", 0.0, False, None)

    has_fin = any(lbl.label in FINANCIAL_LABELS for lbl in labels)
    if not has_fin:
        return ("no_data", "no_data", [], False, 0, "", 0.0, False, None)

    # Tier 1: fast path (deterministic)
    raw_enhanced_values = extract_financial_values_enhanced(
        visual_lines, words, labels, chroma_client, merchant_name,
    )
    enhanced_values = filter_junk_values(raw_enhanced_values)
    phantom_values_filtered = len(raw_enhanced_values.get("LINE_TOTAL", [])) > len(
        enhanced_values.get("LINE_TOTAL", [])
    )

    # Text-scan supplement: when label-based extraction finds <2 label
    # types, merge keyword-based text-scan findings.
    label_types_with_values = [k for k, v in enhanced_values.items() if v]
    text_scan_used = False
    if len(label_types_with_values) < 2:
        scanned = scan_receipt_text(words)
        scan_values = _text_scan_to_financial_values(
            scanned, words, visual_lines,
        )
        for scan_label, scan_fvs in scan_values.items():
            if scan_label not in enhanced_values or not enhanced_values[scan_label]:
                enhanced_values[scan_label] = scan_fvs
                text_scan_used = True
        label_types_with_values = [k for k, v in enhanced_values.items() if v]

    math_issues = detect_math_issues_from_values(enhanced_values)
    subtotal_gap = _subtotal_mismatch_gap(math_issues)
    reocr_region = _compute_reocr_region(words, labels) if subtotal_gap > 0 else None
    has_values = any(bool(v) for v in enhanced_values.values())
    label_type_count = len(label_types_with_values)

    if not skip_fast_path and not math_issues and has_values:
        if label_type_count >= 2:
            decisions = _build_valid_decisions(
                enhanced_values, image_id, receipt_id,
                "structured_fast_path_balanced",
            )
            return (
                "fast_path",
                "balanced",
                decisions,
                text_scan_used,
                label_type_count,
                "",
                subtotal_gap,
                phantom_values_filtered,
                reocr_region,
            )
        else:
            return (
                "fast_path",
                "single_label",
                [],
                text_scan_used,
                label_type_count,
                "",
                subtotal_gap,
                phantom_values_filtered,
                reocr_region,
            )

    # Need Tier 2
    fast_summary = (
        "; ".join(f"{mi.issue_type}: {mi.description}" for mi in math_issues)
        if math_issues
        else "No financial values found"
    )
    return (
        "__need_llm__",
        "",
        [],
        text_scan_used,
        label_type_count,
        fast_summary,
        subtotal_gap,
        phantom_values_filtered,
        reocr_region,
    )


def _classify_llm_result(llm_decisions: list[dict]) -> tuple[str, str]:
    """Classify LLM decisions into (tier, status)."""
    has_valid = any(
        d.get("llm_review", {}).get("decision") == "VALID"
        for d in llm_decisions
    )
    if has_valid:
        return ("llm", "balanced")
    if not llm_decisions:
        return ("llm", "no_equation")
    return ("llm", "issues")


def run_two_tier_financial_validation(
    llm,
    words,
    labels,
    visual_lines,
    chroma_client,
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
    *,
    skip_fast_path: bool = False,
) -> TwoTierFinancialResult:
    """Run the full two-tier financial validation pipeline (sync).

    Tier 1: Deterministic equation balancing with text-scan supplement.
    Tier 2: Single structured-output LLM call (only when Tier 1 fails).
    """
    start = time.time()

    (
        tier,
        status,
        decisions,
        text_scan_used,
        label_type_count,
        fast_summary,
        subtotal_gap,
        phantom_values_filtered,
        reocr_region,
    ) = _run_two_tier_core(
        words, labels, visual_lines, chroma_client,
        image_id, receipt_id, merchant_name,
        skip_fast_path=skip_fast_path,
    )

    if tier != "__need_llm__":
        return TwoTierFinancialResult(
            tier=tier,
            status=status,
            decisions=decisions,
            duration_seconds=time.time() - start,
            text_scan_used=text_scan_used,
            label_type_count=label_type_count,
            subtotal_mismatch_gap=subtotal_gap,
            phantom_values_filtered=phantom_values_filtered,
            reocr_region=reocr_region,
        )

    # Tier 2: Structured LLM fallback
    llm_decisions = run_structured_financial_validation(
        llm=llm,
        words=words,
        labels=labels,
        visual_lines=visual_lines,
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name=merchant_name,
        fast_path_summary=fast_summary,
    )
    tier, status = _classify_llm_result(llm_decisions)

    return TwoTierFinancialResult(
        tier=tier,
        status=status,
        decisions=llm_decisions,
        duration_seconds=time.time() - start,
        text_scan_used=text_scan_used,
        label_type_count=label_type_count,
        subtotal_mismatch_gap=subtotal_gap,
        phantom_values_filtered=phantom_values_filtered,
        reocr_region=reocr_region,
    )


async def run_two_tier_financial_validation_async(
    llm,
    words,
    labels,
    visual_lines,
    chroma_client,
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
    *,
    skip_fast_path: bool = False,
) -> TwoTierFinancialResult:
    """Run the full two-tier financial validation pipeline (async).

    Tier 1: Deterministic equation balancing with text-scan supplement.
    Tier 2: Single structured-output LLM call (only when Tier 1 fails).
    """
    start = time.time()

    (
        tier,
        status,
        decisions,
        text_scan_used,
        label_type_count,
        fast_summary,
        subtotal_gap,
        phantom_values_filtered,
        reocr_region,
    ) = _run_two_tier_core(
        words, labels, visual_lines, chroma_client,
        image_id, receipt_id, merchant_name,
        skip_fast_path=skip_fast_path,
    )

    if tier != "__need_llm__":
        return TwoTierFinancialResult(
            tier=tier,
            status=status,
            decisions=decisions,
            duration_seconds=time.time() - start,
            text_scan_used=text_scan_used,
            label_type_count=label_type_count,
            subtotal_mismatch_gap=subtotal_gap,
            phantom_values_filtered=phantom_values_filtered,
            reocr_region=reocr_region,
        )

    # Tier 2: Structured LLM fallback
    llm_decisions = await run_structured_financial_validation_async(
        llm=llm,
        words=words,
        labels=labels,
        visual_lines=visual_lines,
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name=merchant_name,
        fast_path_summary=fast_summary,
    )
    tier, status = _classify_llm_result(llm_decisions)

    return TwoTierFinancialResult(
        tier=tier,
        status=status,
        decisions=llm_decisions,
        duration_seconds=time.time() - start,
        text_scan_used=text_scan_used,
        label_type_count=label_type_count,
        subtotal_mismatch_gap=subtotal_gap,
        phantom_values_filtered=phantom_values_filtered,
        reocr_region=reocr_region,
    )

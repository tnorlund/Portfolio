"""
Financial math validation subagent for label evaluation.

This subagent validates mathematical relationships between financial labels:
- GRAND_TOTAL = SUBTOTAL + TAX
- SUBTOTAL = sum(LINE_TOTAL)
- QTY × UNIT_PRICE = LINE_TOTAL (per line)

When math doesn't match, the LLM determines which value is likely wrong based on:
- OCR patterns (common misreads)
- Receipt context (surrounding text)
- Typical receipt structure

Input:
    - visual_lines: Words grouped by y-coordinate with current labels
    - image_id, receipt_id: For output format

Output:
    List of decisions ready for apply_llm_decisions():
    [
        {
            "image_id": "...",
            "receipt_id": 1,
            "issue": {"line_id": 5, "word_id": 3, "current_label": "GRAND_TOTAL"},
            "llm_review": {
                "decision": "VALID" | "INVALID" | "NEEDS_REVIEW",
                "reasoning": "...",
                "suggested_label": "SUBTOTAL" or None,
                "confidence": "high" | "medium" | "low",
            }
        },
        ...
    ]
"""

import asyncio
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from langchain_core.language_models import BaseChatModel
from langsmith import traceable
from pydantic import ValidationError

from receipt_agent.constants import FINANCIAL_MATH_LABELS
from receipt_agent.prompts.structured_outputs import (
    FinancialEvaluationResponse,
    extract_json_from_response,
)
from receipt_agent.utils import (
    LLMRateLimitError,
    ainvoke_structured_with_retry,
    build_structured_failure_decisions,
    build_word_chroma_id,
    get_structured_output_settings,
    invoke_structured_with_retry,
)

from .state import VisualLine, WordContext

logger = logging.getLogger(__name__)

# Tiny epsilon for floating point comparison (not a business tolerance)
# We only filter out differences that are essentially zero due to floating point math
# The LLM decides if a real discrepancy is acceptable based on receipt context
FLOAT_EPSILON = 0.001


@dataclass
class ScannedValue:
    """A keyword-value pair found by text scanning."""

    keyword: str  # TOTAL, SUBTOTAL, TAX, TIP, AMOUNT, BALANCE, AUTHORIZED
    value: float
    source: str  # raw text of the number
    line_id: int
    line_text: str


@dataclass
class TextScanMathIssue:
    """A math check result from text-scanned values."""

    issue_type: str  # HAS_TOTAL, TOTAL_CHECK, TIP_CHECK
    match: bool
    description: str
    expected_value: float | None = None
    actual_value: float | None = None
    difference: float = 0.0
    involved_scanned: list[ScannedValue] = field(default_factory=list)


@dataclass
class FinancialValue:
    """A word with a financial label and its numeric value."""

    word_context: WordContext
    label: str
    numeric_value: float
    line_index: int
    word_text: str


@dataclass
class MathIssue:
    """A detected math discrepancy."""

    issue_type: str  # "GRAND_TOTAL_MISMATCH", "SUBTOTAL_MISMATCH", "LINE_ITEM_MISMATCH"
    expected_value: float
    actual_value: float
    difference: float
    involved_values: list[FinancialValue]
    description: str


# =============================================================================
# Value Extraction Helpers
# =============================================================================


def extract_number(text: str) -> float | None:
    """
    Extract numeric value from text, handling currency symbols and formatting.

    Handles: $1,234.56, (1.23), -1.23, 1.23-, etc.
    """
    if not text:
        return None

    # Remove currency symbols and whitespace
    clean = text.strip()
    clean = re.sub(r"[$€£¥]", "", clean)
    clean = clean.replace(",", "")

    # Handle parentheses for negative (accounting format)
    is_negative = False
    if clean.startswith("(") and clean.endswith(")"):
        is_negative = True
        clean = clean[1:-1]

    # Handle trailing minus
    if clean.endswith("-"):
        is_negative = True
        clean = clean[:-1]

    # Handle leading minus
    if clean.startswith("-"):
        is_negative = True
        clean = clean[1:]

    try:
        value = float(clean)
        return -value if is_negative else value
    except ValueError:
        return None


def extract_financial_values(
    visual_lines: list[VisualLine],
) -> dict[str, list[FinancialValue]]:
    """
    Extract all financial values from visual lines, grouped by label.

    Returns:
        Dict mapping label -> list of FinancialValue
    """
    values_by_label: dict[str, list[FinancialValue]] = {}

    for line in visual_lines:
        for wc in line.words:
            if wc.current_label is None:
                continue

            label = wc.current_label.label
            if label not in FINANCIAL_MATH_LABELS:
                continue

            numeric = extract_number(wc.word.text)
            if numeric is None:
                # QUANTITY might be a whole number without decimal
                if label == "QUANTITY":
                    try:
                        numeric = float(int(wc.word.text.strip()))
                    except ValueError:
                        continue
                else:
                    continue

            fv = FinancialValue(
                word_context=wc,
                label=label,
                numeric_value=numeric,
                line_index=line.line_index,
                word_text=wc.word.text,
            )

            if label not in values_by_label:
                values_by_label[label] = []
            values_by_label[label].append(fv)

    return values_by_label


# =============================================================================
# Enhanced Value Extraction (CHROMA_COLUMN approach)
# =============================================================================

# Summary labels that need column alignment filtering
SUMMARY_LABELS = {"GRAND_TOTAL", "SUBTOTAL", "TAX"}

# Line-item labels that should be scoped above the summary section
LINE_ITEM_LABELS = {"LINE_TOTAL", "UNIT_PRICE", "QUANTITY", "DISCOUNT"}

# Currency labels that should be column-filtered (summary + line-item prices)
_COLUMN_FILTERED_LABELS = {"GRAND_TOTAL", "SUBTOTAL", "TAX", "LINE_TOTAL"}

# Validation status ranking for picking the best label when duplicates exist
_STATUS_RANK = {
    "VALID": 4,
    "INVALID": 3,
    "PENDING": 2,
    "NEEDS_REVIEW": 1,
    "NONE": 0,
}


def _get_right_edge(word: Any) -> float:
    """Get right-edge x-position of a ReceiptWord.

    Currency values on receipts are right-aligned, so comparing right edges
    gives tighter column alignment than left edges.
    """
    if hasattr(word, "top_right") and word.top_right and "x" in word.top_right:
        return float(word.top_right["x"])
    bb = word.bounding_box
    return float(bb["x"]) + float(bb.get("width", 0))


def _detect_price_column(words: list, labels: list) -> float | None:
    """Detect right-edge x of the price column from VALID financial words.

    Returns median right-edge x of VALID financial-labeled numeric words,
    or None if insufficient data.
    """
    valid_fin: dict[tuple[int, int], str] = {}
    for lbl in labels:
        if (
            getattr(lbl, "validation_status", None) == "VALID"
            and lbl.label in FINANCIAL_MATH_LABELS
        ):
            valid_fin[(lbl.line_id, lbl.word_id)] = lbl.label

    right_edges = []
    for w in words:
        key = (w.line_id, w.word_id)
        if key in valid_fin and extract_number(w.text) is not None:
            right_edges.append(_get_right_edge(w))

    if not right_edges:
        return None

    return median(right_edges)


def _chroma_get_label_column_x(
    chroma_client: Any,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    target_label: str,
    merchant_name: str,
    n_results: int = 15,
    min_similarity: float = 0.70,
) -> tuple[float | None, float, int, int]:
    """Query ChromaDB for the typical right-edge x-position of a label.

    Finds similar validated words in ChromaDB and returns:
    - median_x: median right-edge x of positively-matching words (or None)
    - consensus: -1.0 to 1.0 score
    - pos_count, neg_count
    """
    chroma_id = build_word_chroma_id(image_id, receipt_id, line_id, word_id)

    # Get the word's embedding
    try:
        result = chroma_client.get(
            collection_name="words",
            ids=[chroma_id],
            include=["embeddings"],
        )
        embeddings = result.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            return None, 0.0, 0, 0
        embedding = embeddings[0]
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()
    except Exception:
        return None, 0.0, 0, 0

    label_field = f"label_{target_label}"
    positive_weight = 0.0
    negative_weight = 0.0
    pos_count = 0
    neg_count = 0
    positive_x_positions: list[float] = []

    for label_val in [True, False]:
        try:
            results = chroma_client.query(
                collection_name="words",
                query_embeddings=[embedding],
                n_results=n_results,
                where={label_field: label_val},
                include=["metadatas", "distances"],
            )
        except Exception:
            continue

        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        for rid, dist, meta in zip(ids, distances, metadatas):
            if rid == chroma_id:
                continue
            similarity = max(0.0, 1.0 - (dist / 2.0))
            if similarity < min_similarity:
                continue

            weight = similarity
            m_name = meta.get("merchant_name", "")
            if (
                m_name
                and merchant_name
                and m_name.lower() == merchant_name.lower()
            ):
                weight += 0.1

            if label_val:
                positive_weight += weight
                pos_count += 1
                x = meta.get("x")
                w_width = meta.get("width")
                if x is not None:
                    right_edge = (
                        float(x) + float(w_width)
                        if w_width is not None
                        else float(x)
                    )
                    if 0.0 < right_edge <= 1.1:
                        positive_x_positions.append(right_edge)
            else:
                negative_weight += weight
                neg_count += 1

    total = positive_weight + negative_weight
    consensus = (
        (positive_weight - negative_weight) / total if total > 0 else 0.0
    )

    median_x = median(positive_x_positions) if positive_x_positions else None

    return median_x, consensus, pos_count, neg_count


def extract_financial_values_enhanced(
    visual_lines: list[VisualLine],
    words: list,
    labels: list,
    chroma_client: Any,
    merchant_name: str,
) -> dict[str, list[FinancialValue]]:
    """Enhanced extraction: ChromaDB column alignment + summary section dedup.

    Unlike extract_financial_values() which only uses VALID labels, this:
    1. Accepts labels of ANY validation status
    2. Filters misaligned non-VALID summary labels via ChromaDB right-edge x
    3. Detects the summary section boundary
    4. Deduplicates summary labels (keeps best per label)
    5. Scopes line items above the summary section

    Returns dict[str, list[FinancialValue]] -- same format as original.
    """
    # Step 1: Build candidate label map from all statuses
    label_map: dict[tuple[int, int], str] = {}
    label_status: dict[tuple[int, int], str] = {}
    for lbl in labels:
        if lbl.label in FINANCIAL_MATH_LABELS:
            key = (lbl.line_id, lbl.word_id)
            existing = label_status.get(key, "NONE")
            if _STATUS_RANK.get(
                getattr(lbl, "validation_status", "NONE"), 0
            ) >= _STATUS_RANK.get(existing, 0):
                label_map[key] = lbl.label
                label_status[key] = getattr(
                    lbl, "validation_status", "NONE"
                )

    # Step 2: Local column fallback
    local_col_x = _detect_price_column(words, labels)

    # Step 3: Build word lookup by (line_id, word_id) from ReceiptWord list
    word_lookup: dict[tuple[int, int], Any] = {}
    for w in words:
        word_lookup[(w.line_id, w.word_id)] = w

    # Step 4: Build WordContext lookup from visual_lines
    wc_lookup: dict[tuple[int, int], tuple[WordContext, int]] = {}
    for vl in visual_lines:
        for wc in vl.words:
            wc_lookup[(wc.word.line_id, wc.word.word_id)] = (
                wc,
                vl.line_index,
            )

    # Step 5: Collect numeric candidates with column filtering
    # Store as (line_id, label, value, text, word_context, line_index) tuples
    candidates: list[
        tuple[int, str, float, str, WordContext | None, int]
    ] = []
    x_tolerance = 0.20
    min_chroma_evidence = 3

    for w in words:
        key = (w.line_id, w.word_id)
        label = label_map.get(key)
        if label is None:
            continue
        num = extract_number(w.text)
        if num is None:
            if label == "QUANTITY":
                try:
                    num = float(int(w.text.strip()))
                except ValueError:
                    continue
            else:
                continue

        status = label_status.get(key, "NONE")
        wc_info = wc_lookup.get(key)
        wc = wc_info[0] if wc_info else None
        line_index = wc_info[1] if wc_info else w.line_id

        # VALID currency labels: local column sanity check.
        # Catches SKU fragments / MAX REFUND VALUE amounts that were
        # incorrectly validated (e.g., Home Depot code fragments on the
        # left edge labeled as LINE_TOTAL).  Uses only the cheap local
        # column x — no Chroma call needed for VALID words.
        if status == "VALID":
            if label in _COLUMN_FILTERED_LABELS and local_col_x is not None:
                word_right = _get_right_edge(w)
                diff = abs(word_right - local_col_x)
                if diff > x_tolerance:
                    logger.info(
                        "Column sanity check: VALID '%s' as %s "
                        "rejected (right=%.3f, col=%.3f, diff=%.3f)",
                        w.text,
                        label,
                        word_right,
                        local_col_x,
                        diff,
                    )
                    continue
            candidates.append(
                (w.line_id, label, num, w.text, wc, line_index)
            )
            continue

        # Non-VALID currency labels: column filter via Chroma + local
        if label in _COLUMN_FILTERED_LABELS:
            chroma_col_x, _consensus, pos_count, _neg_count = (
                _chroma_get_label_column_x(
                    chroma_client=chroma_client,
                    image_id=w.image_id if hasattr(w, "image_id") else "",
                    receipt_id=(
                        w.receipt_id if hasattr(w, "receipt_id") else 0
                    ),
                    line_id=w.line_id,
                    word_id=w.word_id,
                    target_label=label,
                    merchant_name=merchant_name,
                )
            )

            col_right = (
                chroma_col_x
                if chroma_col_x is not None
                and pos_count >= min_chroma_evidence
                else local_col_x
            )
            word_right = _get_right_edge(w)

            if col_right is not None:
                diff = abs(word_right - col_right)
                if diff > x_tolerance:
                    src = (
                        "chroma"
                        if chroma_col_x is not None
                        and pos_count >= min_chroma_evidence
                        else "local"
                    )
                    logger.debug(
                        "Enhanced extraction column filter: '%s' as %s "
                        "(right=%.3f, col=%.3f[%s], diff=%.3f)",
                        w.text,
                        label,
                        word_right,
                        col_right,
                        src,
                        diff,
                    )
                    continue

        candidates.append(
            (w.line_id, label, num, w.text, wc, line_index)
        )

    logger.info(
        "Enhanced extraction: %d numeric candidates after column filter",
        len(candidates),
    )

    # Step 6: Detect summary section (first SUBTOTAL or GRAND_TOTAL line)
    summary_start_line = None
    for line_id, label, _val, _txt, _wc, _li in candidates:
        if label in ("SUBTOTAL", "GRAND_TOTAL"):
            summary_start_line = line_id
            break

    # Step 7: Deduplicate summary labels
    # For each summary label, prefer in-block occurrence over pre-block
    summary_best: dict[str, tuple[int, float, str, WordContext | None, int]] = {}
    for line_id, label, val, txt, wc, li in candidates:
        if label not in SUMMARY_LABELS:
            continue
        if label not in summary_best:
            summary_best[label] = (line_id, val, txt, wc, li)
        elif summary_start_line is not None:
            existing_in_block = summary_best[label][0] >= summary_start_line
            current_in_block = line_id >= summary_start_line
            if current_in_block and not existing_in_block:
                summary_best[label] = (line_id, val, txt, wc, li)

    # Step 8: Build final values_by_label
    values_by_label: dict[str, list[FinancialValue]] = {}
    seen_summary: set[str] = set()

    for line_id, label, val, txt, wc, li in candidates:
        if label in SUMMARY_LABELS:
            if label in seen_summary:
                continue
            best = summary_best.get(label)
            if best and best[0] != line_id:
                continue
            seen_summary.add(label)
        elif label in LINE_ITEM_LABELS:
            if (
                summary_start_line is not None
                and line_id >= summary_start_line
            ):
                continue

        # Build a FinancialValue (use existing WordContext if available)
        if wc is None:
            # Create a minimal WordContext for words not in visual_lines
            from .state import WordContext as WC

            # Find the ReceiptWord matching this candidate's text on its line
            rw = None
            for k, v in word_lookup.items():
                if k[0] == line_id and v.text == txt:
                    rw = v
                    break
            if rw is None:
                continue
            wc = WC(word=rw)

        fv = FinancialValue(
            word_context=wc,
            label=label,
            numeric_value=val,
            line_index=li,
            word_text=txt,
        )
        if label not in values_by_label:
            values_by_label[label] = []
        values_by_label[label].append(fv)

    return values_by_label


# =============================================================================
# Visual Line Grouping
# =============================================================================


@dataclass
class ReceiptVisualLine:
    """A visual row on a receipt — words grouped by y-position.

    OCR can assign different ``line_id`` values to words that appear on
    the same visual row (e.g. product name on line 35, price on line 41).
    This groups words by their actual y-coordinate so that each instance
    represents one physical row of the receipt.

    The ``index`` is the visual line number shown to the LLM as
    ``Line N:`` — using this same object for both text building and word
    matching guarantees the mapping is consistent.
    """

    index: int  # Visual line number (what the LLM sees as "Line N:")
    words: list  # ReceiptWord objects, sorted left-to-right
    y_center: float  # Average y-coordinate of the row

    @property
    def text(self) -> str:
        """Concatenated text of all words on this line."""
        return " ".join(w.text for w in self.words)


def _get_y_center(word: Any) -> float:
    """Get y-center from a ReceiptWord's bounding box."""
    bb = getattr(word, "bounding_box", None) or {}
    y = bb.get("y", 0)
    h = bb.get("height", 0)
    return y + h / 2


def _group_into_visual_lines(words: list) -> list[ReceiptVisualLine]:
    """Group ReceiptWord objects into visual lines by y-coordinate proximity.

    Same algorithm as ``assemble_visual_lines`` in word_context.py but works
    directly on ReceiptWord objects (no WordContext wrapper needed).

    Returns list of :class:`ReceiptVisualLine` ordered top-to-bottom.
    """
    if not words:
        return []

    # Tolerance from median word height
    heights = [
        w.bounding_box.get("height", 0.02)
        for w in words
        if getattr(w, "bounding_box", None) and w.bounding_box.get("height")
    ]
    tol = max(0.01, median(heights) * 0.75) if heights else 0.015

    # Sort by y descending (top of receipt first, since y=1 is top)
    sorted_words = sorted(words, key=lambda w: -_get_y_center(w))

    raw_groups: list[list] = []
    current: list = [sorted_words[0]]
    current_y = _get_y_center(sorted_words[0])

    for w in sorted_words[1:]:
        yc = _get_y_center(w)
        if abs(yc - current_y) <= tol:
            current.append(w)
            current_y = sum(_get_y_center(cw) for cw in current) / len(current)
        else:
            current.sort(
                key=lambda cw: (getattr(cw, "bounding_box", None) or {}).get(
                    "x", 0
                )
            )
            raw_groups.append(current)
            current = [w]
            current_y = yc

    current.sort(
        key=lambda cw: (getattr(cw, "bounding_box", None) or {}).get("x", 0)
    )
    raw_groups.append(current)

    return [
        ReceiptVisualLine(
            index=i,
            words=grp,
            y_center=sum(_get_y_center(w) for w in grp) / len(grp),
        )
        for i, grp in enumerate(raw_groups)
    ]


# =============================================================================
# LLM Line-Item Fallback
# =============================================================================


def _build_receipt_text(visual_lines: list[ReceiptVisualLine]) -> str:
    """Build readable receipt text from pre-computed visual lines.

    Each visual line becomes a ``Line N: <text>`` row that the LLM sees.
    Because the same :class:`ReceiptVisualLine` list is reused for matching,
    the line numbers are guaranteed to be consistent.
    """
    return "\n".join(f"Line {vl.index}: {vl.text}" for vl in visual_lines)


def _match_llm_items_to_words(
    llm_items: list[dict],
    visual_lines: list[ReceiptVisualLine],
    label_type: str,
    dummy_word: Any,
) -> list[FinancialValue]:
    """Match LLM-returned line items back to real ReceiptWord objects.

    The LLM returns items with ``line`` (visual line index), ``text``, and
    ``amount``.  We look up the referenced :class:`ReceiptVisualLine` and
    search for a word whose numeric value matches the amount.  This gives
    us the real ``(line_id, word_id)`` coordinates for DynamoDB.

    Items that can't be matched (e.g. OCR truncation) get a *dummy_word*
    so the math still balances — they just won't produce VALID label writes.
    """
    from .state import WordContext as WC

    matched: list[FinancialValue] = []

    for item in llm_items:
        llm_line = item.get("line", -1)
        llm_amount = item.get("amount", 0)
        sign = -1 if label_type == "DISCOUNT" else 1

        # Find words on the visual line the LLM referenced (with ±2 fallback)
        found_word = None
        for offset in (0, -1, 1, -2, 2):
            idx = llm_line + offset
            if 0 <= idx < len(visual_lines):
                vl = visual_lines[idx]

                # Strategy 1: exact amount text
                amount_str = f"{llm_amount:.2f}"
                amount_str_dollar = f"${llm_amount:.2f}"
                for w in vl.words:
                    if w.text in (amount_str, amount_str_dollar):
                        found_word = w
                        break

                # Strategy 2: numeric value match
                if found_word is None:
                    for w in vl.words:
                        num = extract_number(w.text)
                        if num is not None and abs(abs(num) - llm_amount) < 0.01:
                            found_word = w
                            break

            if found_word is not None:
                break

        if found_word is not None:
            wc = WC(word=found_word)
            matched.append(
                FinancialValue(
                    word_context=wc,
                    label=label_type,
                    numeric_value=sign * llm_amount,
                    line_index=found_word.line_id,
                    word_text=found_word.text,
                )
            )
        else:
            # Unmatched — use dummy WordContext so math still balances
            wc = WC(word=dummy_word)
            matched.append(
                FinancialValue(
                    word_context=wc,
                    label=label_type,
                    numeric_value=sign * llm_amount,
                    line_index=item.get("line", 0),
                    word_text=item.get("text", str(llm_amount)),
                )
            )
            logger.info(
                "LLM item unmatched: %s $%.2f line=%d text=%r",
                label_type,
                llm_amount,
                llm_line,
                item.get("text", ""),
            )

    return matched


async def _llm_identify_line_items_async(
    llm: Any,
    visual_lines: list[ReceiptVisualLine],
    subtotal: float,
    line_totals: list[FinancialValue],
    discounts: list[FinancialValue],
    merchant_name: str,
    dummy_word: Any,
) -> dict[str, list[FinancialValue]] | None:
    """Ask LLM to identify all line items and discounts on the receipt.

    *visual_lines* is the single pre-computed grouping used for both the
    prompt text and the subsequent word matching — guaranteeing the LLM's
    ``line`` references map to the exact same rows we search.

    Returns dict with 'LINE_TOTAL' and 'DISCOUNT' keys containing
    FinancialValue lists, or None if the LLM couldn't balance the math.
    """
    receipt_text = _build_receipt_text(visual_lines)

    algo_lt_sum = sum(fv.numeric_value for fv in line_totals)
    algo_disc_sum = sum(abs(fv.numeric_value) for fv in discounts)
    algo_expected = algo_lt_sum - algo_disc_sum
    diff = subtotal - algo_expected

    algo_lt_desc = "\n".join(
        f'  - ${fv.numeric_value:.2f} ("{fv.word_text}")'
        for fv in line_totals
    )
    algo_disc_desc = (
        "\n".join(
            f'  - ${abs(fv.numeric_value):.2f} ("{fv.word_text}")'
            for fv in discounts
        )
        if discounts
        else "  (none found)"
    )

    prompt = f"""You are analyzing a receipt from {merchant_name} to verify financial math.

We know the SUBTOTAL is ${subtotal:.2f}. The equation is:
  SUBTOTAL = sum(line item prices) - sum(discounts/coupons/voids)

Our algorithm found these line item prices (sum=${algo_lt_sum:.2f}):
{algo_lt_desc}

And these discounts (sum=${algo_disc_sum:.2f}):
{algo_disc_desc}

This gives ${algo_lt_sum:.2f} - ${algo_disc_sum:.2f} = ${algo_expected:.2f}, but SUBTOTAL is ${subtotal:.2f}.
The difference is ${diff:+.2f} — we need to find what's missing.

Here is the full receipt text:

{receipt_text}

Identify ALL line item prices and ALL discounts/coupons/voids on this receipt.

Rules:
- A "line item price" is the total price charged for a product (qty x unit_price). If a line shows "4 @ 25.09  103.92", the line item price is 103.92, NOT 25.09.
- Discounts, coupons, voids, and instant savings are negative amounts (often shown with trailing "-" like "3.00-"). These REDUCE the subtotal.
- Deposits/fees/surcharges (like CA Redemption Value, bottle deposits, lumber fees) are positive charges that ADD to the subtotal — treat them as line items, not discounts.
- Do NOT include the SUBTOTAL, TAX, GRAND_TOTAL, CHANGE, or payment amounts themselves.
- Each amount should appear exactly once.
- Your line_items sum minus discounts sum MUST equal the SUBTOTAL of ${subtotal:.2f}.

Return ONLY valid JSON (no markdown, no explanation) in this format:
{{
  "line_items": [
    {{"line": <line_number>, "text": "<word on receipt>", "amount": <positive_number>}},
    ...
  ],
  "discounts": [
    {{"line": <line_number>, "text": "<word on receipt>", "amount": <positive_number>, "reason": "<coupon/void/savings>"}},
    ...
  ],
  "line_items_sum": <number>,
  "discounts_sum": <number>,
  "computed_subtotal": <number>
}}"""

    try:
        if hasattr(llm, "ainvoke"):
            response = await llm.ainvoke(prompt)
        else:
            response = await asyncio.to_thread(llm.invoke, prompt)

        raw = _response_to_text(response)
        raw = raw.strip()
        if not raw:
            logger.warning("LLM fallback returned empty response")
            return None

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        parsed = json.loads(raw)
        llm_line_items = parsed.get("line_items", [])
        llm_discounts = parsed.get("discounts", [])

        lt_sum = sum(item["amount"] for item in llm_line_items)
        disc_sum = sum(item["amount"] for item in llm_discounts)
        computed = lt_sum - disc_sum

        logger.info(
            "LLM fallback found: %d line items ($%.2f), "
            "%d discounts ($%.2f), computed=$%.2f vs subtotal=$%.2f",
            len(llm_line_items),
            lt_sum,
            len(llm_discounts),
            disc_sum,
            computed,
            subtotal,
        )

        # Accept if math balances within $0.05
        if abs(computed - subtotal) > 0.05:
            logger.info(
                "LLM fallback rejected: math doesn't balance "
                "(diff=$%.2f)",
                abs(computed - subtotal),
            )
            return None

        # Match LLM items back to real ReceiptWord objects — uses the
        # same visual_lines that built the prompt text.
        result_lt = _match_llm_items_to_words(
            llm_line_items, visual_lines, "LINE_TOTAL", dummy_word
        )
        result_disc = _match_llm_items_to_words(
            llm_discounts, visual_lines, "DISCOUNT", dummy_word
        )

        return {"LINE_TOTAL": result_lt, "DISCOUNT": result_disc}

    except json.JSONDecodeError as e:
        logger.warning("LLM fallback JSON parse error: %s", e)
        return None
    except Exception as e:
        logger.warning("LLM fallback error: %s: %s", type(e).__name__, e)
        return None


def _llm_identify_line_items(
    llm: Any,
    visual_lines: list[ReceiptVisualLine],
    subtotal: float,
    line_totals: list[FinancialValue],
    discounts: list[FinancialValue],
    merchant_name: str,
    dummy_word: Any,
) -> dict[str, list[FinancialValue]] | None:
    """Sync version of :func:`_llm_identify_line_items_async`."""
    receipt_text = _build_receipt_text(visual_lines)

    algo_lt_sum = sum(fv.numeric_value for fv in line_totals)
    algo_disc_sum = sum(abs(fv.numeric_value) for fv in discounts)
    algo_expected = algo_lt_sum - algo_disc_sum
    diff = subtotal - algo_expected

    algo_lt_desc = "\n".join(
        f'  - ${fv.numeric_value:.2f} ("{fv.word_text}")'
        for fv in line_totals
    )
    algo_disc_desc = (
        "\n".join(
            f'  - ${abs(fv.numeric_value):.2f} ("{fv.word_text}")'
            for fv in discounts
        )
        if discounts
        else "  (none found)"
    )

    prompt = f"""You are analyzing a receipt from {merchant_name} to verify financial math.

We know the SUBTOTAL is ${subtotal:.2f}. The equation is:
  SUBTOTAL = sum(line item prices) - sum(discounts/coupons/voids)

Our algorithm found these line item prices (sum=${algo_lt_sum:.2f}):
{algo_lt_desc}

And these discounts (sum=${algo_disc_sum:.2f}):
{algo_disc_desc}

This gives ${algo_lt_sum:.2f} - ${algo_disc_sum:.2f} = ${algo_expected:.2f}, but SUBTOTAL is ${subtotal:.2f}.
The difference is ${diff:+.2f} — we need to find what's missing.

Here is the full receipt text:

{receipt_text}

Identify ALL line item prices and ALL discounts/coupons/voids on this receipt.

Rules:
- A "line item price" is the total price charged for a product (qty x unit_price). If a line shows "4 @ 25.09  103.92", the line item price is 103.92, NOT 25.09.
- Discounts, coupons, voids, and instant savings are negative amounts (often shown with trailing "-" like "3.00-"). These REDUCE the subtotal.
- Deposits/fees/surcharges (like CA Redemption Value, bottle deposits, lumber fees) are positive charges that ADD to the subtotal — treat them as line items, not discounts.
- Do NOT include the SUBTOTAL, TAX, GRAND_TOTAL, CHANGE, or payment amounts themselves.
- Each amount should appear exactly once.
- Your line_items sum minus discounts sum MUST equal the SUBTOTAL of ${subtotal:.2f}.

Return ONLY valid JSON (no markdown, no explanation) in this format:
{{
  "line_items": [
    {{"line": <line_number>, "text": "<word on receipt>", "amount": <positive_number>}},
    ...
  ],
  "discounts": [
    {{"line": <line_number>, "text": "<word on receipt>", "amount": <positive_number>, "reason": "<coupon/void/savings>"}},
    ...
  ],
  "line_items_sum": <number>,
  "discounts_sum": <number>,
  "computed_subtotal": <number>
}}"""

    try:
        response = llm.invoke(prompt)

        raw = _response_to_text(response)
        raw = raw.strip()
        if not raw:
            logger.warning("LLM fallback returned empty response")
            return None

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        parsed = json.loads(raw)
        llm_line_items = parsed.get("line_items", [])
        llm_discounts = parsed.get("discounts", [])

        lt_sum = sum(item["amount"] for item in llm_line_items)
        disc_sum = sum(item["amount"] for item in llm_discounts)
        computed = lt_sum - disc_sum

        logger.info(
            "LLM fallback found: %d line items ($%.2f), "
            "%d discounts ($%.2f), computed=$%.2f vs subtotal=$%.2f",
            len(llm_line_items),
            lt_sum,
            len(llm_discounts),
            disc_sum,
            computed,
            subtotal,
        )

        # Accept if math balances within $0.05
        if abs(computed - subtotal) > 0.05:
            logger.info(
                "LLM fallback rejected: math doesn't balance "
                "(diff=$%.2f)",
                abs(computed - subtotal),
            )
            return None

        # Match LLM items back to real ReceiptWord objects — uses the
        # same visual_lines that built the prompt text.
        result_lt = _match_llm_items_to_words(
            llm_line_items, visual_lines, "LINE_TOTAL", dummy_word
        )
        result_disc = _match_llm_items_to_words(
            llm_discounts, visual_lines, "DISCOUNT", dummy_word
        )

        return {"LINE_TOTAL": result_lt, "DISCOUNT": result_disc}

    except json.JSONDecodeError as e:
        logger.warning("LLM fallback JSON parse error: %s", e)
        return None
    except Exception as e:
        logger.warning("LLM fallback error: %s: %s", type(e).__name__, e)
        return None


def detect_math_issues_from_values(
    values: dict[str, list[FinancialValue]],
) -> list[MathIssue]:
    """Detect math issues from a pre-built values dict.

    Same logic as detect_math_issues() but accepts values directly
    instead of extracting from visual_lines.
    """
    issues = []

    grand_total_issue = check_grand_total_math(values)
    if grand_total_issue:
        issues.append(grand_total_issue)

    subtotal_issue = check_subtotal_math(values)
    if subtotal_issue:
        issues.append(subtotal_issue)

    line_issues = check_line_item_math(values)
    issues.extend(line_issues)

    return issues


def _build_valid_decisions(
    values: dict[str, list[FinancialValue]],
    image_id: str,
    receipt_id: int,
    source: str,
) -> list[dict[str, Any]]:
    """Build VALID decision dicts from math-confirmed financial values.

    When the financial math balances, every label that participated in
    the equation is confirmed correct.  This builds decision dicts
    compatible with ``apply_llm_decisions`` so those labels get written
    as VALID in DynamoDB.

    Only values with a real WordContext produce decisions.  Dummy
    WordContext (from unmatched LLM fallback items) is detected by
    comparing the ReceiptWord's text with the FinancialValue's
    word_text — a dummy wraps words[0] whose text won't match the
    financial value.

    Each word produces at most ONE decision to avoid conflicting writes.
    A word may appear under multiple labels (e.g. LINE_TOTAL and
    UNIT_PRICE). We pick the label from the dict key it appears under,
    prioritising the label that directly participated in a cross-label
    equation (summary labels and LINE_TOTAL/DISCOUNT over UNIT_PRICE
    and QUANTITY).
    """
    # Priority: summary labels and line-item labels that appear in the
    # SUBTOTAL equation first, then secondary per-line labels.
    _LABEL_PRIORITY = {
        "GRAND_TOTAL": 0,
        "SUBTOTAL": 1,
        "TAX": 2,
        "LINE_TOTAL": 3,
        "DISCOUNT": 4,
        "UNIT_PRICE": 5,
        "QUANTITY": 6,
    }

    decisions: list[dict[str, Any]] = []
    # One decision per word — (line_id, word_id) → (priority, decision_dict)
    best: dict[tuple[int, int], tuple[int, dict[str, Any]]] = {}

    for label_name, fvs in values.items():
        for fv in fvs:
            wc = fv.word_context
            if wc is None:
                continue

            w = wc.word
            lid = getattr(w, "line_id", None)
            wid = getattr(w, "word_id", None)
            if lid is None or wid is None:
                continue

            # Skip dummy WordContext: for a real match, the word's text
            # should parse to the same numeric value as the FinancialValue.
            # Dummies wrap words[0] which is typically a non-numeric word.
            word_num = extract_number(getattr(w, "text", ""))
            if word_num is None or abs(word_num - fv.numeric_value) > 0.01:
                continue

            prio = _LABEL_PRIORITY.get(fv.label, 99)
            key = (lid, wid)

            # Keep only the highest-priority label per word
            if key in best and best[key][0] <= prio:
                continue

            decision = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "issue": {
                    "line_id": lid,
                    "word_id": wid,
                    "current_label": fv.label,
                    "word_text": fv.word_text,
                },
                "llm_review": {
                    "decision": "VALID",
                    "reasoning": (
                        f"Financial math balanced ({source}): "
                        f"{fv.label}={fv.numeric_value:.2f}"
                    ),
                    "suggested_label": None,
                    "confidence": "high",
                },
            }
            best[key] = (prio, decision)

    return [d for _, d in best.values()]


# =============================================================================
# Text Scanning for Service/Terminal Receipts
# =============================================================================


def _build_lines_from_words(
    words: list,
) -> list[tuple[int, str, list]]:
    """Group ReceiptWord objects into sorted lines: [(line_id, line_text, words)]."""
    by_line: dict[int, list] = defaultdict(list)
    for w in words:
        by_line[w.line_id].append(w)
    for lid in by_line:
        by_line[lid].sort(key=lambda w: w.word_id)

    return [
        (lid, " ".join(w.text for w in by_line[lid]), by_line[lid])
        for lid in sorted(by_line.keys())
    ]


def _rightmost_dollar(lwords: list) -> tuple[float | None, str]:
    """Find the rightmost number with $ or . on a line."""
    for w in reversed(lwords):
        num = extract_number(w.text)
        if num is not None and ("." in w.text or "$" in w.text):
            return num, w.text
    return None, ""


def scan_receipt_text(words: list) -> list[ScannedValue]:
    """Scan raw receipt text for financial keyword-value pairs.

    Uses a pending-keyword queue to handle OCR's split-line layout where
    left-aligned keywords (Subtotal, Tip, Total) and right-aligned dollar
    values ($50.00, $10.00, $60.00) end up on different line_ids.

    Args:
        words: List of ReceiptWord objects from DynamoDB.

    Returns:
        List of ScannedValue pairs found.
    """
    if not words:
        return []

    lines = _build_lines_from_words(words)
    found: list[ScannedValue] = []
    pending: list[tuple[str, int, str]] = []  # (keyword, line_id, line_text)

    for lid, text, lwords in lines:
        lt = text.lower()

        # Skip long prose lines
        if len(lwords) > 8:
            continue

        # --- Detect keywords on this line ---
        keywords: list[str] = []

        # Primary keywords (mutually exclusive)
        if re.search(r"\bsub[-\s]?total\b", lt):
            keywords.append("SUBTOTAL")
        elif re.search(r"\bamount\b", lt):
            keywords.append("AMOUNT")
        elif re.search(r"\bbalance\b", lt):
            keywords.append("BALANCE")
        elif re.search(r"\bauthorized?\b", lt):
            keywords.append("AUTHORIZED")
        elif re.search(r"\btotal\b", lt):
            keywords.append("TOTAL")

        # Secondary keywords (independent)
        if re.search(r"\b(tip|gratuity)\b", lt) and "receipt" not in lt:
            keywords.append("TIP")
        if re.search(r"\btax\b", lt) and "pre-tax" not in lt:
            keywords.append("TAX")

        # --- Find dollar value on this line ---
        num, num_text = _rightmost_dollar(lwords)

        # --- Pair keywords with values ---
        if keywords and num is not None:
            found.append(ScannedValue(keywords[0], num, num_text, lid, text))
            for kw in keywords[1:]:
                pending.append((kw, lid, text))
        elif keywords:
            for kw in keywords:
                pending.append((kw, lid, text))
        elif num is not None and pending:
            kw, kw_lid, kw_text = pending.pop(0)
            found.append(ScannedValue(kw, num, num_text, kw_lid, kw_text))

    # Remaining pending keywords with ":" → mark as blank
    for kw, lid, text in pending:
        if ":" in text:
            found.append(
                ScannedValue(f"{kw}_BLANK", 0.0, "(blank)", lid, text)
            )

    return found


def classify_receipt_type(
    words: list | None,
    line_item_patterns: dict | None,
) -> str:
    """Classify receipt type for financial math equation selection.

    Args:
        words: List of ReceiptWord objects (for text-scan fallback).
        line_item_patterns: Pattern discovery result dict (may have receipt_type).

    Returns:
        "itemized", "service", or "terminal"
    """
    # Prefer line_item_patterns classification if available
    if line_item_patterns and isinstance(line_item_patterns, dict):
        rt = line_item_patterns.get("receipt_type")
        if rt in ("itemized", "service", "terminal"):
            logger.info("Receipt type from line_item_patterns: %s", rt)
            return rt

    # Text-scan fallback
    if not words:
        return "itemized"

    scanned = scan_receipt_text(words)
    keywords_found = {sv.keyword for sv in scanned}

    has_subtotal = "SUBTOTAL" in keywords_found
    has_tax = "TAX" in keywords_found
    has_tip = "TIP" in keywords_found

    if has_subtotal or has_tax or has_tip:
        logger.info(
            "Text-scan classification: service (subtotal=%s, tax=%s, tip=%s)",
            has_subtotal,
            has_tax,
            has_tip,
        )
        return "service"

    has_total = any(
        kw in keywords_found
        for kw in ("TOTAL", "AMOUNT", "BALANCE", "AUTHORIZED")
    )
    has_tip_blank = "TIP_BLANK" in keywords_found
    if has_total or has_tip_blank:
        logger.info("Text-scan classification: terminal")
        return "terminal"

    return "itemized"


def check_service_equations(
    scanned_values: list[ScannedValue],
) -> list[TextScanMathIssue]:
    """Check financial equations for service/terminal receipts.

    Args:
        scanned_values: Values found by scan_receipt_text().

    Returns:
        List of equation check results.
    """
    # Extract values by keyword priority
    gt_val = None
    gt_sv = None
    for keyword in ("TOTAL", "AMOUNT", "BALANCE", "AUTHORIZED"):
        for sv in scanned_values:
            if sv.keyword == keyword:
                gt_val = sv.value
                gt_sv = sv
                break
        if gt_val is not None:
            break

    st_val = None
    st_sv = None
    for sv in scanned_values:
        if sv.keyword == "SUBTOTAL":
            st_val = sv.value
            st_sv = sv
            break

    tax_val = None
    tax_sv = None
    for sv in scanned_values:
        if sv.keyword == "TAX":
            tax_val = sv.value
            tax_sv = sv
            break

    tip_val = None
    tip_sv = None
    for sv in scanned_values:
        if sv.keyword == "TIP":
            tip_val = sv.value
            tip_sv = sv
            break

    issues: list[TextScanMathIssue] = []

    # HAS_TOTAL: does a total exist?
    involved = [gt_sv] if gt_sv else []
    issues.append(
        TextScanMathIssue(
            issue_type="HAS_TOTAL",
            match=gt_val is not None,
            description=(
                f"Total found: ${gt_val:.2f}" if gt_val else "Total NOT FOUND"
            ),
            expected_value=gt_val,
            actual_value=gt_val,
            involved_scanned=[sv for sv in involved if sv],
        )
    )

    # TOTAL_CHECK: TOTAL = SUBTOTAL + TAX (when all present)
    if gt_val is not None and st_val is not None and tax_val is not None:
        expected = st_val + tax_val
        diff = gt_val - expected
        match = abs(diff) <= 0.02
        involved = [sv for sv in (gt_sv, st_sv, tax_sv) if sv]
        issues.append(
            TextScanMathIssue(
                issue_type="TOTAL_CHECK",
                match=match,
                description=(
                    f"TOTAL (${gt_val:.2f}) {'=' if match else '!='} "
                    f"SUBTOTAL (${st_val:.2f}) + TAX (${tax_val:.2f}) "
                    f"= ${expected:.2f}"
                    + (f" [diff=${abs(diff):.2f}]" if not match else "")
                ),
                expected_value=expected,
                actual_value=gt_val,
                difference=diff,
                involved_scanned=involved,
            )
        )

    # TIP_CHECK: TOTAL = SUBTOTAL + TIP (when all present)
    if gt_val is not None and st_val is not None and tip_val is not None:
        expected = st_val + tip_val
        diff = gt_val - expected
        match = abs(diff) <= 0.02
        involved = [sv for sv in (gt_sv, st_sv, tip_sv) if sv]
        issues.append(
            TextScanMathIssue(
                issue_type="TIP_CHECK",
                match=match,
                description=(
                    f"TOTAL (${gt_val:.2f}) {'=' if match else '!='} "
                    f"SUBTOTAL (${st_val:.2f}) + TIP (${tip_val:.2f}) "
                    f"= ${expected:.2f}"
                    + (f" [diff=${abs(diff):.2f}]" if not match else "")
                ),
                expected_value=expected,
                actual_value=gt_val,
                difference=diff,
                involved_scanned=involved,
            )
        )

    return issues


def _format_text_scan_results(
    issues: list[TextScanMathIssue],
    scanned_values: list[ScannedValue],
    image_id: str,
    receipt_id: int,
    receipt_type: str,
) -> list[dict]:
    """Format text-scan equation results into the standard output format.

    Produces the same list[dict] structure as _format_financial_results
    but with deterministic decisions (no LLM needed).
    """
    results: list[dict[str, Any]] = []

    for issue in issues:
        # One result per issue (text-scan issues are self-contained)
        decision = "VALID" if issue.match else "NEEDS_REVIEW"
        results.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "receipt_type": receipt_type,
                "issue": {
                    "line_id": (
                        issue.involved_scanned[0].line_id
                        if issue.involved_scanned
                        else 0
                    ),
                    "word_id": 0,
                    "current_label": issue.issue_type,
                    "word_text": (
                        issue.involved_scanned[0].source
                        if issue.involved_scanned
                        else ""
                    ),
                    "issue_type": issue.issue_type,
                    "expected_value": issue.expected_value,
                    "actual_value": issue.actual_value,
                    "difference": issue.difference,
                    "description": issue.description,
                },
                "llm_review": {
                    "decision": decision,
                    "reasoning": (
                        f"Text-scan {issue.issue_type}: {issue.description}"
                    ),
                    "suggested_label": None,
                    "confidence": "high",
                },
            }
        )

    # Log summary
    matched = sum(1 for i in issues if i.match)
    logger.info(
        "Text-scan financial results: %d issues, %d matched, receipt_type=%s",
        len(issues),
        matched,
        receipt_type,
    )

    return results


# =============================================================================
# Math Validation
# =============================================================================


def check_grand_total_math(
    values: dict[str, list[FinancialValue]],
) -> MathIssue | None:
    """
    Check: GRAND_TOTAL = sum(SUBTOTAL) + sum(TAX)

    Sums all subtotals and taxes to handle receipts with multiple entries.
    Returns MathIssue if math doesn't match, None otherwise.
    """
    grand_totals = values.get("GRAND_TOTAL", [])
    subtotals = values.get("SUBTOTAL", [])
    taxes = values.get("TAX", [])

    if not grand_totals or not subtotals:
        return None

    grand_total = grand_totals[0]
    subtotal_sum = sum(s.numeric_value for s in subtotals)
    tax_sum = sum(t.numeric_value for t in taxes)

    expected = subtotal_sum + tax_sum
    actual = grand_total.numeric_value
    difference = actual - expected

    # Only skip if difference is essentially zero (floating point precision)
    # Let LLM decide if real discrepancies are acceptable
    if abs(difference) <= FLOAT_EPSILON:
        return None

    involved = [grand_total] + subtotals + taxes

    # Build description showing all components
    subtotal_desc = " + ".join(f"{s.numeric_value:.2f}" for s in subtotals)
    tax_desc = (
        " + ".join(f"{t.numeric_value:.2f}" for t in taxes)
        if taxes
        else "0.00"
    )

    return MathIssue(
        issue_type="GRAND_TOTAL_MISMATCH",
        expected_value=expected,
        actual_value=actual,
        difference=difference,
        involved_values=involved,
        description=(
            f"GRAND_TOTAL ({actual:.2f}) != SUBTOTAL ({subtotal_desc}) "
            f"+ TAX ({tax_desc}) = {expected:.2f}. Difference: {difference:.2f}"
        ),
    )


def check_subtotal_math(
    values: dict[str, list[FinancialValue]],
) -> MathIssue | None:
    """
    Check: SUBTOTAL = sum(LINE_TOTAL)

    Returns MathIssue if math doesn't match, None otherwise.
    """
    subtotals = values.get("SUBTOTAL", [])
    line_totals = values.get("LINE_TOTAL", [])
    discounts = values.get("DISCOUNT", [])

    if not subtotals or not line_totals:
        return None

    subtotal = subtotals[0]
    line_total_sum = sum(lt.numeric_value for lt in line_totals)

    # Discounts are typically negative or should be subtracted
    discount_sum = sum(abs(d.numeric_value) for d in discounts)

    expected = line_total_sum - discount_sum
    actual = subtotal.numeric_value
    difference = actual - expected

    # Only skip if difference is essentially zero (floating point precision)
    # Let LLM decide if real discrepancies are acceptable
    if abs(difference) <= FLOAT_EPSILON:
        return None

    involved = [subtotal] + line_totals + discounts

    return MathIssue(
        issue_type="SUBTOTAL_MISMATCH",
        expected_value=expected,
        actual_value=actual,
        difference=difference,
        involved_values=involved,
        description=(
            f"SUBTOTAL ({actual:.2f}) != sum(LINE_TOTAL) ({line_total_sum:.2f}) "
            f"- DISCOUNT ({discount_sum:.2f}) = {expected:.2f}. Difference: {difference:.2f}"
        ),
    )


def check_line_item_math(
    values: dict[str, list[FinancialValue]],
) -> list[MathIssue]:
    """
    Check: QTY × UNIT_PRICE = LINE_TOTAL for each line item row.

    Returns list of MathIssue for each mismatched line.
    """
    issues = []

    # Group values by line_index for per-line validation
    line_values: dict[int, dict[str, FinancialValue]] = {}

    for label in ["QUANTITY", "UNIT_PRICE", "LINE_TOTAL"]:
        for fv in values.get(label, []):
            if fv.line_index not in line_values:
                line_values[fv.line_index] = {}
            line_values[fv.line_index][label] = fv

    # Check each line that has all three components
    for line_idx, line_vals in line_values.items():
        qty = line_vals.get("QUANTITY")
        unit_price = line_vals.get("UNIT_PRICE")
        line_total = line_vals.get("LINE_TOTAL")

        if qty is None or unit_price is None or line_total is None:
            continue

        expected = qty.numeric_value * unit_price.numeric_value
        actual = line_total.numeric_value
        difference = actual - expected

        # Only skip if difference is essentially zero (floating point precision)
        # Let LLM decide if real discrepancies are acceptable
        if abs(difference) <= FLOAT_EPSILON:
            continue

        issues.append(
            MathIssue(
                issue_type="LINE_ITEM_MISMATCH",
                expected_value=expected,
                actual_value=actual,
                difference=difference,
                involved_values=[qty, unit_price, line_total],
                description=(
                    f"Line {line_idx}: LINE_TOTAL ({actual:.2f}) != "
                    f"QTY ({qty.numeric_value}) × UNIT_PRICE ({unit_price.numeric_value:.2f}) "
                    f"= {expected:.2f}. Difference: {difference:.2f}"
                ),
            )
        )

    return issues


def detect_math_issues(
    visual_lines: list[VisualLine],
) -> list[MathIssue]:
    """
    Detect all math issues in the receipt.

    Returns list of MathIssue objects.
    """
    values = extract_financial_values(visual_lines)
    issues = []

    # Check GRAND_TOTAL = SUBTOTAL + TAX
    grand_total_issue = check_grand_total_math(values)
    if grand_total_issue:
        issues.append(grand_total_issue)

    # Check SUBTOTAL = sum(LINE_TOTAL)
    subtotal_issue = check_subtotal_math(values)
    if subtotal_issue:
        issues.append(subtotal_issue)

    # Check QTY × UNIT_PRICE = LINE_TOTAL per line
    line_issues = check_line_item_math(values)
    issues.extend(line_issues)

    return issues


# =============================================================================
# LLM Prompt Building
# =============================================================================


def build_financial_validation_prompt(
    visual_lines: list[VisualLine],
    math_issues: list[MathIssue],
    merchant_name: str = "Unknown",
) -> str:
    """
    Build LLM prompt for financial math validation.

    The LLM determines which value is likely wrong when math doesn't match.
    """
    # Build receipt text representation
    receipt_lines = []
    for line in visual_lines:
        line_text = []
        for wc in line.words:
            label = wc.current_label.label if wc.current_label else "unlabeled"
            line_text.append(f"{wc.word.text}[{label}]")
        receipt_lines.append(
            f"  Line {line.line_index}: " + " | ".join(line_text)
        )

    receipt_text = "\n".join(receipt_lines[:50])
    if len(visual_lines) > 50:
        receipt_text += f"\n  ... ({len(visual_lines) - 50} more lines)"

    # Build math issues table
    issues_text = []
    for i, issue in enumerate(math_issues):
        values_desc = []
        for fv in issue.involved_values:
            values_desc.append(
                f'    - {fv.label}: "{fv.word_text}" = {fv.numeric_value:.2f} '
                f"(line {fv.line_index})"
            )
        values_str = "\n".join(values_desc)

        issues_text.append(
            f"[{i}] {issue.issue_type}\n"
            f"  Description: {issue.description}\n"
            f"  Values involved:\n{values_str}"
        )

    issues_str = "\n\n".join(issues_text)

    prompt = f"""# Financial Math Validation for {merchant_name}

You are validating mathematical relationships between financial values on a receipt.
For each math issue detected, determine which value is most likely WRONG.

## Receipt Structure
{receipt_text}

## Math Issues Detected
{issues_str}

## Your Task
For each issue above, analyze the receipt context and determine:
1. Whether the discrepancy is significant enough to be a real error
2. If it is a real error, which value is most likely wrong

Consider:
1. **Acceptable rounding**: Small discrepancies (e.g., $0.01-0.02) on large receipts
   are often acceptable due to per-item rounding.
2. **Cumulative rounding**: Receipts with many line items accumulate rounding errors.
3. **OCR errors**: Common misreads include 8<->6, 1<->7, 0<->O, causing larger differences.
4. **Receipt structure**: Totals usually appear at bottom, line items in middle.
5. **Context clues**: Which value "looks wrong" based on surrounding text.

Return one decision per involved value in each issue. Use `value_index` to indicate
which value within the issue (0-based index into the "Values involved" list above).

## Decision Guide
- VALID: The discrepancy is acceptable (e.g., rounding) OR this specific value is correct
- INVALID: This specific value's label is wrong (suggest correction if applicable)
- NEEDS_REVIEW: Cannot determine if the discrepancy is acceptable or which value is wrong
"""
    return prompt


def parse_financial_evaluation_response(
    response_text: str,
    num_values: int,
    math_issues: list[MathIssue] | None = None,
) -> list[dict]:
    """Parse the LLM response into a list of per-value decisions.

    Args:
        response_text: Raw LLM response text
        num_values: Total number of involved values across all issues
        math_issues: The math issues (used to map per-issue fallback to per-value)

    Returns:
        List of per-value decisions (one per involved value across all issues)
    """
    response_text = extract_json_from_response(response_text)

    fallback = {
        "decision": "NEEDS_REVIEW",
        "reasoning": "Failed to parse LLM response",
        "suggested_label": None,
        "confidence": "low",
        "issue_type": "UNKNOWN",
    }

    # Parse JSON once
    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM response as JSON: %s", e)
        return [fallback.copy() for _ in range(num_values)]

    # Try structured parsing with Pydantic
    try:
        if isinstance(parsed, list):
            parsed_obj = {"evaluations": parsed}
        else:
            parsed_obj = parsed
        structured_response = FinancialEvaluationResponse.model_validate(
            parsed_obj
        )
        # Convert to per-value decision list
        return _structured_to_per_value(
            structured_response.evaluations, num_values, math_issues
        )
    except ValidationError as e:
        logger.debug(
            "Structured parsing failed, falling back to manual parsing: %s", e
        )

    # Fallback to manual parsing (reuse already-parsed JSON)
    try:
        decisions = parsed  # Reuse already-parsed JSON
        if isinstance(decisions, dict):
            decisions = decisions.get("evaluations", [])

        if not isinstance(decisions, list):
            logger.warning(
                "Decisions is not a list: %s", type(decisions).__name__
            )
            return [fallback.copy() for _ in range(num_values)]

        return _raw_to_per_value(decisions, num_values, math_issues)

    except TypeError as e:
        logger.warning("Failed to process parsed response: %s", e)
        return [fallback.copy() for _ in range(num_values)]


def _response_to_text(response: Any) -> str:
    """Convert chat-model response content to a plain string."""
    content = response.content if hasattr(response, "content") else response
    return content if isinstance(content, str) else str(content)


def _structured_to_per_value(
    evaluations: list,
    num_values: int,
    math_issues: list[MathIssue] | None,
) -> list[dict]:
    """Convert structured FinancialEvaluation list to per-value decisions."""
    result: list[dict | None] = [None] * num_values

    # Build mapping: (issue_index, value_index) -> flat index
    flat_idx = 0
    issue_value_to_flat: dict[tuple[int, int], int] = {}
    if math_issues:
        for issue_idx, issue in enumerate(math_issues):
            for val_idx in range(len(issue.involved_values)):
                issue_value_to_flat[(issue_idx, val_idx)] = flat_idx
                flat_idx += 1

    for eval_item in evaluations:
        d = eval_item.to_dict()
        issue_idx = d.get("index", 0)
        val_idx = d.get("value_index", 0)
        flat = issue_value_to_flat.get((issue_idx, val_idx))
        if flat is not None and flat < num_values:
            result[flat] = d

    # Fill gaps with NEEDS_REVIEW
    fallback = {
        "decision": "NEEDS_REVIEW",
        "reasoning": "No per-value decision from LLM",
        "suggested_label": None,
        "confidence": "low",
        "issue_type": "UNKNOWN",
    }
    return [r if r is not None else fallback.copy() for r in result]


def _raw_to_per_value(
    decisions: list[dict],
    num_values: int,
    math_issues: list[MathIssue] | None,
) -> list[dict]:
    """Convert raw LLM decision list to per-value decisions."""
    result: list[dict | None] = [None] * num_values

    # Build mapping: (issue_index, value_index) -> flat index
    flat_idx = 0
    issue_value_to_flat: dict[tuple[int, int], int] = {}
    if math_issues:
        for issue_idx, issue in enumerate(math_issues):
            for val_idx in range(len(issue.involved_values)):
                issue_value_to_flat[(issue_idx, val_idx)] = flat_idx
                flat_idx += 1

    # Check if decisions have value_index (per-value) or not (per-issue)
    has_value_index = any(
        "value_index" in d for d in decisions if isinstance(d, dict)
    )

    if has_value_index:
        # Per-value decisions: map by (index, value_index)
        for d in decisions:
            if not isinstance(d, dict):
                continue
            issue_idx = d.get("index", 0)
            val_idx = d.get("value_index", 0)
            flat = issue_value_to_flat.get((issue_idx, val_idx))
            if flat is not None and flat < num_values:
                result[flat] = {
                    "decision": d.get("decision", "NEEDS_REVIEW"),
                    "reasoning": d.get("reasoning", ""),
                    "suggested_label": d.get("suggested_label"),
                    "confidence": d.get("confidence", "medium"),
                    "issue_type": d.get("issue_type", "UNKNOWN"),
                }
    else:
        # Legacy per-issue decisions: apply to first value only, NEEDS_REVIEW for rest
        if math_issues:
            for d in decisions:
                if not isinstance(d, dict):
                    continue
                issue_idx = d.get("index", 0)
                # Apply decision to first involved value only
                flat = issue_value_to_flat.get((issue_idx, 0))
                if flat is not None and flat < num_values:
                    result[flat] = {
                        "decision": d.get("decision", "NEEDS_REVIEW"),
                        "reasoning": d.get("reasoning", ""),
                        "suggested_label": d.get("suggested_label"),
                        "confidence": d.get("confidence", "medium"),
                        "issue_type": d.get("issue_type", "UNKNOWN"),
                    }

    # Fill gaps with NEEDS_REVIEW
    fallback = {
        "decision": "NEEDS_REVIEW",
        "reasoning": "No per-value decision from LLM",
        "suggested_label": None,
        "confidence": "low",
        "issue_type": "UNKNOWN",
    }
    return [r if r is not None else fallback.copy() for r in result]


# =============================================================================
# Shared Helpers for Result Formatting
# =============================================================================


def _pad_decisions(
    decisions: list[dict] | None,
    num_values: int,
) -> list[dict]:
    """
    Pad decisions list to match total number of involved values.

    If the LLM returns fewer decisions than values, pad with NEEDS_REVIEW fallbacks.
    If it returns more, truncate to match.

    Args:
        decisions: List of decision dicts from LLM (may be None or short)
        num_values: Total number of involved values that need decisions

    Returns:
        List of exactly num_values decisions
    """
    normalized_decisions = list(decisions) if decisions else []
    num_decisions = len(normalized_decisions)
    if num_decisions != num_values:
        logger.warning(
            "Decision count mismatch: %d values, %d decisions",
            num_values,
            num_decisions,
        )
        while len(normalized_decisions) < num_values:
            normalized_decisions.append(
                {
                    "decision": "NEEDS_REVIEW",
                    "reasoning": "No decision from LLM (count mismatch)",
                    "suggested_label": None,
                    "confidence": "low",
                    "issue_type": "UNKNOWN",
                }
            )
        normalized_decisions = normalized_decisions[:num_values]
    return normalized_decisions


def _format_financial_results(
    math_issues: list[MathIssue],
    decisions: list[dict],
    image_id: str,
    receipt_id: int,
) -> list[dict]:
    """
    Format financial validation results from issues and per-value decisions.

    Creates one result entry per involved value in each issue, using the
    corresponding per-value decision.

    Args:
        math_issues: List of detected math issues
        decisions: List of LLM decisions (one per involved value, flattened)
        image_id: Receipt image ID
        receipt_id: Receipt ID

    Returns:
        List of result dicts ready for apply_llm_decisions()
    """
    results: list[dict[str, Any]] = []
    decision_idx = 0
    for issue in math_issues:
        for fv in issue.involved_values:
            decision = (
                decisions[decision_idx]
                if decision_idx < len(decisions)
                else {
                    "decision": "NEEDS_REVIEW",
                    "reasoning": "No decision available",
                    "suggested_label": None,
                    "confidence": "low",
                }
            )
            wc = fv.word_context
            results.append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "issue": {
                        "line_id": wc.word.line_id,
                        "word_id": wc.word.word_id,
                        "current_label": fv.label,
                        "word_text": fv.word_text,
                        "issue_type": issue.issue_type,
                        "expected_value": issue.expected_value,
                        "actual_value": issue.actual_value,
                        "difference": issue.difference,
                        "description": issue.description,
                    },
                    "llm_review": {
                        "decision": decision.get("decision", "NEEDS_REVIEW"),
                        "reasoning": decision.get("reasoning", ""),
                        "suggested_label": decision.get("suggested_label"),
                        "confidence": decision.get("confidence", "medium"),
                    },
                }
            )
            decision_idx += 1

    # Log summary
    decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
    for r in results:
        llm_review = r.get("llm_review", {})
        dec = (
            llm_review.get("decision", "NEEDS_REVIEW")
            if isinstance(llm_review, dict)
            else "NEEDS_REVIEW"
        )
        if dec in decision_counts:
            decision_counts[dec] += 1
    logger.info("Financial validation results: %s", decision_counts)

    return results


# =============================================================================
# Main Evaluation Function
# =============================================================================


@traceable(name="financial_validation", run_type="chain")
def evaluate_financial_math(
    visual_lines: list[VisualLine],
    llm: BaseChatModel,
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
    words: list | None = None,
    line_item_patterns: dict | None = None,
    labels: list | None = None,
    chroma_client: Any | None = None,
) -> list[dict]:
    """
    Validate financial math on a receipt.

    This is the main entry point for financial validation.

    Args:
        visual_lines: Visual lines from the receipt (words with labels)
        llm: Language model for determining which value is wrong
        image_id: Image ID for output format
        receipt_id: Receipt ID for output format
        merchant_name: Merchant name for context
        words: Raw ReceiptWord list for text scanning (service/terminal)
        line_item_patterns: Pattern discovery result with receipt_type
        labels: ReceiptWordLabel list for enhanced extraction
        chroma_client: ChromaDB client for column alignment

    Returns:
        List of decisions ready for apply_llm_decisions()
    """
    # Step 0: Classify receipt type
    receipt_type = classify_receipt_type(words, line_item_patterns)

    if receipt_type in ("service", "terminal"):
        # Text-scan based: no LLM needed
        scanned = scan_receipt_text(words) if words else []
        issues = check_service_equations(scanned)
        return _format_text_scan_results(
            issues, scanned, image_id, receipt_id, receipt_type
        )

    # Itemized path: try enhanced extraction if labels + chroma available
    enhanced_values: dict[str, list[FinancialValue]] | None = None
    used_llm_fallback = False
    if labels and chroma_client and words:
        enhanced_values = extract_financial_values_enhanced(
            visual_lines, words, labels, chroma_client, merchant_name
        )
        math_issues = detect_math_issues_from_values(enhanced_values)
        logger.info(
            "Enhanced extraction: %d math issues from %d label types",
            len(math_issues),
            len(enhanced_values),
        )
    else:
        math_issues = detect_math_issues(visual_lines)

    logger.info("Detected %d math issues", len(math_issues))

    # LLM fallback: if SUBTOTAL_MISMATCH and we have enhanced values + words
    if enhanced_values and words:
        has_subtotal_mismatch = any(
            i.issue_type == "SUBTOTAL_MISMATCH" for i in math_issues
        )
        if has_subtotal_mismatch:
            subtotal_val = None
            for fv in enhanced_values.get("SUBTOTAL", []):
                subtotal_val = fv.numeric_value
                break

            if subtotal_val is not None:
                logger.info(
                    "LLM fallback: SUBTOTAL_MISMATCH detected, "
                    "asking LLM to identify line items"
                )
                receipt_visual_lines = _group_into_visual_lines(words)
                llm_result = _llm_identify_line_items(
                    llm=llm,
                    visual_lines=receipt_visual_lines,
                    subtotal=subtotal_val,
                    line_totals=enhanced_values.get("LINE_TOTAL", []),
                    discounts=enhanced_values.get("DISCOUNT", []),
                    merchant_name=merchant_name,
                    dummy_word=words[0],
                )
                if llm_result is not None:
                    enhanced_values["LINE_TOTAL"] = llm_result["LINE_TOTAL"]
                    enhanced_values["DISCOUNT"] = llm_result["DISCOUNT"]
                    math_issues = detect_math_issues_from_values(
                        enhanced_values
                    )
                    used_llm_fallback = True
                    logger.info(
                        "LLM fallback applied: %d math issues remaining",
                        len(math_issues),
                    )

    if not math_issues:
        if enhanced_values:
            source = (
                "llm_fallback_math_balanced"
                if used_llm_fallback
                else "enhanced_extraction_math_balanced"
            )
            valid_decisions = _build_valid_decisions(
                enhanced_values,
                image_id,
                receipt_id,
                source,
            )
            if valid_decisions:
                logger.info(
                    "Math balanced (%s): emitting %d VALID decisions",
                    source,
                    len(valid_decisions),
                )
                return valid_decisions
        logger.info("No math issues found, skipping financial validation")
        return []

    # Log issue summary
    for issue in math_issues:
        logger.info("  %s: %s", issue.issue_type, issue.description)

    # Step 2: Build prompt and call LLM
    prompt = build_financial_validation_prompt(
        visual_lines=visual_lines,
        math_issues=math_issues,
        merchant_name=merchant_name,
    )

    strict_structured_output, structured_retries = (
        get_structured_output_settings(logger_instance=logger)
    )
    num_values = sum(len(issue.involved_values) for issue in math_issues)

    try:
        decisions: list[dict[str, Any]] | None = None

        if strict_structured_output:
            structured_result = invoke_structured_with_retry(
                llm=llm,
                schema=FinancialEvaluationResponse,
                input_payload=prompt,
                retries=structured_retries,
            )
            if (
                structured_result.success
                and structured_result.response is not None
            ):
                decisions = _structured_to_per_value(
                    structured_result.response.evaluations,
                    num_values,
                    math_issues,
                )
                logger.debug(
                    "Structured output succeeded with %d evaluations",
                    len(decisions),
                )
            else:
                failure_reason = (
                    "Strict structured output failed for financial validation "
                    f"(attempts={structured_result.attempts}, "
                    f"error={structured_result.error_type or 'unknown'})."
                )
                logger.warning("%s", failure_reason)
                decisions = build_structured_failure_decisions(
                    num_values,
                    failure_reason=failure_reason,
                    extra_fields={"issue_type": "UNKNOWN"},
                )
        else:
            logger.info(
                "Strict structured output disabled for financial validation; "
                "using legacy text parsing fallback."
            )
            max_retries = 3
            use_structured = hasattr(llm, "with_structured_output")

            for attempt in range(max_retries):
                try:
                    if use_structured:
                        try:
                            structured_llm = llm.with_structured_output(
                                FinancialEvaluationResponse
                            )
                            structured_response = structured_llm.invoke(prompt)
                            if not isinstance(
                                structured_response,
                                FinancialEvaluationResponse,
                            ):
                                raise TypeError(
                                    "Expected FinancialEvaluationResponse from "
                                    "with_structured_output"
                                )
                            decisions = _structured_to_per_value(
                                structured_response.evaluations,
                                num_values,
                                math_issues,
                            )
                            logger.debug(
                                "Structured output succeeded with %d evaluations",
                                len(decisions),
                            )
                            break
                        except LLMRateLimitError:
                            raise
                        except Exception as struct_err:
                            logger.warning(
                                "Structured output failed (attempt %d), "
                                "falling back to text: %s",
                                attempt + 1,
                                struct_err,
                            )

                    text_response = llm.invoke(prompt)
                    response_text = _response_to_text(text_response)
                    decisions = parse_financial_evaluation_response(
                        response_text, num_values, math_issues
                    )

                    parse_failures = sum(
                        1
                        for decision in decisions
                        if "Failed to parse" in decision.get("reasoning", "")
                    )
                    if parse_failures == 0:
                        break
                    if parse_failures < len(decisions):
                        logger.info(
                            "Partial parse success: %d/%d parsed",
                            len(decisions) - parse_failures,
                            len(decisions),
                        )
                        break
                    if attempt < max_retries - 1:
                        logger.warning(
                            "All decisions failed to parse, retrying "
                            "(attempt %d)",
                            attempt + 1,
                        )
                    else:
                        logger.warning(
                            "All decisions failed after %d attempts",
                            max_retries,
                        )
                except LLMRateLimitError:
                    raise
                except Exception as inner_err:
                    if attempt == max_retries - 1:
                        raise inner_err
                    logger.warning(
                        "LLM invocation failed (attempt %d): %s",
                        attempt + 1,
                        inner_err,
                    )

        if decisions is None:
            decisions = build_structured_failure_decisions(
                num_values,
                failure_reason="No response received",
                extra_fields={"issue_type": "UNKNOWN"},
            )

        # Step 3: Pad decisions to match total involved values
        decisions = _pad_decisions(decisions, num_values)

        # Step 4: Format output - one result per involved value with matching decision
        return _format_financial_results(
            math_issues, decisions, image_id, receipt_id
        )

    except LLMRateLimitError as e:
        logger.error(
            "Financial LLM rate limited, propagating for retry: %s", e
        )
        raise
    except Exception as e:
        logger.error("Financial LLM call failed: %s", e)

        # Return NEEDS_REVIEW for all values in all issues
        results = []
        for issue in math_issues:
            for fv in issue.involved_values:
                wc = fv.word_context
                results.append(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "issue": {
                            "line_id": wc.word.line_id,
                            "word_id": wc.word.word_id,
                            "current_label": fv.label,
                            "word_text": fv.word_text,
                            "issue_type": issue.issue_type,
                            # Equation breakdown for visualization
                            "expected_value": issue.expected_value,
                            "actual_value": issue.actual_value,
                            "difference": issue.difference,
                            "description": issue.description,
                        },
                        "llm_review": {
                            "decision": "NEEDS_REVIEW",
                            "reasoning": f"LLM call failed: {e}",
                            "suggested_label": None,
                            "confidence": "low",
                        },
                    }
                )
        return results


# =============================================================================
# Async version
# =============================================================================


@traceable(name="financial_validation", run_type="chain")
async def evaluate_financial_math_async(
    visual_lines: list[VisualLine],
    llm: Any,  # RateLimitedLLMInvoker or BaseChatModel with ainvoke
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
    words: list | None = None,
    line_item_patterns: dict | None = None,
    labels: list | None = None,
    chroma_client: Any | None = None,
) -> list[dict]:
    """
    Async version of evaluate_financial_math.

    Uses ainvoke() for concurrent LLM calls. Works with RateLimitedLLMInvoker
    or any LLM that supports ainvoke().

    Decorated with @traceable so LLM calls auto-nest under this span in
    LangSmith when called inside a tracing_context(parent=root).

    Args:
        visual_lines: Visual lines from the receipt (words with labels)
        llm: Language model invoker (RateLimitedLLMInvoker or BaseChatModel)
        image_id: Image ID for output format
        receipt_id: Receipt ID for output format
        merchant_name: Merchant name for context
        words: Raw ReceiptWord list for text scanning (service/terminal)
        line_item_patterns: Pattern discovery result with receipt_type
        labels: ReceiptWordLabel list for enhanced extraction
        chroma_client: ChromaDB client for column alignment

    Returns:
        List of decisions ready for apply_llm_decisions()
    """
    # Step 0: Classify receipt type
    receipt_type = classify_receipt_type(words, line_item_patterns)

    if receipt_type in ("service", "terminal"):
        # Text-scan based: no LLM needed
        scanned = scan_receipt_text(words) if words else []
        issues = check_service_equations(scanned)
        return _format_text_scan_results(
            issues, scanned, image_id, receipt_id, receipt_type
        )

    # Itemized path: try enhanced extraction if labels + chroma available
    enhanced_values: dict[str, list[FinancialValue]] | None = None
    used_llm_fallback = False
    if labels and chroma_client and words:
        enhanced_values = extract_financial_values_enhanced(
            visual_lines, words, labels, chroma_client, merchant_name
        )
        math_issues = detect_math_issues_from_values(enhanced_values)
        logger.info(
            "Enhanced extraction: %d math issues from %d label types",
            len(math_issues),
            len(enhanced_values),
        )
    else:
        math_issues = detect_math_issues(visual_lines)

    logger.info("Detected %d math issues", len(math_issues))

    # LLM fallback: if SUBTOTAL_MISMATCH and we have enhanced values + words
    if enhanced_values and words:
        has_subtotal_mismatch = any(
            i.issue_type == "SUBTOTAL_MISMATCH" for i in math_issues
        )
        if has_subtotal_mismatch:
            subtotal_val = None
            for fv in enhanced_values.get("SUBTOTAL", []):
                subtotal_val = fv.numeric_value
                break

            if subtotal_val is not None:
                logger.info(
                    "LLM fallback: SUBTOTAL_MISMATCH detected, "
                    "asking LLM to identify line items"
                )
                # Compute visual lines once — used for both the LLM
                # prompt text and matching results back to words.
                receipt_visual_lines = _group_into_visual_lines(words)
                llm_result = await _llm_identify_line_items_async(
                    llm=llm,
                    visual_lines=receipt_visual_lines,
                    subtotal=subtotal_val,
                    line_totals=enhanced_values.get("LINE_TOTAL", []),
                    discounts=enhanced_values.get("DISCOUNT", []),
                    merchant_name=merchant_name,
                    dummy_word=words[0],
                )
                if llm_result is not None:
                    # Replace LINE_TOTAL and DISCOUNT with LLM values
                    enhanced_values["LINE_TOTAL"] = llm_result["LINE_TOTAL"]
                    enhanced_values["DISCOUNT"] = llm_result["DISCOUNT"]
                    math_issues = detect_math_issues_from_values(
                        enhanced_values
                    )
                    used_llm_fallback = True
                    logger.info(
                        "LLM fallback applied: %d math issues remaining",
                        len(math_issues),
                    )

    if not math_issues:
        # Math balances — emit VALID decisions for confirmed values
        if enhanced_values:
            source = (
                "llm_fallback_math_balanced"
                if used_llm_fallback
                else "enhanced_extraction_math_balanced"
            )
            valid_decisions = _build_valid_decisions(
                enhanced_values, image_id, receipt_id, source
            )
            if valid_decisions:
                logger.info(
                    "Math balanced (%s): emitting %d VALID decisions",
                    source,
                    len(valid_decisions),
                )
                return valid_decisions
        logger.info("No math issues found, skipping financial validation")
        return []

    for issue in math_issues:
        logger.info("  %s: %s", issue.issue_type, issue.description)

    # Step 2: Build prompt
    prompt = build_financial_validation_prompt(
        visual_lines=visual_lines,
        math_issues=math_issues,
        merchant_name=merchant_name,
    )

    strict_structured_output, structured_retries = (
        get_structured_output_settings(logger_instance=logger)
    )
    num_values = sum(len(issue.involved_values) for issue in math_issues)

    try:
        decisions: list[dict[str, Any]] | None = None

        if strict_structured_output:
            structured_result = await ainvoke_structured_with_retry(
                llm=llm,
                schema=FinancialEvaluationResponse,
                input_payload=prompt,
                retries=structured_retries,
            )
            if (
                structured_result.success
                and structured_result.response is not None
            ):
                decisions = _structured_to_per_value(
                    structured_result.response.evaluations,
                    num_values,
                    math_issues,
                )
                logger.debug(
                    "Structured output succeeded with %d evaluations",
                    len(decisions),
                )
            else:
                failure_reason = (
                    "Strict structured output failed for financial validation "
                    f"(attempts={structured_result.attempts}, "
                    f"error={structured_result.error_type or 'unknown'})."
                )
                logger.warning("%s", failure_reason)
                decisions = build_structured_failure_decisions(
                    num_values,
                    failure_reason=failure_reason,
                    extra_fields={"issue_type": "UNKNOWN"},
                )
        else:
            logger.info(
                "Strict structured output disabled for financial validation; "
                "using legacy text parsing fallback."
            )
            max_retries = 3
            use_structured = hasattr(llm, "with_structured_output")

            for attempt in range(max_retries):
                try:
                    if use_structured:
                        try:
                            structured_llm = llm.with_structured_output(
                                FinancialEvaluationResponse
                            )
                            if hasattr(structured_llm, "ainvoke"):
                                structured_response = (
                                    await structured_llm.ainvoke(prompt)
                                )
                            else:
                                structured_response = await asyncio.to_thread(
                                    structured_llm.invoke,
                                    prompt,
                                )
                            if not isinstance(
                                structured_response,
                                FinancialEvaluationResponse,
                            ):
                                raise TypeError(
                                    "Expected FinancialEvaluationResponse from "
                                    "with_structured_output"
                                )
                            decisions = _structured_to_per_value(
                                structured_response.evaluations,
                                num_values,
                                math_issues,
                            )
                            logger.debug(
                                "Structured output succeeded with %d evaluations",
                                len(decisions),
                            )
                            break
                        except LLMRateLimitError:
                            raise
                        except Exception as struct_err:
                            logger.warning(
                                "Structured output failed (attempt %d), "
                                "falling back to text: %s",
                                attempt + 1,
                                struct_err,
                            )

                    if hasattr(llm, "ainvoke"):
                        text_response = await llm.ainvoke(prompt)
                    else:
                        text_response = await asyncio.to_thread(
                            llm.invoke, prompt
                        )
                    response_text = _response_to_text(text_response)
                    decisions = parse_financial_evaluation_response(
                        response_text, num_values, math_issues
                    )

                    parse_failures = sum(
                        1
                        for decision in decisions
                        if "Failed to parse" in decision.get("reasoning", "")
                    )
                    if parse_failures == 0:
                        break
                    if parse_failures < len(decisions):
                        logger.info(
                            "Partial parse success: %d/%d parsed",
                            len(decisions) - parse_failures,
                            len(decisions),
                        )
                        break
                    if attempt < max_retries - 1:
                        logger.warning(
                            "All decisions failed to parse, retrying "
                            "(attempt %d)",
                            attempt + 1,
                        )
                    else:
                        logger.warning(
                            "All decisions failed after %d attempts",
                            max_retries,
                        )
                except LLMRateLimitError:
                    raise
                except Exception as inner_err:
                    if attempt == max_retries - 1:
                        raise inner_err
                    logger.warning(
                        "LLM invocation failed (attempt %d): %s",
                        attempt + 1,
                        inner_err,
                    )

        if decisions is None:
            decisions = build_structured_failure_decisions(
                num_values,
                failure_reason="No response received",
                extra_fields={"issue_type": "UNKNOWN"},
            )

        # Step 4: Format output
        decisions = _pad_decisions(decisions, num_values)
        return _format_financial_results(
            math_issues, decisions, image_id, receipt_id
        )

    except LLMRateLimitError as e:
        logger.error(
            "Financial LLM rate limited, propagating for retry: %s", e
        )
        raise
    except Exception as e:
        logger.error("Financial LLM call failed: %s", e)

        results = []
        for issue in math_issues:
            for fv in issue.involved_values:
                wc = fv.word_context
                results.append(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "issue": {
                            "line_id": wc.word.line_id,
                            "word_id": wc.word.word_id,
                            "current_label": fv.label,
                            "word_text": fv.word_text,
                            "issue_type": issue.issue_type,
                            "expected_value": issue.expected_value,
                            "actual_value": issue.actual_value,
                            "difference": issue.difference,
                            "description": issue.description,
                        },
                        "llm_review": {
                            "decision": "NEEDS_REVIEW",
                            "reasoning": f"LLM call failed: {e}",
                            "suggested_label": None,
                            "confidence": "low",
                        },
                    }
                )
        return results


# Alias for API consistency with other subagents
evaluate_financial_math_sync = evaluate_financial_math

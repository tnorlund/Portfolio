"""
Label Evaluator Prompts

Production-tested prompts for LLM review of receipt labeling issues.
Moved from infra/label_evaluator_step_functions/lambdas/llm_review.py.
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Optional

from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from receipt_agent.constants import CORE_LABELS as CORE_LABELS_DICT
from receipt_agent.constants import CORE_LABELS_SET
from receipt_agent.prompts.structured_outputs import (
    BatchedReviewResponse,
    extract_json_from_response,
)

if TYPE_CHECKING:
    from receipt_agent.agents.label_evaluator.state import (
        LabelDistributionStats,
        MerchantBreakdown,
        SimilarityDistribution,
        SimilarWordEvidence,
    )

logger = logging.getLogger(__name__)


class LLMResponseParseError(Exception):
    """Raised when LLM response cannot be parsed as valid JSON."""

    pass


# =============================================================================
# Core Label Definitions (generated from constants.CORE_LABELS)
# =============================================================================


def _build_core_labels_prompt() -> str:
    """Build the CORE_LABELS prompt string from the CORE_LABELS_DICT.

    This ensures the prompt always matches CORE_LABELS_SET for validation.
    """
    lines = []
    for label, description in CORE_LABELS_DICT.items():
        lines.append(f"{label}: {description}")
    return "\n".join(lines)


# Generated at module load time
CORE_LABELS = _build_core_labels_prompt()


# =============================================================================
# Prompt Helper Functions
# =============================================================================


def format_line_item_patterns(patterns: Optional[dict]) -> str:
    """Format line item patterns for the LLM prompt.

    Handles both flat schema (new) and nested schema (legacy) for backwards
    compatibility with existing S3 pattern files.
    """
    if not patterns:
        return "No line item patterns available for this merchant."

    # Handle legacy nested structure: flatten if needed
    if "patterns" in patterns and isinstance(patterns.get("patterns"), dict):
        # Merge nested patterns into top level
        nested = patterns["patterns"]
        patterns = {
            "merchant": patterns.get("merchant"),
            "receipt_type": patterns.get("receipt_type"),
            "receipt_type_reason": patterns.get("receipt_type_reason"),
            "auto_generated": patterns.get("auto_generated", False),
            "discovered_from_receipts": patterns.get(
                "discovered_from_receipts"
            ),
            **nested,
        }

    lines = []

    # Metadata
    lines.append(f"**Merchant**: {patterns.get('merchant', 'Unknown')}")
    receipt_type = patterns.get("receipt_type")
    if receipt_type:
        lines.append(f"**Receipt Type**: {receipt_type}")
        if patterns.get("receipt_type_reason"):
            lines.append(
                f"**Classification Reason**: {patterns['receipt_type_reason']}"
            )

    # For service receipts, don't show pattern details
    if receipt_type == "service":
        return "\n".join(lines)

    # Structure
    item_structure = patterns.get("item_structure")
    if item_structure:
        lines.append(f"**Item Structure**: {item_structure}")

    lpi = patterns.get("lines_per_item")
    if lpi and isinstance(lpi, dict):
        lines.append(
            f"**Lines per Item**: typical={lpi.get('typical', '?')}, "
            f"range=[{lpi.get('min', '?')}, {lpi.get('max', '?')}]"
        )

    if patterns.get("item_start_marker"):
        lines.append(f"**Item Start**: {patterns['item_start_marker']}")

    if patterns.get("item_end_marker"):
        lines.append(f"**Item End**: {patterns['item_end_marker']}")

    if patterns.get("grouping_rule"):
        lines.append(f"**Grouping Rule**: {patterns['grouping_rule']}")

    # Position info
    label_positions = patterns.get("label_positions")
    if label_positions and isinstance(label_positions, dict):
        pos_lines = []
        for label, position in label_positions.items():
            if position and position != "not_found":
                pos_lines.append(f"  - {label}: {position}")
        if pos_lines:
            lines.append("**Typical Label Positions**:")
            lines.extend(pos_lines)

    # Pattern matching
    if patterns.get("barcode_pattern"):
        lines.append(f"**Barcode Pattern**: `{patterns['barcode_pattern']}`")

    special_markers = patterns.get("special_markers")
    if (
        special_markers
        and isinstance(special_markers, list)
        and special_markers
    ):
        lines.append(f"**Special Markers**: {', '.join(special_markers)}")

    product_patterns = patterns.get("product_name_patterns")
    if (
        product_patterns
        and isinstance(product_patterns, list)
        and product_patterns
    ):
        lines.append("**Product Name Patterns**:")
        for p in product_patterns[:3]:  # Limit to 3 for prompt size
            lines.append(f"  - {p}")

    return "\n".join(lines)


def format_currency_context_table(currency_items: list[dict]) -> str:
    """Format currency items as a markdown table for the LLM prompt.

    Note: We intentionally do NOT show validation_status here because we want
    the LLM to make an independent judgment based on mathematical relationships,
    not confirm/deny existing labels.
    """
    if not currency_items:
        return "No currency amounts found on this receipt."

    lines = ["| Amount | Current Label | Line | Preceding Text |"]
    lines.append("|--------|---------------|------|----------------|")

    for item in currency_items:
        amount = f"${item['amount']:.2f}"
        label = item["label"] or "(unlabeled)"
        line_id = item["line_id"]
        context = (
            item["context"][:25] + "..."
            if len(item["context"]) > 25
            else item["context"]
        )

        lines.append(f"| {amount} | {label} | {line_id} | {context} |")

    return "\n".join(lines)


def compute_currency_math_hints(currency_items: list[dict]) -> str:
    """
    Compute mathematical relationships between currency amounts.

    Returns hints like "LINE_TOTALs sum to $10.78" to help LLM reason.
    """
    hints = []

    # Group by label
    by_label: dict[str, list[float]] = {}
    for item in currency_items:
        label = item.get("label")
        if label:
            if label not in by_label:
                by_label[label] = []
            by_label[label].append(item["amount"])

    # Sum of LINE_TOTALs and UNIT_PRICEs (both represent item amounts)
    line_totals = by_label.get("LINE_TOTAL", [])
    unit_prices = by_label.get("UNIT_PRICE", [])
    item_amounts = line_totals + unit_prices

    if item_amounts:
        total = sum(item_amounts)
        label_desc = []
        if line_totals:
            label_desc.append(f"{len(line_totals)} LINE_TOTAL")
        if unit_prices:
            label_desc.append(f"{len(unit_prices)} UNIT_PRICE")
        hints.append(
            f"- Item amounts ({', '.join(label_desc)}): sum to ${total:.2f}"
        )

    # Check for GRAND_TOTAL match against item amounts
    grand_totals = by_label.get("GRAND_TOTAL", [])
    if grand_totals and item_amounts:
        items_sum = sum(item_amounts)
        for gt in set(grand_totals):
            if abs(gt - items_sum) < 0.01:
                hints.append(
                    f"- GRAND_TOTAL ${gt:.2f} equals sum of item amounts "
                    f"(${items_sum:.2f}) - mathematically consistent"
                )
            else:
                diff = gt - items_sum
                if diff > 0:
                    hints.append(
                        f"- GRAND_TOTAL ${gt:.2f} = items (${items_sum:.2f}) + "
                        f"${diff:.2f} (likely tax/fees)"
                    )
                else:
                    hints.append(
                        f"- GRAND_TOTAL ${gt:.2f} is ${abs(diff):.2f} less than "
                        f"items sum (${items_sum:.2f}) - possible discount"
                    )

    # Check for duplicate amounts
    amount_counts: dict[float, int] = {}
    for item in currency_items:
        amt = item["amount"]
        amount_counts[amt] = amount_counts.get(amt, 0) + 1

    duplicates = [(amt, cnt) for amt, cnt in amount_counts.items() if cnt > 1]
    if duplicates:
        for amt, cnt in duplicates:
            labels = [
                item["label"]
                for item in currency_items
                if item["amount"] == amt and item["label"]
            ]
            if labels:
                hints.append(
                    f"- ${amt:.2f} appears {cnt} times with labels: "
                    f"{', '.join(labels)}"
                )

    return "\n".join(hints) if hints else "No mathematical patterns detected."


# =============================================================================
# Single-Issue Review Prompt
# =============================================================================


def build_review_prompt(
    issue: dict[str, Any],
    similar_evidence: list["SimilarWordEvidence"],
    similarity_dist: "SimilarityDistribution",
    label_dist: dict[str, "LabelDistributionStats"],
    merchant_breakdown: list["MerchantBreakdown"],
    merchant_name: str,
    merchant_receipt_count: int,
    currency_context: Optional[list[dict]] = None,
    line_item_patterns: Optional[dict] = None,
) -> str:
    """Build comprehensive LLM review prompt with full context."""

    word_text = issue.get("word_text", "")
    current_label = issue.get("current_label") or "NONE (unlabeled)"
    issue_type = issue.get("type", "unknown")
    evaluator_reasoning = issue.get("reasoning", "No reasoning provided")

    # Build similar words section
    same_merchant_examples = []
    other_merchant_examples = []

    for e in similar_evidence[:30]:  # Show top 30
        line = (
            f"- \"{e['word_text']}\" (similarity: {e['similarity_score']:.0%})"
        )
        line += f"\n  Context: `{e['left_neighbor']}` | **{e['word_text']}** "
        line += f"| `{e['right_neighbor']}`"
        line += f"\n  Position: {e['position_description']}"

        if e["validated_as"]:
            for v in e["validated_as"][:2]:
                reasoning = v.get("reasoning") or "no reasoning recorded"
                line += (
                    f"\n  VALIDATED as **{v['label']}**: \"{reasoning[:100]}\""
                )

        if e["invalidated_as"]:
            for v in e["invalidated_as"][:2]:
                reasoning = v.get("reasoning") or "no reasoning recorded"
                line += f"\n  INVALIDATED as **{v['label']}**: \"{reasoning[:100]}\""

        if e["is_same_merchant"]:
            same_merchant_examples.append(line)
        else:
            other_merchant_examples.append(line)

    # Build distribution summary
    dist_summary = (
        f"- {similarity_dist['very_high']} words with similarity >= 90% "
        f"(very similar)\n"
        f"- {similarity_dist['high']} words with similarity 70-90% (similar)\n"
        f"- {similarity_dist['medium']} words with similarity 50-70% "
        f"(somewhat similar)\n"
        f"- {similarity_dist['low']} words with similarity < 50% "
        f"(weak matches)"
    )

    # Build label distribution
    label_summary_lines = []
    for label, stats in sorted(
        label_dist.items(), key=lambda x: -x[1]["count"]
    )[:10]:
        examples = ", ".join(stats["example_words"][:3])
        label_summary_lines.append(
            f"- **{label}**: {stats['count']} occurrences "
            f"({stats['valid_count']} validated, "
            f"{stats['invalid_count']} invalidated) "
            f'e.g., "{examples}"'
        )
    label_summary = "\n".join(label_summary_lines) or "No label data available"

    # Merchant breakdown
    merchant_lines = []
    for m in merchant_breakdown[:5]:
        marker = " (SAME MERCHANT)" if m["is_same_merchant"] else ""
        labels_str = ", ".join(
            f"{label}: {cnt}" for label, cnt in list(m["labels"].items())[:3]
        )
        merchant_lines.append(f"- {m['merchant_name']}{marker}: {labels_str}")
    merchant_summary = "\n".join(merchant_lines) or "No merchant data"

    # Data sparsity note
    if merchant_receipt_count < 10:
        sparsity_note = (
            f"\n**NOTE**: Only {merchant_receipt_count} receipts available "
            f"for {merchant_name}. Cross-merchant examples are shown for "
            f"additional context.\n"
        )
    else:
        sparsity_note = ""

    prompt = f"""# Receipt Label Validation Task

You are reviewing a potential labeling issue on a receipt. Analyze the evidence
carefully and make a decision about whether the current label is correct.

## The Issue Being Reviewed

**Word**: "{word_text}"
**Current Label**: {current_label}
**Issue Type**: {issue_type}
**Evaluator's Concern**: {evaluator_reasoning}

## Label Definitions

{CORE_LABELS}

## Semantic Similarity Evidence
{sparsity_note}
The following words are semantically similar to "{word_text}" based on their
embeddings (which include surrounding context). The similarity score indicates
how close the embedding is to the target word.

### Distribution Summary

{dist_summary}

### Label Distribution Across Similar Words

{label_summary}

### By Merchant

{merchant_summary}

### From Same Merchant ({merchant_name})

{chr(10).join(same_merchant_examples[:15]) if same_merchant_examples else "No examples from same merchant"}

### From Other Merchants

{chr(10).join(other_merchant_examples[:15]) if other_merchant_examples else "No cross-merchant examples"}

## This Receipt's Currency Amounts

{format_currency_context_table(currency_context) if currency_context else "Currency context not available."}

### Mathematical Observations

{compute_currency_math_hints(currency_context) if currency_context else "No math hints available."}

**IMPORTANT**: When reviewing currency-related labels (LINE_TOTAL, SUBTOTAL, TAX,
GRAND_TOTAL, TENDER, CHANGE), use the table above to understand the relationships
between amounts on THIS receipt. The same dollar amount may correctly appear with
the same label multiple times (e.g., GRAND_TOTAL shown at top and bottom of receipt).

## Line Item Patterns for {merchant_name}

{format_line_item_patterns(line_item_patterns)}

**IMPORTANT**: When reviewing line item labels (PRODUCT_NAME, QUANTITY, UNIT_PRICE,
LINE_TOTAL, SKU), use the patterns above to understand how line items are structured
for this specific merchant. Multi-line items may have the product name, quantity, and
price on separate lines that all belong to the same item.

## Your Task

Based on all evidence above, determine:

1. **Decision**:
   - VALID: The current label is correct (including "NONE (unlabeled)" for words that don't need a label - like footer text, legal disclaimers, or promotional messages)
   - INVALID: The current label is wrong AND you can specify the correct label from the definitions above
   - NEEDS_REVIEW: Genuinely ambiguous, needs human review

2. **Reasoning**: Cite specific evidence from the similar words

3. **Suggested Label**: If INVALID, the correct label from the definitions above. Use null if the word should remain unlabeled. NEVER invent labels like "OTHER" or "WEIGHT" - only use labels from the definitions above.

4. **Confidence**: low / medium / high

**IMPORTANT**: Many receipt words (promotional text, legal disclaimers, survey info) correctly have no label. For these, use VALID with null suggested_label - do NOT mark them INVALID with "OTHER".

Respond with ONLY a JSON object:
```json
{{
  "decision": "VALID | INVALID | NEEDS_REVIEW",
  "reasoning": "Your detailed reasoning citing evidence...",
  "suggested_label": "LABEL_NAME or null",
  "confidence": "low | medium | high"
}}
```
"""
    return prompt


# =============================================================================
# Batched Review Prompt
# =============================================================================

DEFAULT_ISSUES_PER_LLM_CALL = 10


def build_batched_review_prompt(
    issues_with_context: list[dict[str, Any]],
    merchant_name: str,
    merchant_receipt_count: int,
    line_item_patterns: Optional[dict] = None,
) -> str:
    """
    Build a batched LLM review prompt for multiple issues.

    Args:
        issues_with_context: List of dicts, each containing:
            - issue: The issue dict
            - evidence_text: Pre-formatted cascade evidence string
            - consensus: Consensus score from cascade evidence
            - positive_count: Number of supporting evidence items
            - negative_count: Number of contradicting evidence items
            - currency_context: Currency amounts from receipt
        merchant_name: The merchant name
        merchant_receipt_count: Number of receipts for this merchant
        line_item_patterns: Optional line item patterns

    Returns:
        Combined prompt for all issues
    """
    issues_text = []

    for idx, item in enumerate(issues_with_context):
        issue = item["issue"]
        evidence_text = item.get("evidence_text", "")
        currency_context = item.get("currency_context", [])

        word_text = issue.get("word_text", "")
        current_label = issue.get("current_label") or "NONE (unlabeled)"
        issue_type = issue.get("type", "unknown")
        evaluator_reasoning = issue.get("reasoning", "No reasoning provided")

        # Currency context (condensed)
        currency_str = ""
        if currency_context:
            amounts = [
                f"{c.get('label', '?')}: {c.get('text', '?')}"
                for c in currency_context[:8]
            ]
            currency_str = f"\n  Currency amounts: {', '.join(amounts)}"

        # Evidence section
        evidence_section = evidence_text if evidence_text else "No evidence available"

        issue_block = f"""
---
## Issue {idx}

**Word**: "{word_text}"
**Current Label**: {current_label}
**Issue Type**: {issue_type}
**Evaluator's Concern**: {evaluator_reasoning}

**Evidence**:
{evidence_section}{currency_str}
"""
        issues_text.append(issue_block)

    # Data sparsity note
    sparsity_note = ""
    if merchant_receipt_count < 10:
        sparsity_note = (
            f"\n**NOTE**: Only {merchant_receipt_count} receipts available "
            f"for {merchant_name}. Cross-merchant examples shown for context.\n"
        )

    prompt = f"""# Batch Receipt Label Validation

You are reviewing {len(issues_with_context)} potential labeling issues for
receipts from **{merchant_name}**. Analyze each issue and provide decisions.
{sparsity_note}
## Label Definitions

{CORE_LABELS}

## Line Item Patterns for {merchant_name}

{format_line_item_patterns(line_item_patterns)}

## Issues to Review

{"".join(issues_text)}

---

## Your Task

For EACH issue above (0 to {len(issues_with_context) - 1}), determine:

1. **Decision**:
   - VALID: The current label is correct (including "NONE (unlabeled)" for words that don't need a label - like footer text, legal disclaimers, or promotional messages)
   - INVALID: The current label is wrong AND you can specify the correct label from the definitions above
   - NEEDS_REVIEW: Genuinely ambiguous, needs human review

2. **Reasoning**: Brief justification citing evidence

3. **Suggested Label**: If INVALID, the correct label from the definitions above. Use null if the word should remain unlabeled. NEVER invent labels like "OTHER" or "WEIGHT" - only use labels from the definitions above.

4. **Confidence**: low / medium / high

**IMPORTANT**: Many receipt words (promotional text, legal disclaimers, survey info) correctly have no label. For these, use VALID with null suggested_label - do NOT mark them INVALID with "OTHER".

Respond with ONLY a JSON object containing a "reviews" array:
```json
{{
  "reviews": [
    {{
      "issue_index": 0,
      "decision": "VALID | INVALID | NEEDS_REVIEW",
      "reasoning": "Brief reasoning...",
      "suggested_label": "LABEL_NAME or null",
      "confidence": "low | medium | high"
    }},
    {{
      "issue_index": 1,
      "decision": "...",
      "reasoning": "...",
      "suggested_label": "...",
      "confidence": "..."
    }}
  ]
}}
```

IMPORTANT: You MUST provide exactly {len(issues_with_context)} reviews, one for each issue index from 0 to {len(issues_with_context) - 1}.
"""
    return prompt


# =============================================================================
# Receipt-Context Prompt Builder
# =============================================================================


def build_receipt_context_prompt(
    receipt_text: str,
    issues_with_context: list[dict[str, Any]],
    merchant_name: str,
    merchant_receipt_count: int,
    line_item_patterns: Optional[dict] = None,
) -> str:
    """
    Build a prompt with full receipt context for a single receipt's issues.

    Shows the complete receipt text with issue words highlighted, then
    details each issue with similar word evidence including reasoning.

    Args:
        receipt_text: Pre-assembled receipt text with highlighted words
        issues_with_context: List of issue dicts for THIS receipt, each with:
            - issue: The issue dict (line_id, word_id, current_label, etc.)
            - similar_evidence: List of SimilarWordEvidence with reasoning (legacy)
            OR (new targeted format):
            - evidence_text: Pre-formatted evidence string
            - consensus: Consensus score (-1 to 1)
            - positive_count: Number of positive evidence items
            - negative_count: Number of negative evidence items
        merchant_name: The merchant name
        merchant_receipt_count: Number of receipts for this merchant
        line_item_patterns: Optional line item patterns

    Returns:
        Prompt with receipt context and issues
    """
    # Build issue details with similar word reasoning
    issues_text = []
    for idx, item in enumerate(issues_with_context):
        issue = item.get("issue", {})

        # Check for new targeted evidence format first
        evidence_text = item.get("evidence_text")

        word_text = issue.get("word_text", "")
        current_label = issue.get("current_label") or "NONE (unlabeled)"
        issue_type = issue.get("type", "unknown")
        evaluator_reasoning = issue.get("reasoning", "No reasoning provided")

        # Use pre-formatted evidence if available (new targeted format)
        if evidence_text is not None:
            similar_text = evidence_text
        else:
            # Legacy format: build from similar_evidence list
            similar_evidence = item.get("similar_evidence")

            if similar_evidence is None:
                logger.warning(
                    "Issue %d: similar_evidence is None (item keys: %s)",
                    idx,
                    list(item.keys()),
                )
                similar_evidence = []
            elif not isinstance(similar_evidence, list):
                logger.warning(
                    "Issue %d: similar_evidence is %s, not list. Value: %s",
                    idx,
                    type(similar_evidence).__name__,
                    str(similar_evidence)[:200],
                )
                similar_evidence = []

            similar_text = _build_legacy_similar_text(similar_evidence, idx)

        # Build drill-down section for constellation anomalies
        drill_down_text = ""
        drill_down = issue.get("drill_down")
        if drill_down and issue_type == "constellation_anomaly":
            drill_down_text = _build_drill_down_text(drill_down)

        issues_text.append(f"""
### Issue {idx}: "{word_text}" labeled as {current_label}
**Type**: {issue_type}
**Evaluator reasoning**: {evaluator_reasoning}

**Similar Word Evidence**:
{similar_text}
{drill_down_text}
""")

    # Build patterns section if available
    patterns_text = ""
    if line_item_patterns:
        patterns_text = _build_patterns_text(line_item_patterns)

    prompt = f"""You are reviewing flagged receipt labels for accuracy.

## Merchant: {merchant_name}
Receipts analyzed from this merchant: {merchant_receipt_count}
{patterns_text}

## Allowed Labels (CORE_LABELS)
{CORE_LABELS}

## Receipt Text (flagged words in **bold**)
```
{receipt_text}
```

## Flagged Issues to Review
{"".join(issues_text)}

## Your Task
For each issue, decide:
- **VALID**: The current label is correct
- **INVALID**: The label is wrong - provide the correct label
- **NEEDS_REVIEW**: Uncertain - needs human review

Consider:
1. The word's position and context on the receipt
2. Similar word evidence (consensus score indicates confidence)
3. Patterns from other receipts of this merchant
4. Whether the word should have no label at all (promotional text, legal disclaimers)

**IMPORTANT**:
- Use ONLY labels listed in CORE_LABELS above.
- If the word should remain unlabeled (promo/legal/footer text), use VALID with null suggested_label.

Respond with ONLY a JSON object:
```json
{{
  "reviews": [
    {{
      "issue_index": 0,
      "decision": "VALID | INVALID | NEEDS_REVIEW",
      "reasoning": "Brief reasoning...",
      "suggested_label": "CORE_LABEL or null",
      "confidence": "low | medium | high"
    }}
  ]
}}
```

You MUST provide exactly {len(issues_with_context)} reviews, one for each issue index from 0 to {len(issues_with_context) - 1}.
"""
    return prompt


def _build_legacy_similar_text(
    similar_evidence: list[dict[str, Any]], idx: int
) -> str:
    """Build similar text from legacy similar_evidence format."""
    similar_lines = []
    for e_idx, e in enumerate(similar_evidence[:10]):
        if e is None:
            logger.warning("Issue %d: evidence[%d] is None", idx, e_idx)
            continue
        if not isinstance(e, dict):
            logger.warning(
                "Issue %d: evidence[%d] is %s, not dict",
                idx,
                e_idx,
                type(e).__name__,
            )
            continue

        sim_score = e.get("similarity_score", 0)
        sim_word = e.get("word_text", "")
        is_same = "same merchant" if e.get("is_same_merchant") else "other"

        # Get validation reasoning - with null safety
        validated_info = []
        validated_as = e.get("validated_as")
        if validated_as and isinstance(validated_as, list):
            for v in validated_as[:2]:
                if not isinstance(v, dict):
                    continue
                label = v.get("label", "?")
                reasoning = (v.get("reasoning") or "no reasoning")[:80]
                validated_info.append(f'{label}: "{reasoning}"')

        invalidated_info = []
        invalidated_as = e.get("invalidated_as")
        if invalidated_as and isinstance(invalidated_as, list):
            for v in invalidated_as[:1]:
                if not isinstance(v, dict):
                    continue
                label = v.get("label", "?")
                reasoning = (v.get("reasoning") or "no reasoning")[:60]
                invalidated_info.append(f'~~{label}~~: "{reasoning}"')

        # Format line
        line = f'- "{sim_word}" ({sim_score:.0%}, {is_same})'
        if validated_info:
            line += f" -> {'; '.join(validated_info)}"
        if invalidated_info:
            line += f" | {'; '.join(invalidated_info)}"
        similar_lines.append(line)

    return (
        "\n".join(similar_lines) if similar_lines else "No similar words found"
    )


def _build_drill_down_text(drill_down: list[dict[str, Any]]) -> str:
    """Build drill-down text for constellation anomalies."""
    culprits = [w for w in drill_down if w.get("is_culprit")]
    non_culprits = [w for w in drill_down if not w.get("is_culprit")]

    def _get_y_position(pos: Any) -> float:
        if pos is None:
            return 0.0
        if isinstance(pos, (int, float)):
            return float(pos)
        if isinstance(pos, dict):
            return float(pos.get("y", 0))
        return 0.0

    drill_down_lines = [
        f"\n**Drill-Down Analysis** ({len(drill_down)} words with this label):"
    ]

    if non_culprits:
        drill_down_lines.append(
            f"Normal positions ({len(non_culprits)} words):"
        )
        for w in sorted(
            non_culprits, key=lambda x: _get_y_position(x.get("position"))
        )[:5]:
            y = _get_y_position(w.get("position"))
            drill_down_lines.append(f'  - "{w.get("text", "?")}" at y={y:.2f}')

    if culprits:
        drill_down_lines.append(f"Outlier positions ({len(culprits)} words):")
        for w in culprits:
            y = _get_y_position(w.get("position"))
            deviation = w.get("deviation", 0)
            drill_down_lines.append(
                f'  - "{w.get("text", "?")}" at y={y:.2f} '
                f"({deviation:.1f}σ from mean)"
            )

    return "\n".join(drill_down_lines)


def _build_patterns_text(line_item_patterns: Optional[dict]) -> str:
    """Build patterns section text."""
    if not line_item_patterns:
        return ""
    return f"""
## Line Item Patterns
{format_line_item_patterns(line_item_patterns)}
"""


# =============================================================================
# Response Parsing
# =============================================================================


def parse_llm_response(response_text: str) -> dict[str, Any]:
    """
    Parse single-issue LLM JSON response.

    Returns:
        Dict with keys: decision, reasoning, suggested_label, confidence
    """
    response_text = extract_json_from_response(response_text)

    try:
        result = json.loads(response_text)
        decision = result.get("decision", "NEEDS_REVIEW")
        if decision not in ("VALID", "INVALID", "NEEDS_REVIEW"):
            decision = "NEEDS_REVIEW"

        confidence = result.get("confidence", "medium")
        if confidence not in ("low", "medium", "high"):
            confidence = "medium"

        # Validate suggested_label is in CORE_LABELS
        suggested_label = result.get("suggested_label")
        if suggested_label:
            suggested_upper = suggested_label.upper()
            if suggested_upper not in CORE_LABELS_SET:
                # Debug level: This is expected behavior - LLM may suggest
                # invalid labels like "OTHER" which we reject, causing the
                # word to remain unlabeled (which is often correct)
                logger.debug(
                    "Rejecting invalid suggested_label '%s' (not in CORE_LABELS)",
                    suggested_label,
                )
                suggested_label = None
            else:
                suggested_label = suggested_upper  # Normalize to uppercase

        return {
            "decision": decision,
            "reasoning": result.get("reasoning", "No reasoning provided"),
            "suggested_label": suggested_label,
            "confidence": confidence,
        }
    except json.JSONDecodeError:
        return {
            "decision": "NEEDS_REVIEW",
            "reasoning": f"Failed to parse LLM response: {response_text[:200]}",
            "suggested_label": None,
            "confidence": "low",
        }


def parse_batched_llm_response(
    response_text: str,
    expected_count: int,
    raise_on_parse_error: bool = False,
) -> list[dict[str, Any]]:
    """
    Parse batched LLM JSON response.

    First attempts to parse using the BatchedReviewResponse Pydantic model,
    which validates the schema and constrains suggested_label to valid values.
    Falls back to manual JSON parsing if structured parsing fails.

    Args:
        response_text: Raw LLM response
        expected_count: Expected number of reviews
        raise_on_parse_error: If True, raise LLMResponseParseError on JSON
            parse failure instead of returning fallback NEEDS_REVIEW values.
            Use this to enable retry logic in the caller.

    Returns:
        List of dicts, one per issue, each with:
            decision, reasoning, suggested_label, confidence

    Raises:
        LLMResponseParseError: If raise_on_parse_error=True and JSON parsing fails
    """
    response_text = extract_json_from_response(response_text)

    # Default fallback for all issues
    fallback = {
        "decision": "NEEDS_REVIEW",
        "reasoning": "Failed to parse batched LLM response",
        "suggested_label": None,
        "confidence": "low",
    }

    # Try to parse JSON first
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse batched LLM response as JSON: %s", e)
        if raise_on_parse_error:
            raise LLMResponseParseError(f"JSON parse failed: {e}") from e
        return [fallback.copy() for _ in range(expected_count)]

    # Try structured validation (validates schema and label values)
    try:
        structured_response = BatchedReviewResponse.model_validate(result)
        return structured_response.to_ordered_list(expected_count)
    except ValidationError as e:
        logger.debug(
            "Structured validation failed, falling back to manual parsing: %s",
            e,
        )

    # Fallback to manual parsing for backwards compatibility
    # (result is already parsed, reuse it)
    reviews = result.get("reviews", [])

    if not isinstance(reviews, list):
        logger.warning("Batched response 'reviews' is not a list")
        return [fallback.copy() for _ in range(expected_count)]

    # Build a map by issue_index
    reviews_by_index: dict[int, dict[str, Any]] = {}

    for review in reviews:
        if not isinstance(review, dict):
            continue

        idx = review.get("issue_index")
        if idx is None or not isinstance(idx, int):
            continue

        decision = review.get("decision", "NEEDS_REVIEW")
        if decision not in ("VALID", "INVALID", "NEEDS_REVIEW"):
            decision = "NEEDS_REVIEW"

        confidence = review.get("confidence", "medium")
        if confidence not in ("low", "medium", "high"):
            confidence = "medium"

        # Validate suggested_label is in CORE_LABELS
        suggested_label = review.get("suggested_label")
        if suggested_label:
            suggested_upper = suggested_label.upper()
            if suggested_upper not in CORE_LABELS_SET:
                # Debug level: This is expected behavior - LLM may suggest
                # invalid labels like "OTHER" which we reject, causing the
                # word to remain unlabeled (which is often correct)
                logger.debug(
                    "Rejecting invalid suggested_label '%s' (not in CORE_LABELS)",
                    suggested_label,
                )
                suggested_label = None
            else:
                suggested_label = suggested_upper  # Normalize to uppercase

        reviews_by_index[idx] = {
            "decision": decision,
            "reasoning": review.get("reasoning", "No reasoning provided"),
            "suggested_label": suggested_label,
            "confidence": confidence,
        }

    # Build ordered list, using fallback for missing indices
    ordered_reviews = []
    for i in range(expected_count):
        if i in reviews_by_index:
            ordered_reviews.append(reviews_by_index[i])
        else:
            logger.warning("Missing review for issue index %d", i)
            ordered_reviews.append(
                {
                    "decision": "NEEDS_REVIEW",
                    "reasoning": f"LLM did not provide review for issue {i}",
                    "suggested_label": None,
                    "confidence": "low",
                }
            )

    return ordered_reviews


def invoke_with_structured_output(
    llm: Any,
    prompt: str,
    expected_count: int,
    run_name: str = "llm_review",
) -> list[dict[str, Any]]:
    """
    Invoke LLM with structured output for batched reviews.

    Uses LangChain's with_structured_output() to constrain the LLM response
    to the BatchedReviewResponse schema, preventing invalid labels at the
    token generation level.

    Args:
        llm: LangChain LLM instance (ChatOpenAI configured for OpenRouter)
        prompt: The review prompt
        expected_count: Expected number of reviews
        run_name: Name for tracing

    Returns:
        List of review dicts, one per issue
    """
    # Check if LLM supports structured output
    if not hasattr(llm, "with_structured_output"):
        logger.debug(
            "LLM %s does not support with_structured_output, using text parsing",
            type(llm).__name__,
        )
        response = llm.invoke(
            [HumanMessage(content=prompt)],
            config={"run_name": run_name},
        )
        return parse_batched_llm_response(response.content, expected_count)

    try:
        # Create structured LLM
        structured_llm = llm.with_structured_output(BatchedReviewResponse)

        # Invoke with structured output
        response: BatchedReviewResponse = structured_llm.invoke(
            [HumanMessage(content=prompt)],
            config={"run_name": run_name},
        )

        return response.to_ordered_list(expected_count)

    except Exception as e:
        logger.warning(
            "Structured output invocation failed (type=%s), falling back to text parsing: %s",
            type(e).__name__,
            e,
        )
        # Fall back to regular invocation + parsing
        response = llm.invoke(
            [HumanMessage(content=prompt)],
            config={"run_name": run_name},
        )
        return parse_batched_llm_response(response.content, expected_count)

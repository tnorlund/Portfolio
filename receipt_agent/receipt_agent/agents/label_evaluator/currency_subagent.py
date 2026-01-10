"""
Currency validation subagent for line item label evaluation.

This is a separate evaluation step that runs after EvaluateLabels.
It uses line item patterns from DiscoverLineItemPatterns to validate
currency labels on line item rows.

Input:
    - visual_lines: Words grouped by y-coordinate with current labels
    - patterns: From DiscoverLineItemPatterns (label_positions, grouping_rule, etc.)
    - image_id, receipt_id: For output format

Output:
    List of decisions ready for apply_llm_decisions():
    [
        {
            "image_id": "...",
            "receipt_id": 1,
            "issue": {"line_id": 5, "word_id": 3, "current_label": "LINE_TOTAL"},
            "llm_review": {
                "decision": "VALID" | "INVALID" | "NEEDS_REVIEW",
                "reasoning": "...",
                "suggested_label": "LINE_TOTAL" or None,
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
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError
from receipt_agent.constants import LINE_ITEM_EVALUATION_LABELS
from receipt_agent.prompts.structured_outputs import (
    CurrencyEvaluationResponse,
    extract_json_from_response,
)

from .state import EvaluationIssue, VisualLine, WordContext

logger = logging.getLogger(__name__)

# Labels that indicate a line item row
LINE_ITEM_LABELS = {"PRODUCT_NAME", "LINE_TOTAL", "UNIT_PRICE", "QUANTITY"}

# Patterns that look like numbers but aren't currency
NON_CURRENCY_PATTERNS = [
    (
        re.compile(r"^\d{3}[-.]?\d{3}[-.]?\d{4}$"),
        "PHONE_NUMBER",
    ),  # 555-123-4567
    (re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$"), "DATE"),  # 12/25/2024
    (
        re.compile(r"^\d{5}(-\d{4})?$"),
        "ADDRESS_LINE",
    ),  # ZIP: 90210, 90210-1234
    (
        re.compile(r"^\d{2}:\d{2}(:\d{2})?([ ]?[AP]M)?$", re.I),
        "TIME",
    ),  # 14:30, 2:30 PM
    (re.compile(r"^#\d{4,}$"), "ORDER_NUMBER"),  # #12345
]

# Pattern for currency-like values
CURRENCY_PATTERN = re.compile(r"^\$?\d{1,3}(,\d{3})*\.\d{2}$")


@dataclass
class LineItemRow:
    """A visual line identified as a line item row."""

    line: VisualLine
    labels: set[str]
    currency_words: list[WordContext]


@dataclass
class CurrencyWord:
    """A word that is currency-related (labeled or should be labeled)."""

    word_context: WordContext
    current_label: Optional[str]
    line_index: int
    position_zone: str  # "left", "center", "right"
    looks_like_currency: bool
    non_currency_pattern: Optional[str]  # If it matches a non-currency pattern


# =============================================================================
# Helper Functions
# =============================================================================


def get_position_zone(x: float, zones: Optional[dict] = None) -> str:
    """Determine which zone (left/center/right) a word is in."""
    if zones:
        left_bounds = zones.get("left", [0, 0.33])
        center_bounds = zones.get("center", [0.33, 0.66])
        if x <= left_bounds[1]:
            return "left"
        elif x <= center_bounds[1]:
            return "center"
        else:
            return "right"
    # Default zones
    if x <= 0.33:
        return "left"
    elif x <= 0.66:
        return "center"
    else:
        return "right"


def looks_like_currency(text: str) -> bool:
    """Check if text looks like a currency value."""
    # Fast regex check first (matches $1,234.56 format)
    if CURRENCY_PATTERN.match(text):
        return True

    # Fallback: remove symbols and try numeric heuristic
    clean = text.replace("$", "").replace(",", "")
    try:
        val = float(clean)
        # Must have decimal and be reasonable
        return "." in text and 0 < val < 100000
    except ValueError:
        return False


def get_non_currency_pattern(text: str) -> Optional[str]:
    """Check if text matches a non-currency pattern like phone, date, etc."""
    for pattern, label in NON_CURRENCY_PATTERNS:
        if pattern.match(text):
            return label
    return None


def identify_line_item_rows(
    visual_lines: list[VisualLine],
    patterns: Optional[
        dict
    ] = None,  # Reserved for future pattern-based detection
) -> list[LineItemRow]:
    """
    Identify which visual lines are line item rows based on patterns.

    Args:
        visual_lines: All visual lines from the receipt
        patterns: Line item patterns from discover_patterns_with_llm() (reserved for future use)

    Returns:
        List of LineItemRow objects
    """
    _ = patterns  # Suppress unused warning until pattern-based detection is implemented
    rows = []
    for line in visual_lines:
        line_labels = {
            wc.current_label.label
            for wc in line.words
            if wc.current_label is not None
        }

        # A line is a line item row if it contains any line item labels
        if line_labels & LINE_ITEM_LABELS:
            # Collect currency words on this line
            currency_words = [
                wc
                for wc in line.words
                if wc.current_label is not None
                and wc.current_label.label in LINE_ITEM_EVALUATION_LABELS
            ]
            rows.append(
                LineItemRow(
                    line=line,
                    labels=line_labels,
                    currency_words=currency_words,
                )
            )

    return rows


def collect_currency_words(
    visual_lines: list[VisualLine],
    line_item_rows: list[LineItemRow],
    patterns: Optional[dict] = None,
) -> list[CurrencyWord]:
    """
    Collect all words that need evaluation on line item rows.

    This includes:
    1. Words with line item labels (LINE_TOTAL, UNIT_PRICE, PRODUCT_NAME, QUANTITY, etc.)
    2. Unlabeled words on line item rows that look like currency
    """
    x_zones = patterns.get("x_position_zones") if patterns else None
    currency_words = []
    line_item_line_indices = {row.line.line_index for row in line_item_rows}

    for line in visual_lines:
        for wc in line.words:
            current_label = (
                wc.current_label.label if wc.current_label else None
            )

            # Check if word has a currency label (LINE_TOTAL, UNIT_PRICE, etc.)
            has_eval_label = current_label in LINE_ITEM_EVALUATION_LABELS

            # Check if word looks like currency and is on a line item row
            text = wc.word.text
            is_currency_like = looks_like_currency(text)
            is_on_line_item_row = line.line_index in line_item_line_indices
            non_currency = get_non_currency_pattern(text)

            # Include if:
            # 1. Has a line item evaluation label (currency OR PRODUCT_NAME/QUANTITY), OR
            # 2. Looks like currency AND on line item row (potential unlabeled currency)
            if has_eval_label or (
                is_currency_like and is_on_line_item_row and not non_currency
            ):
                currency_words.append(
                    CurrencyWord(
                        word_context=wc,
                        current_label=current_label,
                        line_index=line.line_index,
                        position_zone=get_position_zone(
                            wc.normalized_x, x_zones
                        ),
                        looks_like_currency=is_currency_like,
                        non_currency_pattern=non_currency,
                    )
                )

    return currency_words


# =============================================================================
# LLM Prompt Building
# =============================================================================


def build_currency_evaluation_prompt(
    visual_lines: list[VisualLine],
    currency_words: list[CurrencyWord],
    patterns: Optional[dict] = None,
    merchant_name: str = "Unknown",
) -> str:
    """
    Build the LLM prompt for currency label evaluation.

    Shows the receipt structure and asks the LLM to evaluate each currency word.
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

    receipt_text = "\n".join(receipt_lines[:50])  # Limit to 50 lines
    if len(visual_lines) > 50:
        receipt_text += f"\n  ... ({len(visual_lines) - 50} more lines)"

    # Build currency words table
    words_table = []
    for i, cw in enumerate(currency_words):
        wc = cw.word_context
        words_table.append(
            f"  [{i}] Line {cw.line_index}, Zone: {cw.position_zone}\n"
            f'      Text: "{wc.word.text}"\n'
            f"      Current Label: {cw.current_label or 'unlabeled'}\n"
            f"      Looks like currency: {cw.looks_like_currency}"
        )

    words_text = "\n".join(words_table)

    # Pattern context
    pattern_context = ""
    if patterns:
        label_positions = patterns.get("label_positions", {})
        if label_positions:
            pattern_context = f"""
## Expected Label Positions (from merchant patterns)
{json.dumps(label_positions, indent=2)}
"""

    prompt = f"""# Line Item Label Evaluation for {merchant_name}

You are evaluating labels on line item rows of a receipt. For each word below,
decide if the current label is VALID, INVALID, or NEEDS_REVIEW.

## Receipt Structure
{receipt_text}
{pattern_context}
## Words to Evaluate
{words_text}

## Your Task
For each word above, evaluate the label and respond with a JSON array:

```json
[
  {{
    "index": 0,
    "decision": "VALID" | "INVALID" | "NEEDS_REVIEW",
    "reasoning": "Brief explanation",
    "suggested_label": "LINE_TOTAL" | null,
    "confidence": "high" | "medium" | "low"
  }},
  ...
]
```

## Label Types
- PRODUCT_NAME: The name/description of the product (e.g., "MILK 2% GAL", "CHICKEN BREAST")
- QUANTITY: Number of units (e.g., "2", "1.5", "3 @")
- UNIT_PRICE: Price per unit (e.g., "$2.99", "2.99/lb")
- LINE_TOTAL: Total price for the line item (quantity × unit_price)
- SUBTOTAL, TAX, GRAND_TOTAL: Summary amounts
- DISCOUNT: Discount amount (usually negative or with minus sign)

## Rules
- VALID: The current label is correct for this word
- INVALID: The label is wrong OR an unlabeled word needs a label
- NEEDS_REVIEW: You're unsure and a human should check

Common issues to catch:
- Product names labeled as QUANTITY (text vs numbers)
- Phone numbers or dates labeled as currency
- Prices on the left side labeled as LINE_TOTAL (usually UNIT_PRICE)
- Unlabeled currency values on line item rows

For INVALID words, suggest the correct label.

Respond ONLY with the JSON array, no other text.
"""
    return prompt


def parse_currency_evaluation_response(
    response_text: str,
    num_words: int,
) -> list[dict]:
    """Parse the LLM response into a list of decisions.

    First attempts to parse using the CurrencyEvaluationResponse Pydantic model,
    which validates the schema and constrains suggested_label to valid currency labels.
    Falls back to manual JSON parsing if structured parsing fails.
    """
    response_text = extract_json_from_response(response_text)

    # Default fallback
    fallback = {
        "decision": "NEEDS_REVIEW",
        "reasoning": "Failed to parse LLM response",
        "suggested_label": None,
        "confidence": "low",
    }

    # Try structured parsing first (validates schema and label values)
    try:
        parsed = json.loads(response_text)
        # Handle both array format and object with evaluations key
        if isinstance(parsed, list):
            parsed = {"evaluations": parsed}
        structured_response = CurrencyEvaluationResponse.model_validate(parsed)
        return structured_response.to_ordered_list(num_words)
    except (json.JSONDecodeError, ValidationError) as e:
        logger.debug(
            "Structured parsing failed, falling back to manual parsing: %s", e
        )

    # Fallback to manual parsing for backwards compatibility
    try:
        decisions = json.loads(response_text)
        if isinstance(decisions, dict):
            decisions = decisions.get("evaluations", [])

        # Ensure decisions is a list before iterating
        if not isinstance(decisions, list):
            logger.warning(
                "Decisions is not a list: %s", type(decisions).__name__
            )
            return [fallback.copy() for _ in range(num_words)]

        # Validate and normalize
        result = []
        for i in range(num_words):
            # Find decision for this index
            decision = next(
                (d for d in decisions if d.get("index") == i), None
            )
            if decision:
                result.append(
                    {
                        "decision": decision.get("decision", "NEEDS_REVIEW"),
                        "reasoning": decision.get("reasoning", ""),
                        "suggested_label": decision.get("suggested_label"),
                        "confidence": decision.get("confidence", "medium"),
                    }
                )
            else:
                result.append(
                    {
                        "decision": "NEEDS_REVIEW",
                        "reasoning": "No decision from LLM",
                        "suggested_label": None,
                        "confidence": "low",
                    }
                )

        return result

    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Failed to parse LLM response: %s", e)
        return [fallback.copy() for _ in range(num_words)]


# =============================================================================
# Main Evaluation Function
# =============================================================================


def evaluate_currency_labels(
    visual_lines: list[VisualLine],
    patterns: Optional[dict],
    llm: BaseChatModel,
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
) -> list[dict]:
    """
    Evaluate currency labels on a receipt.

    This is the main entry point for the currency evaluation step.

    Args:
        visual_lines: Visual lines from the receipt (words with labels)
        patterns: Line item patterns from DiscoverLineItemPatterns
        llm: Language model for evaluation
        image_id: Image ID for output format
        receipt_id: Receipt ID for output format
        merchant_name: Merchant name for context

    Returns:
        List of decisions ready for apply_llm_decisions():
        [
            {
                "image_id": "...",
                "receipt_id": 1,
                "issue": {"line_id": 5, "word_id": 3, "current_label": "LINE_TOTAL"},
                "llm_review": {
                    "decision": "VALID" | "INVALID" | "NEEDS_REVIEW",
                    "reasoning": "...",
                    "suggested_label": "LINE_TOTAL" or None,
                    "confidence": "high" | "medium" | "low",
                }
            },
            ...
        ]
    """
    # Step 1: Identify line item rows
    line_item_rows = identify_line_item_rows(visual_lines, patterns)
    logger.info("Identified %s line item rows", len(line_item_rows))

    if not line_item_rows:
        logger.info("No line item rows found, skipping currency evaluation")
        return []

    # Step 2: Collect currency words to evaluate
    currency_words = collect_currency_words(
        visual_lines, line_item_rows, patterns
    )
    logger.info("Found %s currency words to evaluate", len(currency_words))

    if not currency_words:
        logger.info("No currency words found to evaluate")
        return []

    # Step 3: Build prompt and call LLM
    prompt = build_currency_evaluation_prompt(
        visual_lines=visual_lines,
        currency_words=currency_words,
        patterns=patterns,
        merchant_name=merchant_name,
    )

    # Try structured output first, fall back to text parsing
    max_retries = 3
    last_decisions = None
    num_words = len(currency_words)

    # Check if LLM supports structured output
    use_structured = hasattr(llm, "with_structured_output")

    for attempt in range(max_retries):
        try:
            if use_structured:
                try:
                    structured_llm = llm.with_structured_output(
                        CurrencyEvaluationResponse
                    )
                    response: CurrencyEvaluationResponse = (
                        structured_llm.invoke(prompt)
                    )
                    decisions = response.to_ordered_list(num_words)
                    logger.debug(
                        "Structured output succeeded with %d evaluations",
                        len(decisions),
                    )
                except Exception as struct_err:
                    # Structured output failed, fall back to text parsing
                    logger.warning(
                        "Structured output failed (attempt %d), falling back to text: %s",
                        attempt + 1,
                        struct_err,
                    )
                    response = llm.invoke(prompt)
                    response_text = (
                        response.content
                        if hasattr(response, "content")
                        else str(response)
                    )
                    decisions = parse_currency_evaluation_response(
                        response_text, num_words
                    )
            else:
                response = llm.invoke(prompt)
                response_text = (
                    response.content
                    if hasattr(response, "content")
                    else str(response)
                )
                decisions = parse_currency_evaluation_response(
                    response_text, num_words
                )

            last_decisions = decisions

            # Check if all decisions failed to parse (indicates malformed response)
            parse_failures = sum(
                1
                for d in decisions
                if "Failed to parse" in d.get("reasoning", "")
            )

            if parse_failures == 0:
                # Success - no parse failures
                break
            elif parse_failures < len(decisions):
                # Partial success - some parsed, use what we have
                logger.info(
                    "Partial parse success: %d/%d parsed on attempt %d",
                    len(decisions) - parse_failures,
                    len(decisions),
                    attempt + 1,
                )
                break
            else:
                # All failed to parse - retry if we have attempts left
                if attempt < max_retries - 1:
                    logger.warning(
                        "All %d decisions failed to parse on attempt %d, retrying...",
                        len(decisions),
                        attempt + 1,
                    )
                else:
                    logger.warning(
                        "All %d decisions failed to parse after %d attempts",
                        len(decisions),
                        max_retries,
                    )

        except Exception as e:
            # Check if this is a rate limit error that should trigger Step Function retry
            from receipt_agent.utils import (
                BothProvidersFailedError,
                OllamaRateLimitError,
            )

            if isinstance(e, (OllamaRateLimitError, BothProvidersFailedError)):
                logger.error(
                    "Currency LLM rate limited, propagating for retry: %s", e
                )
                raise  # Let Step Function retry handle this

            logger.error(
                "Currency LLM call failed on attempt %d: %s", attempt + 1, e
            )
            if attempt == max_retries - 1:
                # Final attempt failed - return NEEDS_REVIEW for all
                results = []
                for cw in currency_words:
                    wc = cw.word_context
                    results.append(
                        {
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                            "issue": {
                                "line_id": wc.word.line_id,
                                "word_id": wc.word.word_id,
                                "current_label": cw.current_label,
                                "word_text": wc.word.text,
                            },
                            "llm_review": {
                                "decision": "NEEDS_REVIEW",
                                "reasoning": f"LLM call failed after {max_retries} attempts: {e}",
                                "suggested_label": None,
                                "confidence": "low",
                            },
                        }
                    )
                return results

    # Use the last decisions we got (best effort)
    decisions = last_decisions or [
        {
            "decision": "NEEDS_REVIEW",
            "reasoning": "No response received",
            "suggested_label": None,
            "confidence": "low",
        }
        for _ in currency_words
    ]

    # Step 5: Format output for apply_llm_decisions
    # Handle length mismatches by padding with NEEDS_REVIEW fallback
    results = []
    num_words = len(currency_words)
    num_decisions = len(decisions)
    if num_decisions != num_words:
        logger.warning(
            "Decision count mismatch: %d words, %d decisions",
            num_words,
            num_decisions,
        )
        # Pad decisions if too few, or truncate if too many
        while len(decisions) < num_words:
            decisions.append(
                {
                    "decision": "NEEDS_REVIEW",
                    "reasoning": "No decision from LLM (count mismatch)",
                    "suggested_label": None,
                    "confidence": "low",
                }
            )
        decisions = decisions[:num_words]

    for cw, decision in zip(currency_words, decisions, strict=False):
        wc = cw.word_context
        results.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "issue": {
                    "line_id": wc.word.line_id,
                    "word_id": wc.word.word_id,
                    "current_label": cw.current_label,
                    "word_text": wc.word.text,
                },
                "llm_review": decision,
            }
        )

    # Log summary
    decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
    for r in results:
        decision_counts[r["llm_review"]["decision"]] += 1
    logger.info("Currency evaluation results: %s", decision_counts)

    return results


# =============================================================================
# Async version
# =============================================================================


async def evaluate_currency_labels_async(
    visual_lines: list[VisualLine],
    patterns: Optional[dict],
    llm: Any,  # RateLimitedLLMInvoker or BaseChatModel with ainvoke
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
) -> list[dict]:
    """
    Async version of evaluate_currency_labels.

    Uses ainvoke() for concurrent LLM calls. Works with RateLimitedLLMInvoker
    or any LLM that supports ainvoke().

    Args:
        visual_lines: Visual lines from the receipt (words with labels)
        patterns: Line item patterns from DiscoverLineItemPatterns
        llm: Language model invoker (RateLimitedLLMInvoker or BaseChatModel)
        image_id: Image ID for output format
        receipt_id: Receipt ID for output format
        merchant_name: Merchant name for context

    Returns:
        List of decisions ready for apply_llm_decisions()
    """
    # Step 1: Identify line item rows
    line_item_rows = identify_line_item_rows(visual_lines, patterns)
    logger.info("Identified %s line item rows", len(line_item_rows))

    if not line_item_rows:
        logger.info("No line item rows found, skipping currency evaluation")
        return []

    # Step 2: Collect currency words to evaluate
    currency_words = collect_currency_words(
        visual_lines, line_item_rows, patterns
    )
    logger.info("Found %s currency words to evaluate", len(currency_words))

    if not currency_words:
        logger.info("No currency words found to evaluate")
        return []

    # Step 3: Build prompt
    prompt = build_currency_evaluation_prompt(
        visual_lines=visual_lines,
        currency_words=currency_words,
        patterns=patterns,
        merchant_name=merchant_name,
    )

    # Step 4: Call LLM asynchronously
    max_retries = 3
    last_decisions = None
    num_words = len(currency_words)

    # Check if LLM supports structured output
    use_structured = hasattr(llm, "with_structured_output")

    for attempt in range(max_retries):
        try:
            if use_structured:
                try:
                    structured_llm = llm.with_structured_output(
                        CurrencyEvaluationResponse
                    )
                    # Use ainvoke for async call
                    if hasattr(structured_llm, "ainvoke"):
                        response: CurrencyEvaluationResponse = (
                            await structured_llm.ainvoke(prompt)
                        )
                    else:
                        # Fallback to sync if no ainvoke - run in thread pool to avoid blocking
                        response: CurrencyEvaluationResponse = (
                            await asyncio.to_thread(structured_llm.invoke, prompt)
                        )
                    decisions = response.to_ordered_list(num_words)
                    logger.debug(
                        "Structured output succeeded with %d evaluations",
                        len(decisions),
                    )
                except Exception as struct_err:
                    # Structured output failed, fall back to text parsing
                    logger.warning(
                        "Structured output failed (attempt %d), falling back to text: %s",
                        attempt + 1,
                        struct_err,
                    )
                    if hasattr(llm, "ainvoke"):
                        response = await llm.ainvoke(prompt)
                    else:
                        # Run sync invoke in thread pool to avoid blocking event loop
                        response = await asyncio.to_thread(llm.invoke, prompt)
                    response_text = (
                        response.content
                        if hasattr(response, "content")
                        else str(response)
                    )
                    decisions = parse_currency_evaluation_response(
                        response_text, num_words
                    )
            else:
                # Use ainvoke if available, otherwise fall back to invoke
                if hasattr(llm, "ainvoke"):
                    response = await llm.ainvoke(prompt)
                else:
                    # Run sync invoke in thread pool to avoid blocking event loop
                    response = await asyncio.to_thread(llm.invoke, prompt)
                response_text = (
                    response.content
                    if hasattr(response, "content")
                    else str(response)
                )
                decisions = parse_currency_evaluation_response(
                    response_text, num_words
                )

            last_decisions = decisions

            # Check if all decisions failed to parse
            parse_failures = sum(
                1
                for d in decisions
                if "Failed to parse" in d.get("reasoning", "")
            )

            if parse_failures == 0:
                break
            elif parse_failures < len(decisions):
                logger.info(
                    "Partial parse success: %d/%d parsed on attempt %d",
                    len(decisions) - parse_failures,
                    len(decisions),
                    attempt + 1,
                )
                break
            else:
                if attempt < max_retries - 1:
                    logger.warning(
                        "All %d decisions failed to parse on attempt %d, retrying...",
                        len(decisions),
                        attempt + 1,
                    )
                else:
                    logger.warning(
                        "All %d decisions failed to parse after %d attempts",
                        len(decisions),
                        max_retries,
                    )

        except Exception as e:
            from receipt_agent.utils import (
                BothProvidersFailedError,
                OllamaRateLimitError,
            )

            if isinstance(e, (OllamaRateLimitError, BothProvidersFailedError)):
                logger.error(
                    "Currency LLM rate limited, propagating for retry: %s", e
                )
                raise

            logger.error(
                "Currency LLM call failed on attempt %d: %s", attempt + 1, e
            )
            if attempt == max_retries - 1:
                # Final attempt failed - return NEEDS_REVIEW for all
                results = []
                for cw in currency_words:
                    wc = cw.word_context
                    results.append(
                        {
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                            "issue": {
                                "line_id": wc.word.line_id,
                                "word_id": wc.word.word_id,
                                "current_label": cw.current_label,
                                "word_text": wc.word.text,
                            },
                            "llm_review": {
                                "decision": "NEEDS_REVIEW",
                                "reasoning": f"LLM call failed after {max_retries} attempts: {e}",
                                "suggested_label": None,
                                "confidence": "low",
                            },
                        }
                    )
                return results

    # Use the last decisions we got (best effort)
    decisions = last_decisions or [
        {
            "decision": "NEEDS_REVIEW",
            "reasoning": "No response received",
            "suggested_label": None,
            "confidence": "low",
        }
        for _ in currency_words
    ]

    # Step 5: Format output for apply_llm_decisions
    results = []
    num_words = len(currency_words)
    num_decisions = len(decisions)
    if num_decisions != num_words:
        logger.warning(
            "Decision count mismatch: %d words, %d decisions",
            num_words,
            num_decisions,
        )
        while len(decisions) < num_words:
            decisions.append(
                {
                    "decision": "NEEDS_REVIEW",
                    "reasoning": "No decision from LLM (count mismatch)",
                    "suggested_label": None,
                    "confidence": "low",
                }
            )
        decisions = decisions[:num_words]

    for cw, decision in zip(currency_words, decisions, strict=False):
        wc = cw.word_context
        results.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "issue": {
                    "line_id": wc.word.line_id,
                    "word_id": wc.word.word_id,
                    "current_label": cw.current_label,
                    "word_text": wc.word.text,
                },
                "llm_review": decision,
            }
        )

    # Log summary
    decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
    for r in results:
        decision_counts[r["llm_review"]["decision"]] += 1
    logger.info("Currency evaluation results: %s", decision_counts)

    return results


# =============================================================================
# Sync wrapper
# =============================================================================


def evaluate_currency_labels_sync(
    visual_lines: list[VisualLine],
    patterns: Optional[dict],
    llm: BaseChatModel,
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
) -> list[dict]:
    """Synchronous wrapper for evaluate_currency_labels."""
    return evaluate_currency_labels(
        visual_lines=visual_lines,
        patterns=patterns,
        llm=llm,
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name=merchant_name,
    )


# =============================================================================
# Legacy compatibility - convert to EvaluationIssue for graph integration
# =============================================================================


def convert_to_evaluation_issues(
    currency_decisions: list[dict],
) -> list[EvaluationIssue]:
    """
    Convert currency decisions to EvaluationIssue objects for graph integration.

    This is for backward compatibility with the existing graph.py integration.
    Only returns issues that are INVALID or NEEDS_REVIEW.
    """
    from receipt_dynamo.entities import ReceiptWord

    issues = []
    for decision in currency_decisions:
        llm_review = decision.get("llm_review", {})
        if llm_review.get("decision") == "VALID":
            continue  # Skip valid labels

        issue_data = decision.get("issue", {})

        # Create a minimal ReceiptWord for the issue
        # Note: This is a simplified version - in real usage, pass the actual word
        word = ReceiptWord(
            image_id=decision.get("image_id", ""),
            receipt_id=decision.get("receipt_id", 0),
            line_id=issue_data.get("line_id", 0),
            word_id=issue_data.get("word_id", 0),
            text=issue_data.get("word_text", ""),
            bounding_box={"x": 0, "y": 0, "width": 0, "height": 0},
            top_left={"x": 0, "y": 0},
            top_right={"x": 0, "y": 0},
            bottom_left={"x": 0, "y": 0},
            bottom_right={"x": 0, "y": 0},
            angle_degrees=0,
            angle_radians=0,
            confidence=0,
        )

        issues.append(
            EvaluationIssue(
                issue_type="currency_label_issue",
                word=word,
                current_label=issue_data.get("current_label"),
                suggested_status=llm_review.get("decision", "NEEDS_REVIEW"),
                reasoning=llm_review.get("reasoning", ""),
                suggested_label=llm_review.get("suggested_label"),
            )
        )

    return issues

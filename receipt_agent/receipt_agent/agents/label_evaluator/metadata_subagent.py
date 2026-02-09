"""
Metadata validation subagent for receipt label evaluation.

This evaluates metadata labels (MERCHANT_NAME, ADDRESS_LINE, PHONE_NUMBER, etc.)
using ReceiptPlace data from Google Places API and LLM pattern validation.

Input:
    - visual_lines: Words grouped by y-coordinate with current labels
    - place: ReceiptPlace record from DynamoDB (Google Places data)
    - image_id, receipt_id: For output format

Output:
    List of decisions ready for apply_llm_decisions():
    [
        {
            "image_id": "...",
            "receipt_id": 1,
            "issue": {"line_id": 5, "word_id": 3, "current_label": "MERCHANT_NAME"},
            "llm_review": {
                "decision": "VALID" | "INVALID" | "NEEDS_REVIEW",
                "reasoning": "...",
                "suggested_label": "MERCHANT_NAME" or None,
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
import string
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langsmith import traceable
from pydantic import ValidationError

from receipt_agent.constants import METADATA_EVALUATION_LABELS
from receipt_agent.prompts.structured_outputs import (
    MetadataEvaluationResponse,
    extract_json_from_response,
)

from .state import VisualLine, WordContext

logger = logging.getLogger(__name__)

# Maximum receipt lines to include in LLM prompt for context
MAX_RECEIPT_LINES_FOR_PROMPT = 30

# Labels validated against ReceiptPlace data
PLACE_VALIDATED_LABELS = {
    "MERCHANT_NAME",
    "ADDRESS_LINE",
    "PHONE_NUMBER",
    "WEBSITE",
    "STORE_HOURS",
}

# Labels validated by format patterns
FORMAT_VALIDATED_LABELS = {
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "LOYALTY_ID",
}

# Patterns for detecting unlabeled metadata
PHONE_PATTERN = re.compile(r"^\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$")
DATE_PATTERNS = [
    re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$"),  # 12/25/2024
    re.compile(r"^\d{4}[/.-]\d{1,2}[/.-]\d{1,2}$"),  # 2024-12-25
    re.compile(
        r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}$",
        re.I,
    ),
]
TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?\s*([AP]M)?$", re.I)
WEBSITE_PATTERN = re.compile(
    r"^(www\.)?\w+\.(com|org|net|edu|gov|io|co|biz|info|store|shop|app|dev|ai)$",
    re.I,
)
PAYMENT_PATTERNS = [
    re.compile(r"^(VISA|MASTERCARD|AMEX|DISCOVER|DEBIT|CREDIT)\b", re.I),
    re.compile(r"^(CASH|CHECK|EBT|SNAP)\b", re.I),
    re.compile(r"^[*•]+\d{4}$"),  # ••••1234
]

# Common stop words that are never metadata
# Note: Single characters are handled separately to preserve state abbreviations (CA, TX)
METADATA_STOP_WORDS = frozenset(
    {
        "for",
        "to",
        "and",
        "or",
        "the",
        "a",
        "an",
        "is",
        "by",
        "of",
        "in",
        "on",
        "at",
        "we",
        "it",
    }
)


def should_skip_for_metadata_evaluation(word_text: str) -> bool:
    """
    Check if a token should be skipped for metadata evaluation.

    Returns True for tokens that are obviously not metadata:
    - Single character tokens (length == 1)
    - Pure punctuation (all chars are in string.punctuation)
    - Common stop words

    Note: Two-character tokens are NOT filtered because they could be
    legitimate metadata like state abbreviations (CA, TX, NY, etc.)

    Args:
        word_text: The text of the word to check

    Returns:
        True if the word should be skipped, False otherwise
    """
    # Single character tokens (T, F, E, A, S, etc. - tax/food stamp indicators)
    if len(word_text) == 1:
        return True

    # Pure punctuation (-, /, ., =, etc.)
    if all(c in string.punctuation for c in word_text):
        return True

    # Common stop words (case-insensitive)
    return word_text.lower() in METADATA_STOP_WORDS


@dataclass
class MetadataWord:
    """A word that is metadata-related (labeled or should be labeled)."""

    word_context: WordContext
    current_label: str | None
    line_index: int
    detected_type: str | None  # What pattern it matches (if unlabeled)
    place_match: str | None  # What ReceiptPlace field it might match


# =============================================================================
# Helper Functions
# =============================================================================


def normalize_phone(phone: str) -> str:
    """Normalize phone number for comparison."""
    return re.sub(r"[^\d]", "", phone)


def normalize_text(text: str) -> str:
    """Normalize text for fuzzy matching."""
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def check_merchant_match(word_text: str, merchant_name: str) -> bool:
    """Check if word could be part of merchant name."""
    if not merchant_name:
        return False
    word_norm = normalize_text(word_text)
    merchant_norm = normalize_text(merchant_name)
    # Check if word is a significant part of merchant name
    return len(word_norm) >= 3 and word_norm in merchant_norm


def check_address_match(
    word_text: str, address: str, components: dict
) -> bool:
    """Check if word could be part of address."""
    if not address and not components:
        return False
    word_norm = normalize_text(word_text)
    if len(word_norm) < 2:
        return False
    # Check against full address
    if address and word_norm in normalize_text(address):
        return True
    # Check against components (street, city, state, zip)
    if components:
        for key in [
            "street_number",
            "route",
            "locality",
            "administrative_area_level_1",
            "postal_code",
        ]:
            if key in components and word_norm in normalize_text(
                str(components[key])
            ):
                return True
    return False


def check_phone_match(word_text: str, phone: str, phone_intl: str) -> bool:
    """Check if word matches phone number."""
    if not phone and not phone_intl:
        return False
    word_digits = normalize_phone(word_text)
    if len(word_digits) < 7:
        return False
    place_digits = normalize_phone(phone or phone_intl or "")
    return word_digits in place_digits or place_digits in word_digits


def check_website_match(word_text: str, website: str) -> bool:
    """Check if word matches website."""
    if not website:
        return False
    word_norm = (
        word_text.lower()
        .replace("www.", "")
        .replace("http://", "")
        .replace("https://", "")
    )
    site_norm = (
        website.lower()
        .replace("www.", "")
        .replace("http://", "")
        .replace("https://", "")
    )
    # Check domain match
    return word_norm in site_norm or site_norm.startswith(
        word_norm.split(".")[0]
    )


def detect_pattern_type(text: str) -> str | None:
    """Detect what type of metadata pattern a text matches."""
    if PHONE_PATTERN.match(text):
        return "PHONE_NUMBER"
    for pattern in DATE_PATTERNS:
        if pattern.match(text):
            return "DATE"
    if TIME_PATTERN.match(text):
        return "TIME"
    if WEBSITE_PATTERN.match(text):
        return "WEBSITE"
    for pattern in PAYMENT_PATTERNS:
        if pattern.match(text):
            return "PAYMENT_METHOD"
    return None


def collect_metadata_words(
    visual_lines: list[VisualLine],
    place: Any | None = None,
) -> tuple[list[MetadataWord], int]:
    """
    Collect all words that need metadata evaluation.

    This includes:
    1. Words with metadata labels
    2. Unlabeled words that match metadata patterns or ReceiptPlace data

    Pre-filters obvious non-metadata tokens (single chars, punctuation, stop words)
    to save API costs.

    Returns:
        Tuple of (metadata_words, prefiltered_count)
    """
    metadata_words = []
    prefiltered_count = 0

    # Extract place data if available
    merchant_name = getattr(place, "merchant_name", "") if place else ""
    address = getattr(place, "formatted_address", "") if place else ""
    address_components = (
        getattr(place, "address_components", {}) if place else {}
    )
    phone = getattr(place, "phone_number", "") if place else ""
    phone_intl = getattr(place, "phone_intl", "") if place else ""
    website = getattr(place, "website", "") if place else ""

    for line in visual_lines:
        for wc in line.words:
            current_label = (
                wc.current_label.label if wc.current_label else None
            )
            text = wc.word.text

            # Check if word has a metadata label
            has_metadata_label = current_label in METADATA_EVALUATION_LABELS

            # Pre-filter: Skip obvious non-metadata tokens for unlabeled words
            # Words with metadata labels are always evaluated (to catch mislabeling)
            if not has_metadata_label and should_skip_for_metadata_evaluation(
                text
            ):
                prefiltered_count += 1
                continue

            # Check for pattern matches (for unlabeled words only)
            detected_type = None
            if not has_metadata_label:
                detected_type = detect_pattern_type(text)

            # Always check against ReceiptPlace data (provides context for LLM)
            place_match = None
            if check_merchant_match(text, merchant_name):
                place_match = "MERCHANT_NAME"
            elif check_address_match(text, address, address_components):
                place_match = "ADDRESS_LINE"
            elif check_phone_match(text, phone, phone_intl):
                place_match = "PHONE_NUMBER"
            elif check_website_match(text, website):
                place_match = "WEBSITE"

            # Include if: has metadata label OR matches pattern OR matches place data
            if has_metadata_label or detected_type or place_match:
                metadata_words.append(
                    MetadataWord(
                        word_context=wc,
                        current_label=current_label,
                        line_index=line.line_index,
                        detected_type=detected_type,
                        place_match=place_match,
                    )
                )

    return metadata_words, prefiltered_count


def auto_resolve_metadata_words(
    metadata_words: list[MetadataWord],
) -> tuple[list[tuple[MetadataWord, dict]], list[MetadataWord]]:
    """
    Auto-resolve metadata words where the current label agrees with an
    independent deterministic signal (regex pattern or Google Places match).

    Words are auto-VALID when current_label matches either:
    - place_match (Google Places data confirms the label)
    - detected_type (regex pattern confirms the label)

    All other words (no label, no confirming signal, conflicts, STORE_HOURS,
    COUPON, LOYALTY_ID) are returned as unresolved for LLM evaluation.

    Returns:
        (resolved_pairs, unresolved_words) where resolved_pairs is a list of
        (MetadataWord, decision_dict) tuples.
    """
    resolved = []
    unresolved = []

    for mw in metadata_words:
        label = mw.current_label
        if not label:
            unresolved.append(mw)
            continue

        confirmed_by = None
        if label == mw.place_match:
            confirmed_by = "Google Places match"
        elif label == mw.detected_type:
            confirmed_by = "format pattern match"

        if confirmed_by:
            resolved.append(
                (
                    mw,
                    {
                        "decision": "VALID",
                        "reasoning": (
                            f"Label {label} confirmed by {confirmed_by}"
                        ),
                        "suggested_label": None,
                        "confidence": "high",
                    },
                )
            )
        else:
            unresolved.append(mw)

    return resolved, unresolved


# =============================================================================
# LLM Prompt Building
# =============================================================================


def build_metadata_evaluation_prompt(
    visual_lines: list[VisualLine],
    metadata_words: list[MetadataWord],
    place: Any | None = None,
    merchant_name: str = "Unknown",
) -> str:
    """
    Build the LLM prompt for metadata label evaluation.

    Shows the receipt structure, ReceiptPlace data, and asks the LLM to evaluate.
    """
    # Build receipt text representation (first N lines for context)
    receipt_lines = []
    for line in visual_lines[:MAX_RECEIPT_LINES_FOR_PROMPT]:
        line_text = []
        for wc in line.words:
            label = wc.current_label.label if wc.current_label else "unlabeled"
            line_text.append(f"{wc.word.text}[{label}]")
        receipt_lines.append(
            f"  Line {line.line_index}: " + " | ".join(line_text)
        )

    receipt_text = "\n".join(receipt_lines)
    if len(visual_lines) > MAX_RECEIPT_LINES_FOR_PROMPT:
        receipt_text += (
            f"\n  ... ({len(visual_lines) - MAX_RECEIPT_LINES_FOR_PROMPT} "
            "more lines)"
        )

    # Build ReceiptPlace context
    place_context = ""
    if place:
        place_data = {
            "merchant_name": getattr(place, "merchant_name", ""),
            "formatted_address": getattr(place, "formatted_address", ""),
            "phone_number": getattr(place, "phone_number", ""),
            "website": getattr(place, "website", ""),
            "hours_summary": getattr(place, "hours_summary", []),
        }
        place_context = f"""
## Google Places Data (Ground Truth)
```json
{json.dumps(place_data, indent=2)}
```
"""
    else:
        place_context = """
## Google Places Data
No Google Places data is available for this merchant. Evaluate labels based on
format patterns and receipt context only. Be more conservative — prefer
NEEDS_REVIEW over INVALID when uncertain about metadata labels.
"""

    # Build metadata words table
    words_table = []
    for i, mw in enumerate(metadata_words):
        wc = mw.word_context
        notes = []
        if mw.detected_type:
            notes.append(f"Pattern: {mw.detected_type}")
        if mw.place_match:
            notes.append(f"Matches: {mw.place_match}")
        notes_str = f" ({', '.join(notes)})" if notes else ""

        words_table.append(
            f"  [{i}] Line {mw.line_index}\n"
            f'      Text: "{wc.word.text}"\n'
            f"      Current Label: {mw.current_label or 'unlabeled'}{notes_str}"
        )

    words_text = "\n".join(words_table)

    prompt = f"""# Metadata Label Evaluation for {merchant_name}

You are evaluating metadata labels on a receipt. For each word below,
decide if the current label is VALID, INVALID, or NEEDS_REVIEW.

## Receipt Structure
{receipt_text}
{place_context}
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
    "suggested_label": "MERCHANT_NAME" | null,
    "confidence": "high" | "medium" | "low"
  }},
  ...
]
```

## Label Types
- MERCHANT_NAME: Store name/brand (compare against Google Places merchant_name)
- ADDRESS_LINE: Street address, city, state, zip (compare against formatted_address)
- PHONE_NUMBER: Store phone (compare against phone_number from Places)
- WEBSITE: Store website/email (compare against website from Places)
- STORE_HOURS: Business hours (e.g., "Mon-Fri 9-5", "Open 24 Hours")
- DATE: Transaction date (e.g., "12/25/2024", "Dec 25, 2024")
- TIME: Transaction time (e.g., "14:30", "2:30 PM")
- PAYMENT_METHOD: Payment type (e.g., "VISA ••••1234", "CASH", "DEBIT")
- COUPON: Coupon code or description
- LOYALTY_ID: Customer loyalty/rewards ID

## Validation Rules
1. For MERCHANT_NAME, ADDRESS_LINE, PHONE_NUMBER, WEBSITE: Compare against Google Places data
2. For DATE, TIME, PAYMENT_METHOD: Validate format patterns
3. For COUPON, LOYALTY_ID: Use context clues

- VALID: The label correctly describes this word
- INVALID: The label is wrong OR unlabeled word should have a metadata label
- NEEDS_REVIEW: You're unsure

For INVALID words, suggest the correct label.

Respond ONLY with the JSON array, no other text.
"""
    return prompt


def parse_metadata_evaluation_response(
    response_text: str,
    num_words: int,
) -> list[dict]:
    """Parse the LLM response into a list of decisions.

    First attempts to parse using the MetadataEvaluationResponse Pydantic model,
    which validates the schema and constrains suggested_label to valid metadata labels.
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
        structured_response = MetadataEvaluationResponse.model_validate(parsed)
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


def evaluate_metadata_labels(
    visual_lines: list[VisualLine],
    place: Any | None,
    llm: BaseChatModel,
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
) -> list[dict]:
    """
    Evaluate metadata labels on a receipt.

    This is the main entry point for the metadata evaluation step.

    Args:
        visual_lines: Visual lines from the receipt (words with labels)
        place: ReceiptPlace record from DynamoDB (Google Places data)
        llm: Language model for evaluation
        image_id: Image ID for output format
        receipt_id: Receipt ID for output format
        merchant_name: Merchant name for context

    Returns:
        List of decisions ready for apply_llm_decisions()
    """
    # Step 1: Collect metadata words to evaluate (with pre-filtering)
    metadata_words, prefiltered_count = collect_metadata_words(
        visual_lines, place
    )

    # Log pre-filtering results
    if prefiltered_count > 0:
        logger.debug("Pre-filtered %d non-metadata tokens", prefiltered_count)

    logger.info("Found %d metadata words to evaluate", len(metadata_words))

    if not metadata_words:
        logger.info("No metadata words found to evaluate")
        return []

    # Step 1.5: Auto-resolve words where label agrees with deterministic signal
    resolved_pairs, remaining_words = auto_resolve_metadata_words(
        metadata_words
    )
    if resolved_pairs:
        logger.info(
            "Auto-resolved %d/%d metadata words without LLM",
            len(resolved_pairs),
            len(metadata_words),
        )

    # Format auto-resolved results
    auto_results = []
    for mw, decision in resolved_pairs:
        wc = mw.word_context
        auto_results.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "issue": {
                    "line_id": wc.word.line_id,
                    "word_id": wc.word.word_id,
                    "current_label": mw.current_label,
                    "word_text": wc.word.text,
                },
                "llm_review": decision,
            }
        )

    # If all words resolved, skip LLM entirely
    if not remaining_words:
        logger.info("All metadata words auto-resolved, skipping LLM call")
        return auto_results

    # Step 2: Build prompt and call LLM (only for unresolved words)
    prompt = build_metadata_evaluation_prompt(
        visual_lines=visual_lines,
        metadata_words=remaining_words,
        place=place,
        merchant_name=merchant_name,
    )

    # Try structured output, then fall back to text parsing
    structured_retries = 1
    text_retries = 1
    num_words = len(remaining_words)
    decisions = None

    # Check if LLM supports structured output
    use_structured = hasattr(llm, "with_structured_output")

    # Phase 1: Try structured output multiple times
    if use_structured:
        for attempt in range(structured_retries):
            try:
                structured_llm = llm.with_structured_output(
                    MetadataEvaluationResponse
                )
                response: MetadataEvaluationResponse = structured_llm.invoke(
                    prompt
                )
                decisions = response.to_ordered_list(num_words)
                logger.debug(
                    "Structured output succeeded with %d evaluations",
                    len(decisions),
                )
                break  # Success, exit retry loop
            except Exception as struct_err:
                # Check if this is a rate limit error that should propagate
                from receipt_agent.utils import LLMRateLimitError

                if isinstance(struct_err, LLMRateLimitError):
                    logger.error(
                        "Metadata LLM rate limited, propagating for retry: %s",
                        struct_err,
                    )
                    raise  # Let Step Function retry handle this

                logger.warning(
                    "Structured output failed (attempt %d/%d): %s",
                    attempt + 1,
                    structured_retries,
                    struct_err,
                )
                if attempt == structured_retries - 1:
                    logger.info(
                        "All %d structured output attempts failed, "
                        "falling back to text parsing",
                        structured_retries,
                    )

    # Phase 2: Fall back to text parsing if structured output failed or unavailable
    if decisions is None:
        for attempt in range(text_retries):
            try:
                response = llm.invoke(prompt)
                response_text = (
                    response.content
                    if hasattr(response, "content")
                    else str(response)
                )
                decisions = parse_metadata_evaluation_response(
                    response_text, num_words
                )

                # Check if all decisions failed to parse
                parse_failures = sum(
                    1
                    for d in decisions
                    if "Failed to parse" in d.get("reasoning", "")
                )

                if parse_failures == 0:
                    logger.debug(
                        "Text parsing succeeded with %d evaluations",
                        len(decisions),
                    )
                    break
                elif parse_failures < len(decisions):
                    logger.info(
                        "Partial text parse success: %d/%d parsed",
                        len(decisions) - parse_failures,
                        len(decisions),
                    )
                    break
                else:
                    if attempt < text_retries - 1:
                        logger.warning(
                            "All %d decisions failed text parse "
                            "(attempt %d/%d), retrying...",
                            len(decisions),
                            attempt + 1,
                            text_retries,
                        )
                    else:
                        logger.warning(
                            "All %d decisions failed text parse "
                            "after %d attempts",
                            len(decisions),
                            text_retries,
                        )
            except Exception as e:
                logger.error(
                    "Text parsing failed on attempt %d: %s", attempt + 1, e
                )
                if attempt == text_retries - 1:
                    decisions = None

    # Use the decisions we got (best effort)
    if decisions is None:
        decisions = [
            {
                "decision": "NEEDS_REVIEW",
                "reasoning": "No response received",
                "suggested_label": None,
                "confidence": "low",
            }
            for _ in metadata_words
        ]

    # Step 4: Format output for apply_llm_decisions
    llm_results = []
    for mw, decision in zip(remaining_words, decisions, strict=True):
        wc = mw.word_context
        llm_results.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "issue": {
                    "line_id": wc.word.line_id,
                    "word_id": wc.word.word_id,
                    "current_label": mw.current_label,
                    "word_text": wc.word.text,
                },
                "llm_review": decision,
            }
        )

    # Combine auto-resolved + LLM results
    all_results = auto_results + llm_results

    # Log summary
    decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
    for r in all_results:
        decision = r.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
        if decision in decision_counts:
            decision_counts[decision] += 1
        else:
            decision_counts["NEEDS_REVIEW"] += 1
    logger.info("Metadata evaluation results: %s", decision_counts)

    return all_results


# =============================================================================
# Async version
# =============================================================================


@traceable(name="metadata_evaluation", run_type="chain")
async def evaluate_metadata_labels_async(
    visual_lines: list[VisualLine],
    place: Any | None,
    llm: Any,  # RateLimitedLLMInvoker or BaseChatModel with ainvoke
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
) -> list[dict]:
    """
    Async version of evaluate_metadata_labels.

    Uses ainvoke() for concurrent LLM calls. Works with RateLimitedLLMInvoker
    or any LLM that supports ainvoke().

    Decorated with @traceable so LLM calls auto-nest under this span in
    LangSmith when called inside a tracing_context(parent=root).

    Args:
        visual_lines: Visual lines from the receipt (words with labels)
        place: ReceiptPlace record from DynamoDB (Google Places data)
        llm: Language model invoker (RateLimitedLLMInvoker or BaseChatModel)
        image_id: Image ID for output format
        receipt_id: Receipt ID for output format
        merchant_name: Merchant name for context

    Returns:
        List of decisions ready for apply_llm_decisions()
    """

    # Step 1: Collect metadata words to evaluate
    metadata_words, prefiltered_count = collect_metadata_words(
        visual_lines, place
    )

    if prefiltered_count > 0:
        logger.debug("Pre-filtered %d non-metadata tokens", prefiltered_count)

    logger.info("Found %d metadata words to evaluate", len(metadata_words))

    if not metadata_words:
        logger.info("No metadata words found to evaluate")
        return []

    # Step 1.5: Auto-resolve words where label agrees with deterministic signal
    resolved_pairs, remaining_words = auto_resolve_metadata_words(
        metadata_words
    )
    if resolved_pairs:
        logger.info(
            "Auto-resolved %d/%d metadata words without LLM",
            len(resolved_pairs),
            len(metadata_words),
        )

    # Format auto-resolved results
    auto_results = []
    for mw, decision in resolved_pairs:
        wc = mw.word_context
        auto_results.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "issue": {
                    "line_id": wc.word.line_id,
                    "word_id": wc.word.word_id,
                    "current_label": mw.current_label,
                    "word_text": wc.word.text,
                },
                "llm_review": decision,
            }
        )

    # If all words resolved, skip LLM entirely
    if not remaining_words:
        logger.info("All metadata words auto-resolved, skipping LLM call")
        return auto_results

    # Step 2: Build prompt (only for unresolved words)
    prompt = build_metadata_evaluation_prompt(
        visual_lines=visual_lines,
        metadata_words=remaining_words,
        place=place,
        merchant_name=merchant_name,
    )

    # Step 3: Call LLM asynchronously
    max_retries = 2
    last_decisions = None
    num_words = len(remaining_words)

    use_structured = hasattr(llm, "with_structured_output")

    for attempt in range(max_retries):
        try:
            if use_structured:
                try:
                    structured_llm = llm.with_structured_output(
                        MetadataEvaluationResponse
                    )
                    if hasattr(structured_llm, "ainvoke"):
                        response: MetadataEvaluationResponse = (
                            await structured_llm.ainvoke(prompt)
                        )
                    else:
                        # Run sync invoke in thread pool to avoid blocking event loop
                        response: MetadataEvaluationResponse = (
                            await asyncio.to_thread(
                                structured_llm.invoke,
                                prompt,
                            )
                        )
                    decisions = response.to_ordered_list(num_words)
                    logger.debug(
                        "Structured output succeeded with %d evaluations",
                        len(decisions),
                    )
                except Exception as struct_err:
                    logger.warning(
                        "Structured output failed (attempt %d), falling back to text: %s",
                        attempt + 1,
                        struct_err,
                    )
                    if hasattr(llm, "ainvoke"):
                        response = await llm.ainvoke(prompt)
                    else:
                        # Run sync invoke in thread pool to avoid blocking event loop
                        response = await asyncio.to_thread(
                            llm.invoke, prompt,
                        )
                    response_text = (
                        response.content
                        if hasattr(response, "content")
                        else str(response)
                    )
                    decisions = parse_metadata_evaluation_response(
                        response_text, num_words
                    )
            else:
                if hasattr(llm, "ainvoke"):
                    response = await llm.ainvoke(prompt)
                else:
                    # Run sync invoke in thread pool to avoid blocking event loop
                    response = await asyncio.to_thread(
                        llm.invoke, prompt,
                    )
                response_text = (
                    response.content
                    if hasattr(response, "content")
                    else str(response)
                )
                decisions = parse_metadata_evaluation_response(
                    response_text, num_words
                )

            last_decisions = decisions

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
            from receipt_agent.utils import LLMRateLimitError

            if isinstance(e, LLMRateLimitError):
                logger.error(
                    "Metadata LLM rate limited, propagating for retry: %s", e
                )
                raise

            logger.error(
                "Metadata LLM call failed on attempt %d: %s", attempt + 1, e
            )
            if attempt == max_retries - 1:
                llm_results = []
                for mw in remaining_words:
                    wc = mw.word_context
                    llm_results.append(
                        {
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                            "issue": {
                                "line_id": wc.word.line_id,
                                "word_id": wc.word.word_id,
                                "current_label": mw.current_label,
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
                return auto_results + llm_results

    decisions = last_decisions or [
        {
            "decision": "NEEDS_REVIEW",
            "reasoning": "No response received",
            "suggested_label": None,
            "confidence": "low",
        }
        for _ in remaining_words
    ]

    # Step 4: Format output
    llm_results = []
    for mw, decision in zip(remaining_words, decisions, strict=True):
        wc = mw.word_context
        llm_results.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "issue": {
                    "line_id": wc.word.line_id,
                    "word_id": wc.word.word_id,
                    "current_label": mw.current_label,
                    "word_text": wc.word.text,
                },
                "llm_review": decision,
            }
        )

    # Combine auto-resolved + LLM results
    all_results = auto_results + llm_results

    decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
    for r in all_results:
        decision = r.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
        if decision in decision_counts:
            decision_counts[decision] += 1
        else:
            decision_counts["NEEDS_REVIEW"] += 1
    logger.info("Metadata evaluation results: %s", decision_counts)

    return all_results

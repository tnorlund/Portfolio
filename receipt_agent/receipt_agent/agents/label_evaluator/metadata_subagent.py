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

import json
import logging
import re
import string
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from receipt_agent.constants import METADATA_EVALUATION_LABELS

from .state import VisualLine, WordContext

logger = logging.getLogger(__name__)

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
    re.compile(r"^\d{4}[/.-]\d{1,2}[/.-]\d{1,2}$"),    # 2024-12-25
    re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}$", re.I),
]
TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?\s*([AP]M)?$", re.I)
WEBSITE_PATTERN = re.compile(r"^(www\.)?\w+\.(com|org|net|edu|gov|io|co)$", re.I)
PAYMENT_PATTERNS = [
    re.compile(r"^(VISA|MASTERCARD|AMEX|DISCOVER|DEBIT|CREDIT)\b", re.I),
    re.compile(r"^(CASH|CHECK|EBT|SNAP)\b", re.I),
    re.compile(r"^[*•]+\d{4}$"),  # ••••1234
]

# Common stop words that are never metadata
# Note: Single characters are handled separately to preserve state abbreviations (CA, TX)
METADATA_STOP_WORDS = frozenset({
    "for", "to", "and", "or", "the", "a", "an", "is", "by", "of", "in", "on", "at", "we", "it"
})


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
    if word_text.lower() in METADATA_STOP_WORDS:
        return True

    return False


@dataclass
class MetadataWord:
    """A word that is metadata-related (labeled or should be labeled)."""

    word_context: WordContext
    current_label: Optional[str]
    line_index: int
    detected_type: Optional[str]  # What pattern it matches (if unlabeled)
    place_match: Optional[str]  # What ReceiptPlace field it might match


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


def check_address_match(word_text: str, address: str, components: dict) -> bool:
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
        for key in ["street_number", "route", "locality", "administrative_area_level_1", "postal_code"]:
            if key in components and word_norm in normalize_text(str(components[key])):
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
    word_norm = word_text.lower().replace("www.", "").replace("http://", "").replace("https://", "")
    site_norm = website.lower().replace("www.", "").replace("http://", "").replace("https://", "")
    # Check domain match
    return word_norm in site_norm or site_norm.startswith(word_norm.split(".")[0])


def detect_pattern_type(text: str) -> Optional[str]:
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
    place: Optional[Any] = None,
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
    address_components = getattr(place, "address_components", {}) if place else {}
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
            if not has_metadata_label and should_skip_for_metadata_evaluation(text):
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


# =============================================================================
# LLM Prompt Building
# =============================================================================


def build_metadata_evaluation_prompt(
    visual_lines: list[VisualLine],
    metadata_words: list[MetadataWord],
    place: Optional[Any] = None,
    merchant_name: str = "Unknown",
) -> str:
    """
    Build the LLM prompt for metadata label evaluation.

    Shows the receipt structure, ReceiptPlace data, and asks the LLM to evaluate.
    """
    # Build receipt text representation (first 30 lines for context)
    receipt_lines = []
    for line in visual_lines[:30]:
        line_text = []
        for wc in line.words:
            label = wc.current_label.label if wc.current_label else "unlabeled"
            line_text.append(f"{wc.word.text}[{label}]")
        receipt_lines.append(f"  Line {line.line_index}: " + " | ".join(line_text))

    receipt_text = "\n".join(receipt_lines)
    if len(visual_lines) > 30:
        receipt_text += f"\n  ... ({len(visual_lines) - 30} more lines)"

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
            f"      Text: \"{wc.word.text}\"\n"
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
    """Parse the LLM response into a list of decisions."""
    try:
        # Handle markdown code blocks
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()
        elif "```" in response_text:
            start = response_text.find("```") + 3
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()

        decisions = json.loads(response_text)

        # Validate and normalize
        result = []
        for i in range(num_words):
            decision = next(
                (d for d in decisions if d.get("index") == i),
                None
            )
            if decision:
                result.append({
                    "decision": decision.get("decision", "NEEDS_REVIEW"),
                    "reasoning": decision.get("reasoning", ""),
                    "suggested_label": decision.get("suggested_label"),
                    "confidence": decision.get("confidence", "medium"),
                })
            else:
                result.append({
                    "decision": "NEEDS_REVIEW",
                    "reasoning": "No decision from LLM",
                    "suggested_label": None,
                    "confidence": "low",
                })

        return result

    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Failed to parse LLM response: {e}")
        return [
            {
                "decision": "NEEDS_REVIEW",
                "reasoning": f"Failed to parse LLM response: {e}",
                "suggested_label": None,
                "confidence": "low",
            }
            for _ in range(num_words)
        ]


# =============================================================================
# Main Evaluation Function
# =============================================================================


def evaluate_metadata_labels(
    visual_lines: list[VisualLine],
    place: Optional[Any],
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
    metadata_words, prefiltered_count = collect_metadata_words(visual_lines, place)

    # Log pre-filtering results
    if prefiltered_count > 0:
        logger.debug("Pre-filtered %d non-metadata tokens", prefiltered_count)

    logger.info(f"Found {len(metadata_words)} metadata words to evaluate")

    if not metadata_words:
        logger.info("No metadata words found to evaluate")
        return []

    # Step 2: Build prompt and call LLM
    prompt = build_metadata_evaluation_prompt(
        visual_lines=visual_lines,
        metadata_words=metadata_words,
        place=place,
        merchant_name=merchant_name,
    )

    try:
        response = llm.invoke(prompt)
        response_text = response.content if hasattr(response, "content") else str(response)

        # Step 3: Parse response
        decisions = parse_metadata_evaluation_response(response_text, len(metadata_words))

        # Step 4: Format output for apply_llm_decisions
        results = []
        for mw, decision in zip(metadata_words, decisions, strict=True):
            wc = mw.word_context
            results.append({
                "image_id": image_id,
                "receipt_id": receipt_id,
                "issue": {
                    "line_id": wc.word.line_id,
                    "word_id": wc.word.word_id,
                    "current_label": mw.current_label,
                    "word_text": wc.word.text,
                },
                "llm_review": decision,
            })

        # Log summary
        decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
        for r in results:
            decision = r.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
            if decision in decision_counts:
                decision_counts[decision] += 1
            else:
                decision_counts["NEEDS_REVIEW"] += 1
        logger.info(f"Metadata evaluation results: {decision_counts}")

        return results

    except Exception as e:
        # Check if this is a rate limit error that should trigger Step Function retry
        from receipt_agent.utils import OllamaRateLimitError, BothProvidersFailedError
        if isinstance(e, (OllamaRateLimitError, BothProvidersFailedError)):
            logger.error(f"Metadata LLM rate limited, propagating for retry: {e}")
            raise  # Let Step Function retry handle this

        logger.error(f"Metadata LLM call failed: {e}")
        # Return NEEDS_REVIEW for all words (non-rate-limit errors only)
        results = []
        for mw in metadata_words:
            wc = mw.word_context
            results.append({
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
                    "reasoning": f"LLM call failed: {e}",
                    "suggested_label": None,
                    "confidence": "low",
                },
            })
        return results

"""
Metadata validation subagent for receipt label evaluation.

This evaluates metadata labels (MERCHANT_NAME, ADDRESS_LINE, PHONE_NUMBER, etc.)
using ReceiptPlace data from Google Places API and deterministic pattern validation.

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
import logging
import re
import string
from dataclasses import dataclass
from typing import Any

from langsmith import traceable

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
    re.compile(
        r"^(VISA|MASTERCARD|MAESTRO|AMEX|DISCOVER|DEBIT|CREDIT|"
        r"CARD|CASH|CHECK|EBT|SNAP|MC|M/C|GIFT|TAP|CONTACTLESS|SWIPE|CHIP)\b",
        re.I,
    ),
    re.compile(r"^(EFT|VISADEBIT|GPAY|EPAY)\b", re.I),
    re.compile(r"^[#]?[Xx*•.\[\]]{2,}\d{3,}$"),  # ****1234, XXXX1234, ...1234
]
STORE_HOURS_PATTERNS = [
    # Day names and ranges: MON, MON-FRI, TUE-SAT, DAILY, OPEN
    re.compile(
        r"^(MON|TUE|WED|THU|FRI|SAT|SUN|DAILY|OPEN)"
        r"(-?(MON|TUE|WED|THU|FRI|SAT|SUN))?:?$",
        re.I,
    ),
    # Short time without colon (not caught by TIME_PATTERN): 7AM, 10PM
    re.compile(r"^\d{1,2}\s*[AP]M$", re.I),
    # Time ranges (always store hours): 7AM-10PM, 9:00AM-5:00PM
    re.compile(
        r"^\d{1,2}(:\d{2})?\s*[AP]M\s*-\s*\d{1,2}(:\d{2})?\s*[AP]M$",
        re.I,
    ),
    # Keywords
    re.compile(r"^(Store|Hours)$", re.I),
]

# ---------------------------------------------------------------------------
# Tier-1 patterns for AUTO-ASSIGNING labels to unlabeled words.
# These patterns have >=90% precision when tested against LLM ground truth.
# They are ONLY used for unlabeled words — labeled words use the broader
# patterns above for confirmation.
# ---------------------------------------------------------------------------
TIER1_PAYMENT_PATTERNS = [
    # Masked card numbers: ****1234, XXXX1234, ...1234, ••••1234
    re.compile(r"^[#]?[Xx*•.\[\]]{2,}\d{3,}$"),
    # Card brand names (unambiguous payment indicators)
    re.compile(r"^(VISA|MASTERCARD|MAESTRO|AMEX|DISCOVER|DINERS)\b", re.I),
    # Compound payment terms
    re.compile(r"^(EFT|VISADEBIT|GPAY|EPAY)\b", re.I),
]
TIER1_STORE_HOURS_PATTERNS = [
    # Day RANGES (hyphen required — single day names are ambiguous)
    re.compile(
        r"^(MON|TUE|WED|THU|FRI|SAT|SUN)"
        r"-(MON|TUE|WED|THU|FRI|SAT|SUN):?$",
        re.I,
    ),
    # Time ranges: 7AM-10PM, 9:00AM-5:00PM
    re.compile(
        r"^\d{1,2}(:\d{2})?\s*[AP]M\s*-\s*\d{1,2}(:\d{2})?\s*[AP]M$",
        re.I,
    ),
    # DAILY is unambiguous
    re.compile(r"^DAILY$", re.I),
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


def detect_tier1_label(text: str) -> str | None:
    """Return a label if *text* matches a Tier-1 high-confidence pattern.

    Tier-1 patterns have >=90% precision for assigning labels to previously
    unlabeled words (validated against LLM ground truth on 721 receipts).
    """
    for pattern in TIER1_PAYMENT_PATTERNS:
        if pattern.match(text):
            return "PAYMENT_METHOD"
    for pattern in TIER1_STORE_HOURS_PATTERNS:
        if pattern.match(text):
            return "STORE_HOURS"
    return None


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
    for pattern in STORE_HOURS_PATTERNS:
        if pattern.match(text):
            return "STORE_HOURS"
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

            # Check for pattern matches (all words, enables auto-resolve
            # for labeled words whose format confirms the label)
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

    Unlabeled words are auto-INVALID (assigned a label) when they match a
    Tier-1 high-confidence pattern (>=90% precision vs LLM ground truth):
    - Masked card numbers, brand names → PAYMENT_METHOD
    - Day ranges, time ranges, DAILY → STORE_HOURS

    All other words (no confirming signal, conflicts, COUPON, LOYALTY_ID)
    are returned as unresolved for LLM evaluation.

    Returns:
        (resolved_pairs, unresolved_words) where resolved_pairs is a list of
        (MetadataWord, decision_dict) tuples.
    """
    resolved = []
    unresolved = []

    for mw in metadata_words:
        label = mw.current_label
        if not label:
            # Try Tier-1 high-confidence auto-assignment for unlabeled words
            tier1_label = detect_tier1_label(mw.word_context.word.text)
            if tier1_label:
                resolved.append(
                    (
                        mw,
                        {
                            "decision": "INVALID",
                            "reasoning": (
                                f"Unlabeled word auto-assigned {tier1_label} "
                                "by Tier-1 pattern match"
                            ),
                            "suggested_label": tier1_label,
                            "confidence": "high",
                        },
                    )
                )
            else:
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
# Main Evaluation Function
# =============================================================================


def evaluate_metadata_labels(
    visual_lines: list[VisualLine],
    place: Any | None,
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
    chroma_client: Any | None = None,
) -> list[dict]:
    """
    Evaluate metadata labels on a receipt.

    Uses a two-tier deterministic pipeline:
    - Tier 1: Regex patterns and Google Places matching (auto-resolve)
    - Tier 2: ChromaDB consensus (for remaining words)

    Unresolved words after both tiers are not included in results
    (they retain their current labels unchanged).

    Args:
        visual_lines: Visual lines from the receipt (words with labels)
        place: ReceiptPlace record from DynamoDB (Google Places data)
        image_id: Image ID for output format
        receipt_id: Receipt ID for output format
        merchant_name: Merchant name for context
        chroma_client: Optional ChromaDB client for consensus pre-check

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
            "Auto-resolved %d/%d metadata words (Tier 1)",
            len(resolved_pairs),
            len(metadata_words),
        )

    # Format auto-resolved results
    auto_results: list[dict[str, Any]] = []
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

    # If all words resolved, done
    if not remaining_words:
        logger.info("All metadata words auto-resolved (Tier 1)")
        return auto_results

    # Step 1.7: ChromaDB consensus auto-resolve for remaining words
    if chroma_client and remaining_words:
        from receipt_agent.utils.chroma_helpers import chroma_resolve_words

        chroma_word_dicts = [
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "line_id": mw.word_context.word.line_id,
                "word_id": mw.word_context.word.word_id,
                "current_label": mw.current_label,
                "word_text": mw.word_context.word.text,
            }
            for mw in remaining_words
        ]
        chroma_resolved, chroma_unresolved_dicts = chroma_resolve_words(
            chroma_client=chroma_client,
            words=chroma_word_dicts,
            merchant_name=merchant_name,
        )
        if chroma_resolved:
            for word_dict, decision in chroma_resolved:
                auto_results.append({
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "issue": {
                        "line_id": word_dict["line_id"],
                        "word_id": word_dict["word_id"],
                        "current_label": word_dict["current_label"],
                        "word_text": word_dict["word_text"],
                    },
                    "llm_review": decision,
                })
            chroma_unresolved_ids = {
                (d["line_id"], d["word_id"]) for d in chroma_unresolved_dicts
            }
            remaining_words = [
                mw for mw in remaining_words
                if (mw.word_context.word.line_id, mw.word_context.word.word_id)
                in chroma_unresolved_ids
            ]
            logger.info(
                "ChromaDB auto-resolved %d/%d metadata words (Tier 2)",
                len(chroma_resolved),
                len(chroma_resolved) + len(remaining_words),
            )

    if remaining_words:
        logger.info(
            "%d metadata words unresolved after Tier 1+2 (kept as-is)",
            len(remaining_words),
        )

    # Log summary
    decision_counts = {"VALID": 0, "INVALID": 0}
    for r in auto_results:
        decision = r.get("llm_review", {}).get("decision", "VALID")
        if decision in decision_counts:
            decision_counts[decision] += 1
    logger.info("Metadata evaluation results: %s", decision_counts)

    return auto_results


# =============================================================================
# Async version
# =============================================================================


@traceable(name="metadata_evaluation", run_type="chain")
async def evaluate_metadata_labels_async(
    visual_lines: list[VisualLine],
    place: Any | None,
    image_id: str,
    receipt_id: int,
    merchant_name: str = "Unknown",
    chroma_client: Any | None = None,
) -> list[dict]:
    """
    Async version of evaluate_metadata_labels.

    Uses a two-tier deterministic pipeline:
    - Tier 1: Regex patterns and Google Places matching (auto-resolve)
    - Tier 2: ChromaDB consensus (for remaining words)

    Unresolved words after both tiers are not included in results
    (they retain their current labels unchanged).

    Decorated with @traceable so calls auto-nest under this span in
    LangSmith when called inside a tracing_context(parent=root).

    Args:
        visual_lines: Visual lines from the receipt (words with labels)
        place: ReceiptPlace record from DynamoDB (Google Places data)
        image_id: Image ID for output format
        receipt_id: Receipt ID for output format
        merchant_name: Merchant name for context
        chroma_client: Optional ChromaDB client for consensus pre-check

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
            "Auto-resolved %d/%d metadata words (Tier 1)",
            len(resolved_pairs),
            len(metadata_words),
        )

    # Format auto-resolved results
    auto_results: list[dict[str, Any]] = []
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

    # If all words resolved, done
    if not remaining_words:
        logger.info("All metadata words auto-resolved (Tier 1)")
        return auto_results

    # Step 1.7: ChromaDB consensus auto-resolve for remaining words
    if chroma_client and remaining_words:
        from receipt_agent.utils.chroma_helpers import chroma_resolve_words

        chroma_word_dicts = [
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "line_id": mw.word_context.word.line_id,
                "word_id": mw.word_context.word.word_id,
                "current_label": mw.current_label,
                "word_text": mw.word_context.word.text,
            }
            for mw in remaining_words
        ]
        chroma_resolved, chroma_unresolved_dicts = await asyncio.to_thread(
            chroma_resolve_words,
            chroma_client=chroma_client,
            words=chroma_word_dicts,
            merchant_name=merchant_name,
        )
        if chroma_resolved:
            for word_dict, decision in chroma_resolved:
                auto_results.append({
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "issue": {
                        "line_id": word_dict["line_id"],
                        "word_id": word_dict["word_id"],
                        "current_label": word_dict["current_label"],
                        "word_text": word_dict["word_text"],
                    },
                    "llm_review": decision,
                })
            chroma_unresolved_ids = {
                (d["line_id"], d["word_id"]) for d in chroma_unresolved_dicts
            }
            remaining_words = [
                mw for mw in remaining_words
                if (mw.word_context.word.line_id, mw.word_context.word.word_id)
                in chroma_unresolved_ids
            ]
            logger.info(
                "ChromaDB auto-resolved %d/%d metadata words (Tier 2)",
                len(chroma_resolved),
                len(chroma_resolved) + len(remaining_words),
            )

    if remaining_words:
        logger.info(
            "%d metadata words unresolved after Tier 1+2 (kept as-is)",
            len(remaining_words),
        )

    # Log summary
    decision_counts = {"VALID": 0, "INVALID": 0}
    for r in auto_results:
        decision = r.get("llm_review", {}).get("decision", "VALID")
        if decision in decision_counts:
            decision_counts[decision] += 1
    logger.info("Metadata evaluation results: %s", decision_counts)

    return auto_results

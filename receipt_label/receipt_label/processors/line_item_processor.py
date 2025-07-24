import json
import logging
import os
import re
import traceback
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Literal, Optional, Tuple, Union

from ..data.gpt import (
    gpt_request_line_item_analysis,
    gpt_request_spatial_currency_analysis,
)
from ..pattern_detection.enhanced_pattern_analyzer import enhanced_pattern_analysis
from ..models.line_item import LineItem, LineItemAnalysis, Price, Quantity
from ..models.receipt import Receipt, ReceiptLine, ReceiptWord
from ..models.uncertainty import (
    MissingComponentUncertainty,
    MultipleAmountsUncertainty,
    TotalMismatchUncertainty,
    UncertaintyItem,
)
from .llm_processor import LLMProcessor

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Define standardized label categories
ITEM_NAME_LABEL = "ITEM_NAME"
ITEM_QUANTITY_LABEL = "ITEM_QUANTITY"
ITEM_UNIT_LABEL = "ITEM_UNIT"
ITEM_PRICE_LABEL = "ITEM_PRICE"
ITEM_TOTAL_LABEL = "ITEM_TOTAL"
SUBTOTAL_LABEL = "SUBTOTAL"
TAX_LABEL = "TAX"
DISCOUNT_LABEL = "DISCOUNT"
TOTAL_LABEL = "TOTAL"
ITEM_MODIFIER_LABEL = "ITEM_MODIFIER"

# Enhanced patterns for financial summary fields
SUBTOTAL_PATTERNS = [
    r"(?i)sub\s*total",
    r"(?i)subtotal",
    r"(?i)sub\-total",
    r"(?i)sub total",
    r"(?i)net\s*total",
    r"(?i)net amount",
    r"(?i)amount before tax",
    r"(?i)merchandise",
]
TAX_PATTERNS = [
    r"(?i)tax(?!\s*exempt)",
    r"(?i)vat",
    r"(?i)gst",
    r"(?i)hst",
    r"(?i)sales tax",
    r"(?i)tax amount",
    r"(?i)state tax",
    r"(?i)local tax",
    r"(?i)city tax",
    r"(?i)t\s*a\s*x",
]
TOTAL_PATTERNS = [
    r"(?i)total\b(?!.*subtotal)",
    r"(?i)amount due",
    r"(?i)amount paid",
    r"(?i)grand total",
    r"(?i)balance due",
    r"(?i)balance",
    r"(?i)due\s*amount",
    r"(?i)amount\s*due",
    r"(?i)payment",
    r"(?i)pay total",
    r"(?i)order total",
    r"(?i)total payment",
    r"(?i)to pay",
    r"(?i)please pay",
    r"(?i)payment due",
    r"(?i)due",
    r"(?i)final amount",
    r"(?i)final total",
    r"(?i)total amount",
    r"(?i)total to pay",
    r"(?i)total due",
]
DISCOUNT_PATTERNS = [
    r"(?i)discount",
    r"(?i)coupon",
    r"(?i)sale",
    r"(?i)markdown",
    r"(?i)promo",
    r"(?i)savings",
    r"(?i)price adjustment",
    r"(?i)% off",
]


@dataclass
class CurrencyMatch:
    """Represents a matched currency amount in text."""

    amount: Decimal
    reasoning: str
    text: str
    position: Dict[str, int]  # x, y coordinates
    line_id: str
    word_id: Optional[int] = None  # Added word_id for more precise tracking


@dataclass
class ProcessingResult:
    """Holds the results of each processing stage."""

    line_items: List[LineItem]
    subtotal: Optional[Decimal]
    tax: Optional[Decimal]
    total: Optional[Decimal]
    reasoning: str = ""
    uncertain_items: List[UncertaintyItem] = None

    def __post_init__(self):
        if self.uncertain_items is None:
            self.uncertain_items = []


@dataclass
class CurrencyAmount:
    """Represents a currency amount with spatial context."""

    value: Decimal
    text: str
    line_id: str
    x_position: float
    y_position: float
    context: Dict[str, str]


class FastPatternMatcher:
    """Handles quick initial pattern matching for currency and line items."""

    def __init__(self):
        # Common currency patterns
        self.currency_patterns = [
            r"\$?\d+\.\d{2}\b",  # $XX.XX or XX.XX
            r"\$\d{1,3}(?:,\d{3})*\.\d{2}\b",  # $X,XXX.XX
            r"\b\d+\s*@\s*\$?\d+\.\d{2}\b",  # Quantity @ price pattern
        ]

    def find_currency_amounts(
        self,
        text: str,
        line_id: str,
        position: Dict[str, int],
        word_id: Optional[int] = None,
    ) -> List[CurrencyMatch]:
        """Extract currency amounts from text with position information."""
        matches = []
        for pattern in self.currency_patterns:
            for match in re.finditer(pattern, text):
                try:
                    # Clean the matched text
                    amount_str = (
                        match.group(0).replace("$", "").replace(",", "")
                    )
                    # Handle quantity @ price pattern
                    if "@" in amount_str:
                        qty, price = amount_str.split("@")
                        amount = Decimal(qty.strip()) * Decimal(price.strip())
                    else:
                        amount = Decimal(amount_str)

                    matches.append(
                        CurrencyMatch(
                            amount=amount,
                            reasoning="Exact match",
                            text=match.group(0),
                            position=position,
                            line_id=line_id,
                            word_id=word_id,
                        )
                    )
                except (ValueError, InvalidOperation):
                    logger.debug(
                        f"Failed to parse amount from {match.group(0)}"
                    )
                    continue

        return matches

    def process(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        structure_analysis=None,
    ) -> ProcessingResult:
        """Process receipt using pattern matching for quick initial analysis.

        Args:
            receipt: Receipt object containing metadata
            receipt_lines: List of receipt lines
            receipt_words: List of receipt words
            structure_analysis: Optional structure analysis for section-aware processing

        Returns:
            ProcessingResult containing initial analysis
        """
        currency_matches = []
        uncertain_items = []

        # SINGLE PASS: Find all currency amounts with precise tracking and spatial information
        for word_idx, word in enumerate(receipt_words):
            # Get bounding box data - fail if not available
            if not hasattr(word, "bounding_box"):
                raise ValueError(
                    f"Word missing required bounding_box attribute: {word}"
                )

            if isinstance(word.bounding_box, dict):
                if (
                    "x" not in word.bounding_box
                    or "y" not in word.bounding_box
                ):
                    raise ValueError(
                        f"Bounding box missing required x,y coordinates: {word.bounding_box}"
                    )
                position = word.bounding_box
            else:
                if not hasattr(word.bounding_box, "x") or not hasattr(
                    word.bounding_box, "y"
                ):
                    raise ValueError(
                        f"Bounding box object missing required x,y attributes: {word.bounding_box}"
                    )
                position = {"x": word.bounding_box.x, "y": word.bounding_box.y}

            # Get line_id - fail if not available
            if hasattr(word, "line_id"):
                line_id = word.line_id
            elif isinstance(word, dict) and "line_id" in word:
                line_id = word["line_id"]
            else:
                raise ValueError(f"Word missing required line_id: {word}")

            # Get word_id for more precise tracking
            word_id = None
            if hasattr(word, "word_id"):
                word_id = word.word_id
            elif isinstance(word, dict) and "word_id" in word:
                word_id = word["word_id"]

            # Get text - fail if not available
            if hasattr(word, "text"):
                text = word.text
            elif isinstance(word, dict) and "text" in word:
                text = word["text"]
            else:
                raise ValueError(f"Word missing required text: {word}")

            matches = self.find_currency_amounts(
                text, line_id, position, word_id
            )
            currency_matches.extend(matches)

        # Organize currency amounts by line ID
        amounts_by_line = {}
        for match in currency_matches:
            if match.line_id not in amounts_by_line:
                amounts_by_line[match.line_id] = []
            amounts_by_line[match.line_id].append(match)

        # Create a dictionary of line texts and y-positions for context
        line_texts = {}
        line_positions = {}
        for line in receipt_lines:
            line_id = (
                line.line_id
                if hasattr(line, "line_id")
                else line.get("line_id", "")
            )
            line_text = (
                line.text if hasattr(line, "text") else line.get("text", "")
            )
            line_texts[line_id] = line_text

            # Extract y-position from the first word of the line
            line_words = [w for w in receipt_words if w.line_id == line_id]
            if line_words:
                first_word = line_words[0]
                y_pos = (
                    first_word.bounding_box["y"]
                    if isinstance(first_word.bounding_box, dict)
                    else first_word.bounding_box.y
                )
                line_positions[line_id] = y_pos

        # Collect spatial contexts for all currency amounts for LLM prompt
        currency_contexts = []

        # Sort lines by vertical position for spatial analysis
        sorted_line_ids = sorted(
            line_positions.keys(), key=lambda line_id: line_positions[line_id]
        )

        # Process each currency amount to gather contextual information
        for line_id, matches in amounts_by_line.items():
            # Get the full line text
            line_text = line_texts.get(line_id, "")
            curr_y_pos = line_positions.get(line_id, 0)

            # Find spatially adjacent lines (above and below)
            prev_line_text = ""
            next_line_text = ""

            # Find the closest line above
            lines_above = [
                l_id
                for l_id in sorted_line_ids
                if line_positions.get(l_id, 0) < curr_y_pos
            ]
            if lines_above:
                # Get the closest line above by y-position
                closest_above = max(
                    lines_above, key=lambda l_id: line_positions.get(l_id, 0)
                )
                prev_line_text = line_texts.get(closest_above, "")

            # Find the closest line below
            lines_below = [
                l_id
                for l_id in sorted_line_ids
                if line_positions.get(l_id, 0) > curr_y_pos
            ]
            if lines_below:
                # Get the closest line below by y-position
                closest_below = min(
                    lines_below, key=lambda l_id: line_positions.get(l_id, 0)
                )
                next_line_text = line_texts.get(closest_below, "")

            # For each currency amount in this line
            for match in matches:
                # Get position information
                x_pos = (
                    match.position.get("x", 0)
                    if isinstance(match.position, dict)
                    else getattr(match.position, "x", 0)
                )

                # Get all words to the left of this currency amount on the same line
                words_to_left = [
                    w
                    for w in receipt_words
                    if w.line_id == line_id
                    and (
                        w.bounding_box["x"]
                        if isinstance(w.bounding_box, dict)
                        else w.bounding_box.x
                    )
                    < x_pos
                ]

                # Get the description to the left (all words joined)
                if words_to_left:
                    # Sort words by x-position to maintain correct reading order
                    sorted_left_words = sorted(
                        words_to_left,
                        key=lambda w: (
                            w.bounding_box["x"]
                            if isinstance(w.bounding_box, dict)
                            else w.bounding_box.x
                        ),
                    )

                    # Filter out any currency-like words or other noise that might appear on the left
                    filtered_left_words = []
                    for word in sorted_left_words:
                        word_text = word.text
                        # Skip currency symbols, numbers alone, or very short words
                        if (
                            word_text in ["$", "€", "£", "¥"]
                            or re.match(r"^\d+\.?\d*$", word_text)
                            or len(word_text) <= 1
                        ):
                            continue
                        filtered_left_words.append(word_text)

                    left_text = " ".join(filtered_left_words)
                else:
                    left_text = ""

                # If left text is empty, try to get context from the beginning of the line
                if not left_text:
                    # Get all words from the beginning of the line up to this currency
                    line_start_words = [
                        w.text
                        for w in sorted(
                            [w for w in receipt_words if w.line_id == line_id],
                            key=lambda w: (
                                w.bounding_box["x"]
                                if isinstance(w.bounding_box, dict)
                                else w.bounding_box.x
                            ),
                        )
                        if (
                            w.bounding_box["x"]
                            if isinstance(w.bounding_box, dict)
                            else w.bounding_box.x
                        )
                        < x_pos
                    ]
                    left_text = (
                        " ".join(line_start_words)
                        if line_start_words
                        else f"Item on line {line_id}"
                    )

                # Add currency with its context to our list
                currency_contexts.append(
                    {
                        "amount": float(match.amount),
                        "text": match.text,
                        "line_id": match.line_id,
                        "word_id": match.word_id,
                        "x_position": x_pos,
                        "y_position": (
                            match.position.get("y", 0)
                            if isinstance(match.position, dict)
                            else getattr(match.position, "y", 0)
                        ),
                        "full_line": line_text,
                        "prev_line": prev_line_text,
                        "next_line": next_line_text,
                        "left_text": left_text,
                    }
                )

                # Mark lines with multiple amounts as uncertain
                if len(matches) > 1:
                    uncertain_items.append(
                        MultipleAmountsUncertainty(
                            line=next(
                                l
                                for l in receipt_lines
                                if (
                                    hasattr(l, "line_id")
                                    and l.line_id == line_id
                                )
                                or (
                                    isinstance(l, dict)
                                    and l.get("line_id", "") == line_id
                                )
                            ),
                            amounts=[m.amount for m in matches],
                        )
                    )

        # At this point, we have collected all currency amounts with their spatial and contextual information
        # This information can be used in a ChatGPT prompt to classify what each amount represents

        # For now, create a simple heuristic-based classification to output a valid ProcessingResult
        subtotal, tax, total = None, None, None
        line_items = []

        # Basic heuristics for financial summary fields (subtotal, tax, total)
        for context in currency_contexts:
            line_text = context["full_line"].lower()
            left_text = context["left_text"].lower()
            amount = Decimal(str(context["amount"]))

            # Check for subtotal indicators
            if any(
                pattern in line_text
                for pattern in ["subtotal", "sub total", "sub-total", "net"]
            ):
                subtotal = amount
                continue

            # Check for tax indicators
            if any(
                pattern in line_text
                for pattern in ["tax", "vat", "gst", "hst"]
            ):
                tax = amount
                continue

            # Check for total indicators
            if any(
                pattern in line_text
                for pattern in ["total", "balance due", "amount due", "pay"]
            ):
                # Skip "subtotal" lines that also contain "total"
                if (
                    "subtotal" not in line_text
                    and "sub total" not in line_text
                    and "sub-total" not in line_text
                ):
                    total = amount
                    continue

            # If we get here, this might be a line item
            # Create a basic description from text to the left
            description = (
                left_text.strip()
                if left_text
                else f"Item on line {context['line_id']}"
            )

            # Clean up the description
            description = re.sub(r"\s+", " ", description).strip()
            if not description:
                description = f"Item on line {context['line_id']}"

            # Create line item
            item = LineItem(
                description=description,
                quantity=None,  # We'll need more sophisticated logic to extract quantities
                price=Price(
                    unit_price=None,  # We'd need to analyze quantity patterns to determine unit price
                    extended_price=amount,
                ),
                reasoning="Identified through currency pattern matching",
                line_ids=[context["line_id"]],
            )
            line_items.append(item)

        # Generate reasoning for the analysis
        reasoning = "Initial currency detection complete. These results will be refined through LLM analysis."

        # Create the ProcessingResult with collected information
        result = ProcessingResult(
            line_items=line_items,
            subtotal=subtotal,
            tax=tax,
            total=total,
            reasoning=reasoning,
            uncertain_items=uncertain_items,
        )

        # Save the currency contexts for later use in LLM prompt
        result.currency_contexts = currency_contexts

        # Add all detected currency amounts to the result for later spatial analysis
        result.currency_amounts = []

        # Debug the currency detection
        logger.info(
            f"Detected {len(currency_contexts)} currency amounts in receipt"
        )

        for ctx in currency_contexts:
            # Get details about the match
            amount = ctx.get("amount")
            text = ctx.get("full_line", "")
            line_id = ctx.get("line_id", "0")
            x_position = ctx.get("x_position", 0)
            y_position = ctx.get("y_position", 0)

            # Log details
            logger.info(
                f"Currency amount: {amount}, Text: {text}, Line: {line_id}"
            )

            # Create context for this currency amount
            context = {
                "full_line": text,
                "left_text": ctx.get("left_text", ""),
            }

            # Create a CurrencyAmount object and add to result
            currency_amount = CurrencyAmount(
                value=amount,
                text=text,
                line_id=line_id,
                x_position=x_position,
                y_position=y_position,
                context=context,
            )

            # Add to the result list
            result.currency_amounts.append(currency_amount)

        logger.info(
            f"Added {len(result.currency_amounts)} currency amounts to result"
        )

        return result


class LineItemValidator:
    """Validates line items and identifies uncertain entries."""

    def identify_uncertain_items(
        self, result: ProcessingResult
    ) -> List[UncertaintyItem]:
        """Identify items that need additional processing."""
        uncertain_items = result.uncertain_items.copy()

        # Check for missing components
        if result.subtotal is None:
            uncertain_items.append(
                MissingComponentUncertainty(component="subtotal")
            )
        if result.tax is None:
            uncertain_items.append(
                MissingComponentUncertainty(component="tax")
            )
        if result.total is None:
            uncertain_items.append(
                MissingComponentUncertainty(component="total")
            )

        # Validate totals
        if result.subtotal and result.line_items:
            calculated_subtotal = sum(
                item.price.extended_price or Decimal("0")
                for item in result.line_items
            )
            if abs(calculated_subtotal - result.subtotal) > Decimal("0.01"):
                uncertain_items.append(
                    TotalMismatchUncertainty(
                        calculated=calculated_subtotal, found=result.subtotal
                    )
                )

        return uncertain_items

    def validate_full_receipt(self, result: ProcessingResult) -> Dict:
        """Perform final validation of the receipt."""
        validation_results = {"status": "valid", "warnings": [], "errors": []}

        # Validate individual components
        if not result.line_items:
            validation_results["errors"].append("No line items found")

        if result.subtotal is None:
            validation_results["warnings"].append("Missing subtotal")

        if result.tax is None:
            validation_results["warnings"].append("Missing tax")

        if result.total is None:
            validation_results["errors"].append("Missing total")

        # Validate totals if components are present
        # Only check total + tax = expected_total if all values are present
        if all([result.subtotal, result.tax, result.total]):
            expected_total = result.subtotal + result.tax
            if abs(expected_total - result.total) > Decimal("0.01"):
                validation_results["errors"].append(
                    f"Total mismatch: {result.subtotal} + {result.tax} != {result.total}"
                )
        # If we have total and subtotal but no tax, check if total ≈ subtotal
        elif result.total is not None and result.subtotal is not None:
            if abs(result.total - result.subtotal) > Decimal("0.01"):
                # Only warn about this as tax might be included in item prices
                validation_results["warnings"].append(
                    f"Total ({result.total}) differs significantly from subtotal ({result.subtotal})"
                )

        # Update status based on errors
        if validation_results["errors"]:
            validation_results["status"] = "invalid"
        elif validation_results["warnings"]:
            validation_results["status"] = "valid_with_warnings"

        return validation_results


class LineItemProcessor:
    """Processes line items in receipts using a progressive refinement approach."""

    def __init__(self, gpt_api_key: Optional[str] = None):
        """
        Initialize the LineItemProcessor.

        Args:
            gpt_api_key: The OpenAI API key to use for processing.
        """
        import os

        self.openai_api_key = gpt_api_key

        # Add debug logging for API key detection
        logger.info("--- API KEY DETECTION DEBUG ---")
        env_key = os.getenv("OPENAI_API_KEY")
        if env_key:
            # Only log first and last few characters for security
            masked_key = (
                env_key[:4] + "..." + env_key[-4:]
                if len(env_key) > 8
                else "***"
            )
            logger.info("ENV variable OPENAI_API_KEY is set: %s", masked_key)
        else:
            logger.warning("ENV variable OPENAI_API_KEY is NOT set")

        if self.openai_api_key:
            masked_key = (
                self.openai_api_key[:4] + "..." + self.openai_api_key[-4:]
                if len(self.openai_api_key) > 8
                else "***"
            )
            logger.info("Constructor provided API key: %s", masked_key)
        else:
            logger.warning("No API key provided to constructor")

        # Determine which API key will be used
        if self.openai_api_key:
            logger.info("Will use constructor-provided API key")
        elif env_key:
            logger.info("Will use environment variable API key")
            self.openai_api_key = env_key
        else:
            logger.warning("No API key available from any source")

        # The API key is passed directly to the LLMProcessor
        # No need to modify global environment variables

        logger.info("---------------------------")

        self.fast_processor = FastPatternMatcher()
        self.validator = LineItemValidator()
        self.llm_processor = LLMProcessor(self.openai_api_key)

    def _find_description_words(
        self,
        receipt_words: List[ReceiptWord],
        description: str,
        line_ids: List[int],
    ) -> List[ReceiptWord]:
        """
        Find words that correspond to an item description.

        Args:
            receipt_words: List of all receipt words
            description: The description text to match
            line_ids: List of line IDs where the item appears

        Returns:
            List of ReceiptWord objects that match the description
        """
        # Skip price-only descriptions (too generic)
        if re.match(r"^\$?\d+\.\d{2}$", description):
            return []

        # Skip empty descriptions
        if not description or description.strip() == "":
            return []

        # Get all words from the requested lines
        line_words = [
            word for word in receipt_words if word.line_id in line_ids
        ]
        if not line_words:
            return []

        # Filter out price-like words from potential matches
        def is_price_like(text):
            # Check if the word looks like a price or quantity
            return bool(
                re.match(r"^\$?\d+\.\d{2}$", text)  # $XX.XX or XX.XX
                or re.match(r"^\d+$", text)  # Just a number
                or text in ["$", "usd", "total", "subtotal", "tax"]
            )

        # Create a cleaned version of the description for matching
        clean_description = description.lower().strip()

        # If description contains "Item on line X", it's a placeholder
        if re.match(r"item on line \d+", clean_description):
            # Return all non-price words as potential description words
            return [
                word for word in line_words if not is_price_like(word.text)
            ]
        else:
            # Otherwise, find words that match parts of the description
            matched_words = []

            # Try exact matching first
            for word in line_words:
                word_text = word.text.lower().strip()
                if (
                    not is_price_like(word_text)
                    and word_text in clean_description
                ):
                    matched_words.append(word)

            # If we got exact matches, return them
            if matched_words:
                return matched_words

            # For short descriptions, try fuzzy matching
            if len(clean_description.split()) <= 3:
                for word in line_words:
                    word_text = word.text.lower().strip()
                    # Skip price-like words
                    if is_price_like(word_text):
                        continue

                    # Check if the word is at least partially contained in the description
                    # or if description is contained in the word (for abbreviated text)
                    if (
                        word_text in clean_description
                        or clean_description in word_text
                        or any(
                            w in word_text for w in clean_description.split()
                        )
                        or any(
                            word_text in d for d in clean_description.split()
                        )
                    ):
                        matched_words.append(word)

            # If we still don't have matches, return all non-financial words as a fallback
            if not matched_words:
                return [
                    word for word in line_words if not is_price_like(word.text)
                ]

            return matched_words

    def _find_quantity_words(
        self,
        receipt_words: List[ReceiptWord],
        quantity: Quantity,
        line_ids: List[int],
    ) -> Dict[str, List[ReceiptWord]]:
        """
        Find words that correspond to quantity and unit.

        Args:
            receipt_words: List of all receipt words
            quantity: The Quantity object to match
            line_ids: List of line IDs where the item appears

        Returns:
            Dictionary with 'amount' and 'unit' keys mapping to lists of matching words
        """
        line_words = [
            word for word in receipt_words if word.line_id in line_ids
        ]
        result = {"amount": [], "unit": []}

        # Convert amount to string representations (handle both "2" and "2.0")
        amount_str = str(quantity.amount)
        amount_float_str = str(float(quantity.amount))

        # Convert unit to lowercase for case-insensitive matching
        unit_lower = quantity.unit.lower()

        for word in line_words:
            word_text = word.text
            word_text_lower = word_text.lower()

            # Check for quantity amount match
            if word_text == amount_str or word_text == amount_float_str:
                result["amount"].append(word)

            # Check for unit match
            elif word_text_lower == unit_lower:
                result["unit"].append(word)

            # Handle combined cases like "2lb"
            elif any(
                word_text_lower.startswith(amt + unit_lower)
                for amt in [amount_str, amount_float_str]
            ):
                result["amount"].append(word)
                result["unit"].append(word)

        return result

    def _find_price_words(
        self,
        receipt_words: List[ReceiptWord],
        price: Decimal,
        line_ids: List[int],
    ) -> List[ReceiptWord]:
        """
        Find words that correspond to a price.

        Args:
            receipt_words: List of all receipt words
            price: The price value to match
            line_ids: List of line IDs where the item appears

        Returns:
            List of ReceiptWord objects that match the price
        """
        line_words = [
            word for word in receipt_words if word.line_id in line_ids
        ]
        matching_words = []

        # Generate different string representations of the price
        price_str = str(price)
        price_float_str = str(float(price))

        # Also handle currency symbols and formatting
        # $10.99, 10.99, $10, etc.
        for word in line_words:
            word_text = word.text

            # Remove currency symbols and spaces for comparison
            clean_text = re.sub(r"[$€£¥\s,]", "", word_text)

            if clean_text == price_str or clean_text == price_float_str:
                matching_words.append(word)

        return matching_words

    def _create_word_labels(
        self,
        receipt_words: List[ReceiptWord],
        initial_results: ProcessingResult,
    ) -> Dict[Tuple[int, int], Dict]:
        """
        Create word labels based on line item analysis results.

        Args:
            receipt_words: List of all receipt words
            initial_results: The processing results with line items and totals

        Returns:
            Dictionary mapping (line_id, word_id) to label information
        """
        word_labels = {}

        # For pretty logging
        label_summary = {}

        # Label line items
        for item_idx, item in enumerate(initial_results.line_items):
            # Add item to label summary for logging
            item_key = f"Item {item_idx+1}: {item.description}"
            label_summary[item_key] = {
                "labels": [],
                "price": (
                    str(item.price.extended_price)
                    if item.price and item.price.extended_price
                    else "N/A"
                ),
                "quantity": (
                    str(item.quantity.amount) + " " + item.quantity.unit
                    if item.quantity
                    else "N/A"
                ),
            }

            # Label item description
            description_words = self._find_description_words(
                receipt_words, item.description, item.line_ids
            )
            for word in description_words:
                word_labels[(word.line_id, word.word_id)] = {
                    "label": ITEM_NAME_LABEL,
                    "item_index": item_idx,
                    "reasoning": f"Part of item description: {item.description}",
                    "text": word.text,
                }
                # Add to summary for logging
                label_summary[item_key]["labels"].append(
                    {
                        "text": word.text,
                        "label": ITEM_NAME_LABEL,
                        "position": f"L{word.line_id}W{word.word_id}",
                    }
                )

            # Label quantity if present
            if item.quantity:
                quantity_words = self._find_quantity_words(
                    receipt_words, item.quantity, item.line_ids
                )

                # Label amount words
                for word in quantity_words["amount"]:
                    word_labels[(word.line_id, word.word_id)] = {
                        "label": ITEM_QUANTITY_LABEL,
                        "item_index": item_idx,
                        "reasoning": f"Quantity amount for item: {item.quantity.amount}",
                        "text": word.text,
                    }
                    # Add to summary for logging
                    label_summary[item_key]["labels"].append(
                        {
                            "text": word.text,
                            "label": ITEM_QUANTITY_LABEL,
                            "position": f"L{word.line_id}W{word.word_id}",
                        }
                    )

                # Label unit words
                for word in quantity_words["unit"]:
                    # Check if this word is already labeled as an amount (combined case)
                    if (
                        word.line_id,
                        word.word_id,
                    ) in word_labels and word_labels[
                        (word.line_id, word.word_id)
                    ][
                        "label"
                    ] == ITEM_QUANTITY_LABEL:
                        # Update the reasoning to indicate it's both
                        word_labels[(word.line_id, word.word_id)][
                            "reasoning"
                        ] += f" with unit: {item.quantity.unit}"
                    else:
                        word_labels[(word.line_id, word.word_id)] = {
                            "label": ITEM_UNIT_LABEL,
                            "item_index": item_idx,
                            "reasoning": f"Unit of measure for item: {item.quantity.unit}",
                            "text": word.text,
                        }
                        # Add to summary for logging
                        label_summary[item_key]["labels"].append(
                            {
                                "text": word.text,
                                "label": ITEM_UNIT_LABEL,
                                "position": f"L{word.line_id}W{word.word_id}",
                            }
                        )

            # Label prices if present
            if item.price:
                # Label unit price if present
                if item.price.unit_price:
                    unit_price_words = self._find_price_words(
                        receipt_words, item.price.unit_price, item.line_ids
                    )
                    for word in unit_price_words:
                        word_labels[(word.line_id, word.word_id)] = {
                            "label": ITEM_PRICE_LABEL,
                            "item_index": item_idx,
                            "reasoning": f"Unit price for item: {item.price.unit_price}",
                            "text": word.text,
                        }
                        # Add to summary for logging
                        label_summary[item_key]["labels"].append(
                            {
                                "text": word.text,
                                "label": ITEM_PRICE_LABEL,
                                "position": f"L{word.line_id}W{word.word_id}",
                            }
                        )

                # Label extended price if present and different from unit price
                if item.price.extended_price and (
                    not item.price.unit_price
                    or item.price.extended_price != item.price.unit_price
                ):
                    ext_price_words = self._find_price_words(
                        receipt_words, item.price.extended_price, item.line_ids
                    )
                    for word in ext_price_words:
                        word_labels[(word.line_id, word.word_id)] = {
                            "label": ITEM_TOTAL_LABEL,
                            "item_index": item_idx,
                            "reasoning": f"Extended price for item: {item.price.extended_price}",
                            "text": word.text,
                        }
                        # Add to summary for logging
                        label_summary[item_key]["labels"].append(
                            {
                                "text": word.text,
                                "label": ITEM_TOTAL_LABEL,
                                "position": f"L{word.line_id}W{word.word_id}",
                            }
                        )

        # Label receipt totals
        summary_labels = {"Subtotal": [], "Tax": [], "Total": []}

        # Find subtotal
        if initial_results.subtotal:
            for word in receipt_words:
                clean_text = re.sub(r"[$€£¥\s,]", "", word.text)
                if clean_text == str(
                    initial_results.subtotal
                ) or clean_text == str(float(initial_results.subtotal)):
                    # Check if this word is near "subtotal" text
                    is_subtotal = False
                    nearby_words = [
                        w
                        for w in receipt_words
                        if abs(w.line_id - word.line_id) <= 1
                    ]
                    for nearby in nearby_words:
                        if "subtotal" in nearby.text.lower():
                            is_subtotal = True
                            break

                    # If confirmed subtotal or no better label exists
                    if (
                        is_subtotal
                        or (word.line_id, word.word_id) not in word_labels
                    ):
                        word_labels[(word.line_id, word.word_id)] = {
                            "label": SUBTOTAL_LABEL,
                            "item_index": None,
                            "reasoning": f"Subtotal amount: {initial_results.subtotal}",
                            "text": word.text,
                        }
                        # Add to summary for logging
                        summary_labels["Subtotal"].append(
                            {
                                "text": word.text,
                                "position": f"L{word.line_id}W{word.word_id}",
                            }
                        )

        # Find tax
        if initial_results.tax:
            for word in receipt_words:
                clean_text = re.sub(r"[$€£¥\s,]", "", word.text)
                if clean_text == str(initial_results.tax) or clean_text == str(
                    float(initial_results.tax)
                ):
                    # Check if this word is near "tax" text
                    is_tax = False
                    nearby_words = [
                        w
                        for w in receipt_words
                        if abs(w.line_id - word.line_id) <= 1
                    ]
                    for nearby in nearby_words:
                        if "tax" in nearby.text.lower():
                            is_tax = True
                            break

                    # If confirmed tax or no better label exists
                    if (
                        is_tax
                        or (word.line_id, word.word_id) not in word_labels
                    ):
                        word_labels[(word.line_id, word.word_id)] = {
                            "label": TAX_LABEL,
                            "item_index": None,
                            "reasoning": f"Tax amount: {initial_results.tax}",
                            "text": word.text,
                        }
                        # Add to summary for logging
                        summary_labels["Tax"].append(
                            {
                                "text": word.text,
                                "position": f"L{word.line_id}W{word.word_id}",
                            }
                        )

        # Find total
        if initial_results.total:
            for word in receipt_words:
                clean_text = re.sub(r"[$€£¥\s,]", "", word.text)
                if clean_text == str(
                    initial_results.total
                ) or clean_text == str(float(initial_results.total)):
                    # Check if this word is near "total" text
                    is_total = False
                    nearby_words = [
                        w
                        for w in receipt_words
                        if abs(w.line_id - word.line_id) <= 1
                    ]
                    for nearby in nearby_words:
                        if "total" in nearby.text.lower():
                            is_total = True
                            break

                    # If confirmed total or no better label exists
                    if (
                        is_total
                        or (word.line_id, word.word_id) not in word_labels
                    ):
                        word_labels[(word.line_id, word.word_id)] = {
                            "label": TOTAL_LABEL,
                            "item_index": None,
                            "reasoning": f"Total amount: {initial_results.total}",
                            "text": word.text,
                        }
                        # Add to summary for logging
                        summary_labels["Total"].append(
                            {
                                "text": word.text,
                                "position": f"L{word.line_id}W{word.word_id}",
                            }
                        )

        # Create summary of financial field labels for logging
        financial_summary = {
            "subtotal": {"value": initial_results.subtotal, "words": []},
            "tax": {"value": initial_results.tax, "words": []},
            "total": {"value": initial_results.total, "words": []},
        }

        # Add words to the financial summary
        for label_type, words_list in summary_labels.items():
            if label_type.lower() == "subtotal" and words_list:
                for word_info in words_list:
                    # Reconstruct ReceiptWord-like objects for the summary
                    word = type("", (), {})()
                    word.line_id = int(
                        word_info["position"].split("L")[1].split("W")[0]
                    )
                    word.word_id = int(word_info["position"].split("W")[1])
                    word.text = word_info["text"]
                    financial_summary["subtotal"]["words"].append(word)
            elif label_type.lower() == "tax" and words_list:
                for word_info in words_list:
                    word = type("", (), {})()
                    word.line_id = int(
                        word_info["position"].split("L")[1].split("W")[0]
                    )
                    word.word_id = int(word_info["position"].split("W")[1])
                    word.text = word_info["text"]
                    financial_summary["tax"]["words"].append(word)
            elif label_type.lower() == "total" and words_list:
                for word_info in words_list:
                    word = type("", (), {})()
                    word.line_id = int(
                        word_info["position"].split("L")[1].split("W")[0]
                    )
                    word.word_id = int(word_info["position"].split("W")[1])
                    word.text = word_info["text"]
                    financial_summary["total"]["words"].append(word)

        # Log a pretty summary of the labeled words
        self._log_label_summary(
            initial_results, financial_summary, word_labels
        )

        return word_labels

    def _log_label_summary(
        self,
        results: ProcessingResult,
        financial_fields: Dict = None,
        word_labels: Dict = None,
    ):
        """
        Print a pretty, structured summary of the line item labeling.

        Args:
            results: ProcessingResult object with line items and totals
            financial_fields: Dictionary with financial field information
            word_labels: Dictionary mapping (line_id, word_id) to label information
        """
        divider = "=" * 70

        logger.info(
            f"\n{divider}\n📋 LINE ITEM LABELING SUMMARY 📋\n{divider}"
        )

        # Financial overview section
        logger.info("💰 FINANCIAL OVERVIEW:")

        subtotal_display = (
            f"{results.subtotal}" if results.subtotal else "Not found"
        )
        tax_display = f"{results.tax}" if results.tax else "Not found"
        total_display = f"{results.total}" if results.total else "Not found"

        # Show financial values with source information
        if financial_fields:
            # Check if we have source words for financial fields
            if financial_fields["subtotal"]["words"]:
                word = financial_fields["subtotal"]["words"][0]
                subtotal_display = f"{results.subtotal} (found at L{word.line_id}W{word.word_id})"

            if financial_fields["tax"]["words"]:
                word = financial_fields["tax"]["words"][0]
                tax_display = (
                    f"{results.tax} (found at L{word.line_id}W{word.word_id})"
                )

            if financial_fields["total"]["words"]:
                word = financial_fields["total"]["words"][0]
                total_display = f"{results.total} (found at L{word.line_id}W{word.word_id})"

        logger.info("   Subtotal: %s", subtotal_display)
        logger.info("   Tax:      %s", tax_display)
        logger.info("   Total:    %s", total_display)

        # Line items section
        logger.info("\n🛒 LINE ITEMS DETECTED: %s", len(results.line_items))

        # Skip the rest if no line items
        if not results.line_items:
            logger.info("   No line items detected")
            logger.info("%s\n", divider)
            return

        # Item-by-item breakdown
        for i, item in enumerate(results.line_items):
            logger.info("\n  🏷️  Item %s: %s", i + 1, item.description)

            # Show price and quantity information
            price_display = "N/A"
            if item.price:
                if item.price.extended_price:
                    price_display = str(item.price.extended_price)
            logger.info("      Price: %s", price_display)

            quantity_display = "N/A"
            if item.quantity:
                if item.quantity.amount and item.quantity.unit:
                    quantity_display = (
                        f"{item.quantity.amount} {item.quantity.unit}"
                    )
                elif item.quantity.amount:
                    quantity_display = str(item.quantity.amount)
            logger.info("      Quantity: %s", quantity_display)

            # Show labeled words for this item if available
            if word_labels:
                item_labels = [
                    (key, label)
                    for key, label in word_labels.items()
                    if "item_index" in label and label["item_index"] == i
                ]

                if item_labels:
                    logger.info("      Labeled Words:")
                    for (line_id, word_id), label in item_labels:
                        # Use the text from the label dict, or fall back to empty string
                        word_text = label.get("text", "")
                        logger.info(
                            f"        • '{word_text}' → {label['label']} (L{line_id}W{word_id})"
                        )
                else:
                    logger.info("      No words labeled for this item")

        # Receipt summary labels section
        if word_labels:
            logger.info("\n🧾 RECEIPT SUMMARY LABELS:")

            # Check for subtotal labels
            subtotal_labels = [
                (key, label)
                for key, label in word_labels.items()
                if label["label"] == SUBTOTAL_LABEL
            ]
            if subtotal_labels:
                logger.info("   Subtotal: %s labels", len(subtotal_labels))
                for (line_id, word_id), label in subtotal_labels[
                    :3
                ]:  # Show up to 3
                    word_text = label.get("text", "")
                    logger.info(
                        f"      • '{word_text}' (L{line_id}W{word_id})"
                    )
                if len(subtotal_labels) > 3:
                    logger.info(
                        f"      • ... and {len(subtotal_labels) - 3} more"
                    )
            else:
                logger.info("   Subtotal: No labels found")

            # Check for tax labels
            tax_labels = [
                (key, label)
                for key, label in word_labels.items()
                if label["label"] == TAX_LABEL
            ]
            if tax_labels:
                logger.info("   Tax: %s labels", len(tax_labels))
                for (line_id, word_id), label in tax_labels[
                    :3
                ]:  # Show up to 3
                    word_text = label.get("text", "")
                    logger.info(
                        f"      • '{word_text}' (L{line_id}W{word_id})"
                    )
                if len(tax_labels) > 3:
                    logger.info("      • ... and %s more", len(tax_labels) - 3)
            else:
                logger.info("   Tax: No labels found")

            # Check for total labels
            total_labels = [
                (key, label)
                for key, label in word_labels.items()
                if label["label"] == TOTAL_LABEL
            ]
            if total_labels:
                logger.info("   Total: %s labels", len(total_labels))
                for (line_id, word_id), label in total_labels[
                    :3
                ]:  # Show up to 3
                    word_text = label.get("text", "")
                    logger.info(
                        f"      • '{word_text}' (L{line_id}W{word_id})"
                    )
                if len(total_labels) > 3:
                    logger.info(
                        f"      • ... and {len(total_labels) - 3} more"
                    )
            else:
                logger.info("   Total: No labels found")

        # Label statistics
        total_labels = len(word_labels) if word_labels else 0
        logger.info("\n📊 LABEL STATISTICS:")
        logger.info("   Total words labeled: %s", total_labels)
        logger.info("%s\n", divider)

    def _find_financial_summary_fields(
        self,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
    ) -> Dict:
        """
        Find subtotal, tax, and total fields in the receipt.

        This method uses pattern matching to identify lines that contain financial summary
        information, and then extracts the currency amount from those lines.

        Args:
            receipt_lines: List of receipt lines
            receipt_words: List of receipt words

        Returns:
            Dictionary with subtotal, tax, and total values and their source words
        """
        result = {
            "subtotal": {"value": None, "words": []},
            "tax": {"value": None, "words": []},
            "total": {"value": None, "words": []},
        }

        # Look for summary fields in bottom half of receipt (typical location)
        receipt_lines_by_id = {line.line_id: line for line in receipt_lines}
        total_lines = len(receipt_lines)

        # Look at the entire receipt but prioritize the bottom half
        candidate_line_ids = [line.line_id for line in receipt_lines]

        # Look for subtotal
        for line_id in candidate_line_ids:
            line = receipt_lines_by_id[line_id]
            line_text = " ".join(
                [
                    word.text
                    for word in receipt_words
                    if word.line_id == line_id
                ]
            )

            # Check for subtotal patterns
            if any(
                re.search(pattern, line_text) for pattern in SUBTOTAL_PATTERNS
            ):
                # Extract amount from this line
                currency_amounts = []
                for word in [w for w in receipt_words if w.line_id == line_id]:
                    try:
                        # Clean the text
                        clean_text = re.sub(r"[$€£¥\s,]", "", word.text)
                        if re.match(r"\d+\.\d{2}", clean_text):
                            amount = Decimal(clean_text)
                            currency_amounts.append((amount, word))
                    except (InvalidOperation, ValueError):
                        pass

                # Use the highest amount if multiple found (usually correct for subtotal)
                if currency_amounts:
                    amount, word = max(currency_amounts, key=lambda x: x[0])
                    result["subtotal"]["value"] = amount
                    result["subtotal"]["words"].append(word)

        # Look for tax
        for line_id in candidate_line_ids:
            line = receipt_lines_by_id[line_id]
            line_text = " ".join(
                [
                    word.text
                    for word in receipt_words
                    if word.line_id == line_id
                ]
            )

            # Check for tax patterns
            if any(re.search(pattern, line_text) for pattern in TAX_PATTERNS):
                # Extract amount from this line
                currency_amounts = []
                for word in [w for w in receipt_words if w.line_id == line_id]:
                    try:
                        # Clean the text
                        clean_text = re.sub(r"[$€£¥\s,]", "", word.text)
                        if re.match(r"\d+\.\d{2}", clean_text):
                            amount = Decimal(clean_text)
                            currency_amounts.append((amount, word))
                    except (InvalidOperation, ValueError):
                        pass

                # Use the rightmost amount if multiple found (usually correct for tax)
                if currency_amounts:
                    # Sort by word's x position (assuming text reads left-to-right)
                    right_aligned = sorted(
                        currency_amounts,
                        key=lambda x: (
                            x[1].bounding_box["x"]
                            if hasattr(x[1], "bounding_box")
                            else 0
                        ),
                        reverse=True,
                    )
                    amount, word = right_aligned[0]
                    result["tax"]["value"] = amount
                    result["tax"]["words"].append(word)

        # Look for total - prioritize explicit total patterns first
        found_total = False
        for line_id in candidate_line_ids:
            line = receipt_lines_by_id[line_id]
            line_text = " ".join(
                [
                    word.text
                    for word in receipt_words
                    if word.line_id == line_id
                ]
            )

            # Check for total patterns
            if any(
                re.search(pattern, line_text) for pattern in TOTAL_PATTERNS
            ):
                # Extract amount from this line
                currency_amounts = []
                for word in [w for w in receipt_words if w.line_id == line_id]:
                    try:
                        # Clean the text
                        clean_text = re.sub(r"[$€£¥\s,]", "", word.text)
                        if re.match(r"\d+\.\d{2}", clean_text):
                            amount = Decimal(clean_text)
                            currency_amounts.append((amount, word))
                    except (InvalidOperation, ValueError):
                        pass

                # Use the highest amount if multiple found (usually correct for total)
                if currency_amounts:
                    amount, word = max(currency_amounts, key=lambda x: x[0])
                    result["total"]["value"] = amount
                    result["total"]["words"].append(word)
                    found_total = True
                    logger.debug(
                        f"Found explicit total: {amount} in line: {line_text}"
                    )

        # If we still don't have a total, look for line items that might represent totals
        # (This is a fallback for receipts that don't have explicit "TOTAL" text)
        if not found_total:
            # Expanded list of total phrases to detect line items that might be totals
            total_phrases = [
                "total",
                "balance due",
                "amount due",
                "grand total",
                "payment due",
                "due",
                "pay",
                "balance",
                "payment total",
                "order total",
                "final amount",
                "to pay",
                "please pay",
                "amount",
                "payment",
                "charge",
                "sum",
                "final",
            ]

            # Find the best candidate for a total by looking at line text
            potential_totals = []

            for line_id in candidate_line_ids:
                line = receipt_lines_by_id[line_id]
                line_text = " ".join(
                    [
                        word.text
                        for word in receipt_words
                        if word.line_id == line_id
                    ]
                ).lower()

                # Check if this line has total-like text
                if any(phrase in line_text for phrase in total_phrases):
                    # Extract amount from this line
                    currency_amounts = []
                    for word in [
                        w for w in receipt_words if w.line_id == line_id
                    ]:
                        try:
                            # Clean the text
                            clean_text = re.sub(r"[$€£¥\s,]", "", word.text)
                            if re.match(r"\d+\.\d{2}", clean_text):
                                amount = Decimal(clean_text)
                                # Score this match based on how many total phrases it contains
                                score = sum(
                                    1
                                    for phrase in total_phrases
                                    if phrase in line_text.lower()
                                )
                                currency_amounts.append((amount, word, score))
                        except (InvalidOperation, ValueError):
                            pass

                    # Add the highest amount with its score to potential totals
                    if currency_amounts:
                        amount, word, score = max(
                            currency_amounts, key=lambda x: (x[2], x[0])
                        )
                        potential_totals.append(
                            (amount, word, score, line_text)
                        )

            # If we found potential totals, use the one with the highest score
            # (or the highest amount if scores are equal)
            if potential_totals:
                # Sort by score (primary) and amount (secondary)
                potential_totals.sort(key=lambda x: (x[2], x[0]), reverse=True)
                amount, word, score, line_text = potential_totals[0]
                result["total"]["value"] = amount
                result["total"]["words"].append(word)
                logger.debug(
                    f"Found implicit total: {amount} from line item: {line_text}"
                )

        return result

    def _post_process_line_items(
        self, result: ProcessingResult
    ) -> ProcessingResult:
        """
        Post-process line items to identify and handle items that appear to be totals.

        Args:
            result: The initial processing result

        Returns:
            Updated processing result with refined line items and totals
        """
        # Clone the result
        updated_result = ProcessingResult(
            line_items=result.line_items.copy(),
            subtotal=result.subtotal,
            tax=result.tax,
            total=result.total,
            reasoning=result.reasoning,
            uncertain_items=result.uncertain_items.copy(),
        )

        # Only proceed if we don't already have a total
        if not updated_result.total:
            # Total-related phrases to detect in line item descriptions
            total_phrases = [
                "total",
                "balance due",
                "amount due",
                "grand total",
                "payment due",
                "due",
                "pay",
                "balance",
                "payment total",
                "order total",
                "final amount",
                "to pay",
                "please pay",
            ]

            potential_total_items = []
            regular_items = []

            # Identify line items that might be totals
            for item in updated_result.line_items:
                if hasattr(item, "description") and item.description:
                    desc_lower = item.description.lower()

                    # Check if this line item resembles a total
                    if any(phrase in desc_lower for phrase in total_phrases):
                        # Score the match based on phrase appearance
                        score = sum(
                            1
                            for phrase in total_phrases
                            if phrase in desc_lower
                        )
                        potential_total_items.append((item, score))
                    else:
                        regular_items.append(item)
                else:
                    regular_items.append(item)

            # If we found potential total items, process them
            if potential_total_items:
                # Sort by score (higher means more likely to be a total)
                potential_total_items.sort(key=lambda x: x[1], reverse=True)

                # Use the highest-scored item as the total
                best_match, score = potential_total_items[0]

                # Set the total and keep the rest of the items
                if (
                    hasattr(best_match.price, "extended_price")
                    and best_match.price.extended_price
                ):
                    logger.info(
                        f"Using line item '{best_match.description}' as total: {best_match.price.extended_price}"
                    )
                    updated_result.total = best_match.price.extended_price

                    # Keep all remaining items
                    updated_result.line_items = [
                        item
                        for item in updated_result.line_items
                        if item != best_match
                    ]

                    # Add reasoning about this conversion
                    if updated_result.reasoning:
                        updated_result.reasoning += f" Identified '{best_match.description}' as the receipt total."
                    else:
                        updated_result.reasoning = f"Identified '{best_match.description}' as the receipt total."

            # If we get here and still don't have a total but do have line items,
            # consider using the sum of line items as a synthetic total
            if not updated_result.total and updated_result.line_items:
                line_items_sum = sum(
                    item.price.extended_price or Decimal("0")
                    for item in updated_result.line_items
                )
                if line_items_sum > Decimal("0"):
                    logger.info(
                        f"Creating synthetic total from line items sum: {line_items_sum}"
                    )
                    updated_result.total = line_items_sum

                    if updated_result.reasoning:
                        updated_result.reasoning += (
                            " Created total from sum of line items."
                        )
                    else:
                        updated_result.reasoning = (
                            "Created total from sum of line items."
                        )

        return updated_result

    def _process_line_items(
        self, line_items: List[LineItem]
    ) -> List[LineItem]:
        """Process line items to differentiate between regular items and summary items.

        Args:
            line_items: List of line items to process

        Returns:
            Processed list with summary items removed and marked appropriately
        """
        regular_items = []

        for item in line_items:
            # Skip items without descriptions or prices
            if not hasattr(item, "description") or not item.description:
                continue
            if (
                not hasattr(item, "price")
                or not item.price
                or not hasattr(item.price, "extended_price")
            ):
                continue

            # Check if this is likely a summary item (total, subtotal, tax, etc.)
            desc_lower = item.description.lower()

            # Use more specific patterns for filtering
            # Check for exact total-like items (more restrictive than the pattern matching)
            is_total_item = any(
                term in desc_lower
                for term in [
                    "total",
                    "balance due",
                    "amount due",
                    "grand total",
                    "payment total",
                    "order total",
                    "total amount",
                    "total due",
                    "payment due",
                ]
            )

            # Check for exact subtotal-like items
            is_subtotal_item = any(
                term in desc_lower
                for term in ["subtotal", "sub total", "sub-total", "net total"]
            )

            # Check for exact tax-like items
            is_tax_item = any(
                term in desc_lower
                for term in [
                    "tax",
                    "vat",
                    "gst",
                    "hst",
                    "sales tax",
                    "tax amount",
                ]
            )

            # Only filter out items that are clearly financial summary items
            if is_total_item and item.price.extended_price > Decimal(0):
                # This is likely a total item, not a regular line item
                logger.debug(
                    f"Identified potential total: {item.description} - {item.price.extended_price}"
                )
                continue

            if is_subtotal_item and item.price.extended_price > Decimal(0):
                # This is likely a subtotal item, not a regular line item
                logger.debug(
                    f"Identified potential subtotal: {item.description} - {item.price.extended_price}"
                )
                continue

            if is_tax_item and item.price.extended_price > Decimal(0):
                # This is likely a tax item, not a regular line item
                logger.debug(
                    f"Identified potential tax: {item.description} - {item.price.extended_price}"
                )
                continue

            # If we get here, this is a regular line item
            regular_items.append(item)

        return regular_items

    def process(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        uncertainty_handler=None,
    ) -> LineItemAnalysis:
        """Process receipt line items."""
        logger.info("Processing receipt %s for line items", receipt.receipt_id)

        try:
            # Stage 1: Run pattern analysis to detect all currency amounts
            logger.info(
                "Stage 1: Running pattern analysis to detect all currency amounts"
            )

            # Extract financial amounts using pattern matching
            pattern_analyzer = FastPatternMatcher()
            initial_result = pattern_analyzer.process(
                receipt, receipt_lines, receipt_words, structure_analysis=None
            )

            # Stage 2: Check if we have currency amounts to analyze
            logger.info(
                f"Stage 2: Found {len(initial_result.currency_amounts) if hasattr(initial_result, 'currency_amounts') else 0} currency amounts to analyze"
            )

            # If no currency amounts found, return the basic results
            if (
                not hasattr(initial_result, "currency_amounts")
                or not initial_result.currency_amounts
            ):
                logger.info("No currency amounts to analyze, using basic results")
                # Convert ProcessingResult to LineItemAnalysis and return
                return LineItemAnalysis(
                    items=initial_result.line_items,
                    total_found=len(initial_result.line_items),
                    subtotal=initial_result.subtotal,
                    tax=initial_result.tax,
                    total=initial_result.total,
                    discrepancies=[],
                    reasoning=initial_result.reasoning,
                    word_labels=self._create_word_labels(
                        receipt_words, initial_result
                    ),
                )

            # Prepare currency contexts for GPT analysis
            currency_contexts = []
            for amt in initial_result.currency_amounts:
                currency_contexts.append(
                    {
                        "amount": amt.value,
                        "left_text": amt.context.get("left_text", ""),
                        "full_line": amt.text,
                        "x_position": amt.x_position,
                        "y_position": amt.y_position,
                        "line_id": amt.line_id,
                    }
                )

            # Stage 3: Use enhanced pattern analysis to classify currency amounts
            logger.info(
                f"Stage 3: Using enhanced pattern analysis to classify {len(currency_contexts)} currency amounts"
            )

            # Process with enhanced pattern analyzer
            try:
                # Use enhanced pattern analysis instead of GPT
                enhanced_result = enhanced_pattern_analysis(currency_contexts)
                
                # Log the result
                logger.info(
                    f"Enhanced Pattern Analysis Result: {json.dumps(enhanced_result, indent=2)}"
                )
                
                # Extract information from enhanced analysis
                if enhanced_result:
                        # Create line items from enhanced analysis
                        classified_items = []

                        # Process line items from the classification
                        for item in enhanced_result.get("line_items", []):
                            # Extract the amount and description
                            amount = item.get("amount")
                            description = item.get("description", "")

                            # Skip items without an amount
                            if amount is None:
                                continue

                            # Convert amount to Decimal
                            try:
                                amount_decimal = Decimal(str(amount))
                            except (ValueError, InvalidOperation):
                                logger.warning(
                                    f"Invalid amount in GPT response: {amount}"
                                )
                                continue

                            # Create quantity if available
                            quantity = None
                            if (
                                "quantity" in item
                                and item["quantity"] is not None
                            ):
                                try:
                                    quantity = Quantity(
                                        amount=Decimal(str(item["quantity"])),
                                        unit=item.get("unit", ""),
                                    )
                                except (ValueError, InvalidOperation):
                                    logger.warning(
                                        f"Invalid quantity in GPT response: {item['quantity']}"
                                    )

                            # Create price
                            price = Price(
                                unit_price=(
                                    Decimal(str(item["unit_price"]))
                                    if "unit_price" in item
                                    and item["unit_price"] is not None
                                    else None
                                ),
                                extended_price=amount_decimal,
                            )

                            # Create line item
                            line_item = LineItem(
                                description=description,
                                quantity=quantity,
                                price=price,
                                reasoning="Classified by GPT spatial analysis",
                                line_ids=[item.get("line_id", "0")],
                            )
                            classified_items.append(line_item)

                        # Extract financial fields from classification
                        subtotal = None
                        tax = None
                        total = None

                        # First check if financial_summary is present
                        if "financial_summary" in enhanced_result and isinstance(
                            enhanced_result["financial_summary"], dict
                        ):
                            # Extract values from financial_summary
                            if (
                                "subtotal" in enhanced_result["financial_summary"]
                                and enhanced_result["financial_summary"]["subtotal"]
                                is not None
                            ):
                                try:
                                    subtotal = Decimal(
                                        str(
                                            enhanced_result["financial_summary"][
                                                "subtotal"
                                            ]
                                        )
                                    )
                                except (ValueError, InvalidOperation):
                                    logger.warning(
                                        f"Invalid subtotal in financial_summary: {enhanced_result['financial_summary']['subtotal']}"
                                    )

                            if (
                                "tax" in enhanced_result["financial_summary"]
                                and enhanced_result["financial_summary"]["tax"]
                                is not None
                            ):
                                try:
                                    tax = Decimal(
                                        str(
                                            enhanced_result["financial_summary"][
                                                "tax"
                                            ]
                                        )
                                    )
                                except (ValueError, InvalidOperation):
                                    logger.warning(
                                        f"Invalid tax in financial_summary: {enhanced_result['financial_summary']['tax']}"
                                    )

                            if (
                                "total" in enhanced_result["financial_summary"]
                                and enhanced_result["financial_summary"]["total"]
                                is not None
                            ):
                                try:
                                    total = Decimal(
                                        str(
                                            enhanced_result["financial_summary"][
                                                "total"
                                            ]
                                        )
                                    )
                                except (ValueError, InvalidOperation):
                                    logger.warning(
                                        f"Invalid total in financial_summary: {enhanced_result['financial_summary']['total']}"
                                    )

                        # If financial_summary didn't provide all values, fall back to classification
                        if subtotal is None or tax is None or total is None:
                            # Process the classification to find financial fields
                            for item in enhanced_result.get("classification", []):
                                category = item.get("category")
                                amount = item.get("amount")

                                # Skip items without category or amount
                                if not category or amount is None:
                                    continue

                                # Convert amount to Decimal
                                try:
                                    amount_decimal = Decimal(str(amount))
                                except (ValueError, InvalidOperation):
                                    logger.warning(
                                        f"Invalid amount in GPT classification: {amount}"
                                    )
                                    continue

                                # Assign to appropriate field if not already set from financial_summary
                                if category == "SUBTOTAL" and subtotal is None:
                                    subtotal = amount_decimal
                                elif category == "TAX" and tax is None:
                                    tax = amount_decimal
                                elif category == "TOTAL" and total is None:
                                    total = amount_decimal

                        # Fall back to initial results if needed
                        if subtotal is None:
                            subtotal = initial_result.subtotal
                        if tax is None:
                            tax = initial_result.tax
                        if total is None:
                            total = initial_result.total

                        # Second-pass analysis: Try to infer financial fields if they're still missing
                        if subtotal is None or tax is None or total is None:
                            logger.info(
                                "Some financial fields missing, attempting to infer them..."
                            )

                            # Calculate potential subtotal from line items
                            if subtotal is None and classified_items:
                                potential_subtotal = sum(
                                    item.price.extended_price
                                    for item in classified_items
                                    if hasattr(item, "price")
                                    and item.price
                                    and item.price.extended_price
                                )
                                if potential_subtotal > Decimal("0"):
                                    logger.info(
                                        f"Inferred subtotal from sum of line items: {potential_subtotal}"
                                    )
                                    subtotal = potential_subtotal

                            # Try to identify tax based on typical percentage of subtotal
                            if tax is None and subtotal is not None:
                                # Look for amounts that might be tax (usually 5-10% of subtotal)
                                for item in classified_items:
                                    if (
                                        hasattr(item, "price")
                                        and item.price
                                        and item.price.extended_price
                                        and item.price.extended_price
                                        > Decimal("0")
                                    ):
                                        tax_ratio = (
                                            item.price.extended_price
                                            / subtotal
                                        )
                                        if (
                                            Decimal("0.03")
                                            < tax_ratio
                                            < Decimal("0.15")
                                        ):  # Common tax rates
                                            logger.info(
                                                f"Potential tax found: {item.price.extended_price} ({tax_ratio:.2%} of subtotal)"
                                            )
                                            if (
                                                tax is None
                                                or item.price.extended_price
                                                > tax
                                            ):
                                                tax = item.price.extended_price
                                                # Remove this item from line items as it's likely tax
                                                classified_items = [
                                                    i
                                                    for i in classified_items
                                                    if i != item
                                                ]

                            # Infer total if still missing
                            if total is None:
                                # If we have subtotal and tax, total should be their sum
                                if subtotal is not None and tax is not None:
                                    total = subtotal + tax
                                    logger.info(
                                        f"Inferred total from subtotal + tax: {total}"
                                    )
                                # Otherwise, look for the largest amount near the end of the receipt
                                elif classified_items:
                                    # Sort by line ID (descending) and then by amount (descending)
                                    items_by_line = sorted(
                                        [
                                            (
                                                (
                                                    int(item.line_ids[0])
                                                    if item.line_ids
                                                    else 0
                                                ),
                                                item,
                                            )
                                            for item in classified_items
                                        ],
                                        key=lambda x: x[0],
                                        reverse=True,
                                    )

                                    # Take the top few items and find the largest amount
                                    potential_totals = items_by_line[
                                        :5
                                    ]  # Look at the last 5 lines
                                    if potential_totals:
                                        largest_amount_item = max(
                                            [
                                                item
                                                for _, item in potential_totals
                                                if hasattr(item, "price")
                                                and item.price
                                                and item.price.extended_price
                                            ],
                                            key=lambda x: x.price.extended_price,
                                            default=None,
                                        )

                                        if (
                                            largest_amount_item
                                            and largest_amount_item.price.extended_price
                                            > Decimal("0")
                                        ):
                                            logger.info(
                                                f"Identified potential total from position and value: {largest_amount_item.price.extended_price}"
                                            )
                                            total = (
                                                largest_amount_item.price.extended_price
                                            )
                                            # Remove this item from line items as it's likely the total
                                            classified_items = [
                                                i
                                                for i in classified_items
                                                if i != largest_amount_item
                                            ]

                        # Log what we've found
                        if subtotal is not None:
                            logger.info("Identified subtotal: %s", subtotal)
                        if tax is not None:
                            logger.info("Identified tax: %s", tax)
                        if total is not None:
                            logger.info("Identified total: %s", total)

                        # Use the GPT results instead of initial results
                        initial_result = ProcessingResult(
                            line_items=classified_items,
                            subtotal=subtotal,
                            tax=tax,
                            total=total,
                            reasoning=initial_result.reasoning
                            + " Refined through enhanced pattern analysis.",
                            uncertain_items=initial_result.uncertain_items,
                        )
                else:
                    logger.info(
                        "Enhanced pattern analysis returned no results"
                    )

            except Exception as e:
                logger.error("Error in enhanced pattern analysis: %s", str(e))
                logger.error(traceback.format_exc())

            # Create the final analysis
            validation_results = self.validator.validate_full_receipt(
                initial_result
            )
            try:
                return LineItemAnalysis(
                    items=initial_result.line_items,
                    total_found=len(initial_result.line_items),
                    subtotal=initial_result.subtotal,
                    tax=initial_result.tax,
                    total=initial_result.total,
                    discrepancies=[
                        f"{item}"
                        for item in validation_results.get("warnings", [])
                    ]
                    + [
                        f"{item}"
                        for item in validation_results.get("errors", [])
                    ],
                    reasoning=initial_result.reasoning,
                    word_labels=self._create_word_labels(
                        receipt_words, initial_result
                    ),
                )
            except Exception as e:
                logger.error("Error creating LineItemAnalysis: %s", str(e))
                logger.error(traceback.format_exc())
                # Return a valid LineItemAnalysis object even in case of errors
                return LineItemAnalysis(
                    items=[],
                    total_found=0,
                    subtotal=None,
                    tax=None,
                    total=None,
                    discrepancies=[
                        "Error creating LineItemAnalysis: " + str(e)
                    ],
                    reasoning=f"Error creating LineItemAnalysis: {str(e)}",
                    word_labels={},
                )

        except Exception as e:
            logger.error("Error processing receipt: %s", str(e))
            logger.error(traceback.format_exc())
            # Return a valid LineItemAnalysis object even in case of errors
            return LineItemAnalysis(
                items=[],
                total_found=0,
                subtotal=None,
                tax=None,
                total=None,
                discrepancies=["Line item processing failed: " + str(e)],
                reasoning=f"Processing failed due to error: {str(e)}",
                word_labels={},
            )

    def analyze_line_items(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        places_api_data: Optional[Dict] = None,
        structure_analysis=None,
    ) -> LineItemAnalysis:
        """
        Analyze line items on a receipt.

        Args:
            receipt: Receipt object
            receipt_lines: List of receipt lines
            receipt_words: List of receipt words
            places_api_data: Optional Places API data
            structure_analysis: Optional structure analysis results to help locate sections

        Returns:
            LineItemAnalysis object with extracted line items
        """
        try:
            # Process the receipt using the new process method
            return self.process(receipt, receipt_lines, receipt_words)

        except Exception as e:
            logger.error("Error analyzing line items: %s", str(e))
            logger.error(traceback.format_exc())

            # Return an empty analysis in case of errors
            return LineItemAnalysis(
                items=[],
                total_found=0,
                subtotal=None,
                tax=None,
                total=None,
                discrepancies=["Line item analysis failed: " + str(e)],
                reasoning=f"Analysis failed due to error: {str(e)}",
                word_labels={},
            )

    def _handle_uncertainties(
        self,
        llm_processor: LLMProcessor,
        uncertain_items: List[UncertaintyItem],
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        results: ProcessingResult,
    ) -> Dict:
        """
        Handle uncertain items by using LLM processing if needed.

        Args:
            llm_processor: The LLM processor to use
            uncertain_items: List of uncertain items to handle
            receipt: Receipt object
            receipt_lines: List of receipt lines
            receipt_words: List of receipt words
            results: Current processing results

        Returns:
            Dictionary with updates to apply to the results
        """
        if not uncertain_items or not llm_processor:
            return {}

        try:
            # Call the LLM processor to handle uncertain items
            llm_updates = llm_processor.process_uncertain_items(
                uncertain_items=uncertain_items,
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                initial_state={
                    "line_items": results.line_items,
                    "subtotal": results.subtotal,
                    "tax": results.tax,
                    "total": results.total,
                },
                places_api_data={},
            )

            return llm_updates
        except Exception as e:
            logger.error("Error during LLM processing: %s", str(e))
            logger.error(traceback.format_exc())
            return {}

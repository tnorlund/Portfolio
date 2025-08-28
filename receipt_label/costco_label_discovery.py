#!/usr/bin/env python3
"""
COSTCO Label Discovery using LangChain
=====================================

This script uses LangChain to identify GRAND_TOTAL, TAX, LINE_TOTAL, and SUBTOTAL
labels from raw OCR text WITHOUT relying on existing labels.

Approach:
1. Reconstruct receipt text from OCR words
2. Use LLM to identify currency amounts and classify them
3. Validate using known totals and arithmetic relationships
"""

import asyncio
import json
import logging
import os
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pydantic import BaseModel, Field
from enum import Enum
import re
from datetime import datetime
from receipt_label.prompt_formatting import format_receipt_lines_visual_order

try:
    from langchain_core.output_parsers import PydanticOutputParser
    from langchain_core.prompts import PromptTemplate
    from receipt_label.langchain_validation.ollama_turbo_client import (
        create_ollama_turbo_client,
    )

    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    print(
        "⚠️ LangChain not available - install with: pip install langchain-core"
    )

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.entities import ReceiptWord, ReceiptLine
from receipt_label.constants import CORE_LABELS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LabelType(str, Enum):
    """Currency label types to identify."""

    GRAND_TOTAL = "GRAND_TOTAL"
    TAX = "TAX"
    LINE_TOTAL = "LINE_TOTAL"
    SUBTOTAL = "SUBTOTAL"


@dataclass
class ReceiptTextGroup:
    """A group of visually contiguous receipt lines."""

    line_ids: List[int]
    text: str
    centroid_y: float  # Y coordinate of the visual group center


@dataclass
class SpatialMarker:
    """Spatial position marker for receipt analysis."""

    position_percent: float  # 0.0 = top, 1.0 = bottom
    description: str  # "TOP", "MIDDLE", "BOTTOM"


class CurrencyLabel(BaseModel):
    """A discovered currency label with LLM reasoning."""

    word_text: str = Field(description="The exact text of the currency amount")
    label_type: LabelType = Field(description="The classified label type")
    line_number: int = Field(description="Line number in the receipt")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this classification"
    )
    reasoning: str = Field(
        description="Explanation for why this classification was chosen"
    )
    value: float = Field(description="Numeric value extracted from the text")
    position_y: float = Field(
        description="Relative position on receipt (0.0=top, 1.0=bottom)"
    )
    context: str = Field(description="Surrounding text context")


class ReceiptAnalysis(BaseModel):
    """Complete analysis results for a receipt."""

    receipt_id: str
    known_total: float
    discovered_labels: List[CurrencyLabel]
    validation_results: (
        Dict  # Allow any values including None for missing validations
    )
    total_lines: int
    currency_amounts_found: int


class ReceiptTextReconstructor:
    """Reconstructs readable receipt text from ReceiptLine entities using proper grouping."""

    def format_receipt_lines_exactly(self, lines: List[ReceiptLine]) -> str:
        """
        Format receipt text by grouping visually contiguous lines and
        prefixing each group with its line ID or ID range.
        Exact replication of _format_receipt_lines from completion/_format_prompt.py
        """
        if not lines:
            return ""

        # Sort lines by line_id to ensure proper order
        sorted_lines = sorted(lines, key=lambda x: x.line_id)

        # Helper to format ID or ID range
        def format_ids(ids: list[int]) -> str:
            if len(ids) == 1:
                return f"{ids[0]}:"
            return f"{ids[0]}-{ids[-1]}:"

        # Initialize first group
        grouped: list[tuple[list[int], str]] = []
        current_ids = [sorted_lines[0].line_id]
        current_text = sorted_lines[0].text

        for prev_line, curr_line in zip(sorted_lines, sorted_lines[1:]):
            curr_id = curr_line.line_id
            centroid = curr_line.calculate_centroid()
            # Decide if on same visual line as previous
            if (
                prev_line.bottom_left["y"]
                < centroid[1]
                < prev_line.top_left["y"]
            ):
                # Same group: append text
                current_ids.append(curr_id)
                current_text += " " + curr_line.text
            else:
                # Flush previous group
                grouped.append((current_ids, current_text))
                # Start new group
                current_ids = [curr_id]
                current_text = curr_line.text

        # Flush final group
        grouped.append((current_ids, current_text))

        # Build formatted lines
        formatted_lines = [
            f"{format_ids(ids)} {text}" for ids, text in grouped
        ]
        return "\n".join(formatted_lines)

    def reconstruct_receipt(
        self, lines: List[ReceiptLine]
    ) -> Tuple[str, List[ReceiptTextGroup]]:
        """
        Reconstruct receipt text from ReceiptLine entities using _format_receipt_lines approach.
        Groups visually contiguous lines based on Y coordinates.

        Returns:
            Tuple of (formatted_text, list_of_text_groups)
        """
        if not lines:
            return "", []

        # Sort lines by Y position (top to bottom) then by line_id
        sorted_lines = sorted(
            lines, key=lambda l: (-l.calculate_centroid()[1], l.line_id)
        )

        # Group visually contiguous lines using the same logic as _format_receipt_lines
        grouped = self._group_visual_lines(sorted_lines)

        # Build formatted text with line ID ranges
        formatted_lines = []
        for ids, text in grouped:
            if len(ids) == 1:
                formatted_lines.append(f"{ids[0]}: {text}")
            else:
                formatted_lines.append(f"{ids[0]}-{ids[-1]}: {text}")

        formatted_text = "\n".join(formatted_lines)

        # Convert to ReceiptTextGroup objects
        text_groups = []
        for ids, text in grouped:
            # Calculate average Y position for this group
            group_lines = [
                line for line in sorted_lines if line.line_id in ids
            ]
            avg_y = sum(
                line.calculate_centroid()[1] for line in group_lines
            ) / len(group_lines)

            text_groups.append(
                ReceiptTextGroup(line_ids=ids, text=text, centroid_y=avg_y)
            )

        return formatted_text, text_groups

    def _group_visual_lines(
        self, lines: List[ReceiptLine]
    ) -> List[Tuple[List[int], str]]:
        """Group visually contiguous lines based on Y coordinate overlap."""
        if not lines:
            return []

        # Sort lines by line_id to ensure proper order
        sorted_lines = sorted(lines, key=lambda x: x.line_id)

        # Initialize first group
        grouped = []
        current_ids = [sorted_lines[0].line_id]
        current_text = sorted_lines[0].text

        for prev_line, curr_line in zip(sorted_lines, sorted_lines[1:]):
            curr_id = curr_line.line_id
            centroid = curr_line.calculate_centroid()

            # Decide if on same visual line as previous
            if (
                prev_line.bottom_left["y"]
                < centroid[1]
                < prev_line.top_left["y"]
            ):
                # Same group: append text
                current_ids.append(curr_id)
                current_text += " " + curr_line.text
            else:
                # Flush previous group
                grouped.append((current_ids, current_text))
                # Start new group
                current_ids = [curr_id]
                current_text = curr_line.text

        # Flush final group
        grouped.append((current_ids, current_text))

        return grouped

    def add_spatial_markers(
        self, formatted_text: str, text_groups: List[ReceiptTextGroup]
    ) -> str:
        """Add spatial position markers to help LLM understand receipt layout."""
        if not text_groups:
            return formatted_text

        # Calculate spatial regions
        min_y = min(group.centroid_y for group in text_groups)
        max_y = max(group.centroid_y for group in text_groups)
        y_range = max_y - min_y if max_y > min_y else 1.0

        # Add markers at key positions
        text_lines = formatted_text.split("\n")
        enhanced_lines = []

        for i, (text_line, group) in enumerate(zip(text_lines, text_groups)):
            # Calculate relative position (0.0 = top, 1.0 = bottom)
            relative_pos = (
                (group.centroid_y - min_y) / y_range if y_range > 0 else 0.0
            )

            # Add spatial markers
            if relative_pos <= 0.2:
                marker = "📄 TOP"
            elif relative_pos >= 0.8:
                marker = "📄 BOTTOM"
            elif 0.4 <= relative_pos <= 0.6:
                marker = "📄 MIDDLE"
            else:
                marker = ""

            if marker:
                enhanced_lines.append(f"{text_line} {marker}")
            else:
                enhanced_lines.append(text_line)

        return "\n".join(enhanced_lines)

    def extract_currency_context(
        self, text_groups: List[ReceiptTextGroup]
    ) -> List[Dict]:
        """Extract all currency amounts with their context from grouped text."""
        import re

        currency_pattern = re.compile(r"\$?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)")
        currency_contexts = []

        for group_idx, group in enumerate(text_groups):
            matches = currency_pattern.finditer(group.text)

            for match in matches:
                try:
                    # Extract numeric value
                    value_text = match.group(0)
                    value = float(value_text.replace("$", "").replace(",", ""))

                    # Get context (surrounding text)
                    start = max(0, match.start() - 15)
                    end = min(len(group.text), match.end() + 15)
                    context = group.text[start:end]

                    currency_contexts.append(
                        {
                            "text": value_text,
                            "value": value,
                            "group_number": group_idx + 1,
                            "line_ids": group.line_ids,
                            "context": context,
                            "full_line": group.text,
                            "y_position": group.centroid_y,
                        }
                    )
                except ValueError:
                    continue

        return currency_contexts


def create_costco_labeling_prompt(
    receipt_text: str, known_total: float, currency_contexts: List[Dict]
) -> str:
    """Create a specialized prompt for COSTCO receipt labeling."""

    prompt = f"""You are analyzing a COSTCO receipt to identify currency labels from OCR text.

COSTCO RECEIPT PATTERNS:
- GRAND_TOTAL: Final total including tax (appears multiple times, usually at ~85% down receipt)  
- TAX: Sales tax amount (6-12% rate typical for COSTCO, appears at ~70% down receipt)
- LINE_TOTAL: Individual item/group prices (scattered throughout receipt)
- SUBTOTAL: Pre-tax total (OFTEN MISSING on COSTCO receipts)

KNOWN INFORMATION:
- This receipt's grand total should be: ${known_total:.2f}
- You MUST identify this exact amount as GRAND_TOTAL

RECEIPT TEXT:
{receipt_text}

CURRENCY AMOUNTS FOUND:
"""

    for i, ctx in enumerate(currency_contexts, 1):
        line_range = (
            f"{ctx['line_ids'][0]}-{ctx['line_ids'][-1]}"
            if len(ctx["line_ids"]) > 1
            else str(ctx["line_ids"][0])
        )
        prompt += f"{i}. {ctx['text']} (${ctx['value']:.2f}) on lines {line_range}: '{ctx['full_line']}'\n"

    prompt += f"""
INSTRUCTIONS:
1. Identify which currency amount is the GRAND_TOTAL (must be ${known_total:.2f})
2. Find the TAX amount (should be 6-12% of subtotal)
3. Identify LINE_TOTAL amounts (individual items/groups)
4. Look for SUBTOTAL if present (may be missing)
5. Verify arithmetic: SUBTOTAL + TAX = GRAND_TOTAL (or SUM(LINE_TOTAL) + TAX = GRAND_TOTAL if no subtotal)

For each currency amount, classify it and explain your reasoning based on:
- Position on receipt (top/middle/bottom)
- Surrounding text context
- Mathematical relationships
- COSTCO receipt patterns

Return your analysis in this format:
GRAND_TOTAL: [amount] - [reasoning]
TAX: [amount] - [reasoning]
LINE_TOTAL: [amount1, amount2, ...] - [reasoning for each]
SUBTOTAL: [amount or "NOT FOUND"] - [reasoning]

VALIDATION:
- Does SUBTOTAL + TAX = GRAND_TOTAL?
- Or does SUM(LINE_TOTAL) + TAX ≈ GRAND_TOTAL?
- Is tax rate reasonable (6-12%)?
"""

    return prompt


# Define structured response models for currency classification
class CurrencyClassificationItem(BaseModel):
    """Individual currency classification item."""

    amount: float = Field(description="The currency amount value")
    type: LabelType = Field(
        description="The label type (GRAND_TOTAL, TAX, LINE_TOTAL, SUBTOTAL)"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this classification"
    )
    reasoning: Optional[str] = Field(
        description="Brief reasoning for this classification"
    )


class CurrencyClassificationResponse(BaseModel):
    """Structured response for currency classification."""

    labels: List[CurrencyClassificationItem] = Field(
        description="List of currency classifications"
    )


async def analyze_with_ollama(
    formatted_receipt_text: str,
    currency_contexts: List[Dict],
    known_total: float,
    receipt_id: str = None,
) -> List[CurrencyLabel]:
    """Use Ollama LLM with LangChain structured output to analyze and classify currency amounts."""

    if not LANGCHAIN_AVAILABLE:
        return []

    # Create authenticated Ollama Turbo client using the helper function
    llm = create_ollama_turbo_client(
        model="gpt-oss:20b",
        base_url="https://ollama.com",
        api_key=os.getenv("OLLAMA_API_KEY"),
        temperature=0.0,
    )

    # Create Pydantic output parser
    output_parser = PydanticOutputParser(
        pydantic_object=CurrencyClassificationResponse
    )

    # Focus on most relevant currency amounts (known total and nearby values)
    relevant_contexts = []

    # Always include the known total
    for ctx in currency_contexts:
        if abs(ctx["value"] - known_total) < 0.01:
            relevant_contexts.append(ctx)

    # Add potential tax amounts (5-20% of known total)
    tax_min, tax_max = known_total * 0.05, known_total * 0.20
    for ctx in currency_contexts:
        if tax_min <= ctx["value"] <= tax_max and ctx not in relevant_contexts:
            relevant_contexts.append(ctx)

    # Add some item-level amounts (under 50% of total)
    item_max = known_total * 0.5
    item_contexts = [
        ctx
        for ctx in currency_contexts
        if ctx["value"] < item_max and ctx not in relevant_contexts
    ]
    relevant_contexts.extend(item_contexts[:10])  # Limit to 10 items

    # We need to get the lines and format them properly like _format_receipt_lines
    # The prompt parameter is the old approach - we need to get lines from the caller
    # For now, let's extract what we can from the existing prompt, but ideally we'd pass the lines directly

    # Extract relevant currency amounts for targeting
    relevant_amounts = [f"${ctx['value']:.2f}" for ctx in relevant_contexts]
    amounts_text = ", ".join(
        relevant_amounts[:15]
    )  # Show more amounts for context

    # Create prompt template using the properly formatted receipt text
    prompt_template = PromptTemplate(
        template="""You are analyzing a COSTCO receipt to classify currency amounts. 

RECEIPT TEXT (formatted with line IDs):
{receipt_text}

TARGET CURRENCY AMOUNTS: {target_amounts}
KNOWN GRAND TOTAL: ${known_total:.2f}

LABEL DEFINITIONS (from receipt_label.constants):
- GRAND_TOTAL: {grand_total_def} (must equal ${known_total:.2f})
- TAX: {tax_def}
- LINE_TOTAL: {line_total_def}  
- SUBTOTAL: {subtotal_def}

ARITHMETIC VALIDATION:
The correct receipt arithmetic should follow:
1. SUM(LINE_TOTAL) + TAX = GRAND_TOTAL
2. SUBTOTAL + TAX = GRAND_TOTAL (where SUBTOTAL = SUM(LINE_TOTAL))

INSTRUCTIONS:
Analyze the complete receipt text above. The text is formatted with line IDs (e.g., "5:", "12-15:") showing how OCR lines are grouped.

Look for currency amounts and classify ONLY the target amounts listed above as GRAND_TOTAL, TAX, LINE_TOTAL, or SUBTOTAL based on:

1. **Surrounding text context**: Look for keywords like "TOTAL", "TAX", "SUBTOTAL", product names
2. **Line position**: Earlier lines are usually items, later lines are totals
3. **COSTCO patterns**: 
   - Tax is often $0.00 
   - Grand total appears multiple times (receipt total, payment confirmation)
   - Subtotal may be missing or equal to grand total when tax is $0.00
   - Line totals are individual item prices

4. **Arithmetic validation**: Ensure the relationships make sense

Focus ONLY on the target amounts. Do not classify amounts not in the target list.

{format_instructions}""",
        input_variables=["receipt_text", "target_amounts"],
        partial_variables={
            "format_instructions": output_parser.get_format_instructions(),
            "known_total": known_total,
            "grand_total_def": CORE_LABELS["GRAND_TOTAL"],
            "tax_def": CORE_LABELS["TAX"],
            "line_total_def": CORE_LABELS["LINE_TOTAL"],
            "subtotal_def": CORE_LABELS["SUBTOTAL"],
        },
    )

    # Create the chain: prompt | llm | output_parser
    chain = prompt_template | llm | output_parser

    try:
        print(
            f"\n🤖 Calling Ollama Turbo gpt-oss:20b with PydanticOutputParser..."
        )
        print(f"Analyzing {len(relevant_contexts)} key currency amounts")

        # Execute the chain with metadata for LangSmith tracing
        response = await chain.ainvoke(
            {
                "receipt_text": formatted_receipt_text,
                "target_amounts": amounts_text,
            },
            config={
                "metadata": {
                    "receipt_type": "COSTCO",
                    "receipt_id": receipt_id or "unknown",
                    "known_total": known_total,
                    "currency_amounts_analyzed": len(relevant_contexts),
                    "use_case": "currency_label_classification",
                },
                "tags": [
                    "receipt-labeling",
                    "currency-classification",
                    "costco",
                    "ollama-turbo",
                ],
            },
        )

        print(
            f"\n✅ Structured response received with {len(response.labels)} classifications"
        )

        # Convert to CurrencyLabel objects
        discovered_labels = []

        for item in response.labels:
            # Find matching currency context
            matching_ctx = None
            target_amount = float(item.amount)

            for ctx in relevant_contexts:
                if abs(target_amount - ctx["value"]) < 0.01:
                    matching_ctx = ctx
                    break

            if matching_ctx:
                try:
                    label = CurrencyLabel(
                        word_text=f"${matching_ctx['value']:.2f}",
                        label_type=item.type,
                        line_number=matching_ctx["group_number"],
                        confidence=item.confidence,
                        reasoning=item.reasoning
                        or f"LLM classified as {item.type.value}",
                        value=matching_ctx["value"],
                        position_y=matching_ctx["y_position"],
                        context=matching_ctx["context"],
                    )
                    discovered_labels.append(label)
                    print(
                        f"   📄 {item.type.value}: ${item.amount:.2f} (confidence: {item.confidence:.2f})"
                    )
                except Exception as e:
                    print(f"⚠️ Error creating label for ${item.amount}: {e}")
                    continue
            else:
                print(f"⚠️ No matching context found for ${item.amount:.2f}")

        print(
            f"\n✅ Successfully created {len(discovered_labels)} currency labels"
        )
        return discovered_labels

    except Exception as e:
        print(f"\n❌ LangChain structured analysis failed: {e}")
        logger.error(f"LangChain analysis error: {e}", exc_info=True)
        return []


def validate_arithmetic_relationships(
    discovered_labels: List[CurrencyLabel], known_total: float
) -> Dict[str, bool]:
    """
    Validate arithmetic relationships between discovered labels.

    Returns:
        Dict with validation results for different arithmetic checks
    """
    # Group labels by type
    labels_by_type = {label_type: [] for label_type in LabelType}
    for label in discovered_labels:
        labels_by_type[label.label_type].append(label)

    validation_results = {}

    # Get totals for each type
    grand_totals = [
        label.value for label in labels_by_type[LabelType.GRAND_TOTAL]
    ]
    subtotals = [label.value for label in labels_by_type[LabelType.SUBTOTAL]]
    taxes = [label.value for label in labels_by_type[LabelType.TAX]]
    line_totals = [
        label.value for label in labels_by_type[LabelType.LINE_TOTAL]
    ]

    print(f"\n📊 ARITHMETIC VALIDATION:")
    print(
        f"   GRAND_TOTAL found: {len(grand_totals)} (${', '.join(f'{x:.2f}' for x in grand_totals)})"
    )
    print(
        f"   SUBTOTAL found: {len(subtotals)} (${', '.join(f'{x:.2f}' for x in subtotals)})"
    )
    print(
        f"   TAX found: {len(taxes)} (${', '.join(f'{x:.2f}' for x in taxes)})"
    )
    print(
        f"   LINE_TOTAL found: {len(line_totals)} (sum: ${sum(line_totals):.2f})"
    )

    # Check 1: Grand total matches known total
    validation_results["grand_total_matches_known"] = any(
        abs(gt - known_total) < 0.01 for gt in grand_totals
    )

    # Check 2: SUM(LINE_TOTAL) + TAX = GRAND_TOTAL (primary check)
    line_total_sum = sum(line_totals) if line_totals else 0

    if line_totals and taxes and grand_totals:
        best_match = None
        for tax in taxes:
            for grand_total in grand_totals:
                calculated = line_total_sum + tax
                if abs(calculated - grand_total) < 0.01:
                    best_match = {
                        "line_total_sum": line_total_sum,
                        "tax": tax,
                        "grand_total": grand_total,
                        "calculated": calculated,
                    }
                    break
            if best_match:
                break

        validation_results["line_totals_plus_tax_equals_grand_total"] = (
            best_match is not None
        )
        if best_match:
            print(
                f"   ✅ Arithmetic: ${best_match['line_total_sum']:.2f} (line totals) + ${best_match['tax']:.2f} (tax) = ${best_match['grand_total']:.2f} (grand total)"
            )
        else:
            print(
                f"   ❌ No valid SUM(LINE_TOTAL) + TAX = GRAND_TOTAL combination found"
            )
            print(
                f"      Tried: ${line_total_sum:.2f} + {', '.join(f'${x:.2f}' for x in taxes)} vs {', '.join(f'${x:.2f}' for x in grand_totals)}"
            )
    else:
        validation_results["line_totals_plus_tax_equals_grand_total"] = None
        print(
            f"   ⚠️ Missing data for SUM(LINE_TOTAL) + TAX = GRAND_TOTAL check"
        )

    # Check 3: SUBTOTAL + TAX = GRAND_TOTAL (if subtotal is present)
    if subtotals and taxes and grand_totals:
        best_match = None
        for subtotal in subtotals:
            for tax in taxes:
                for grand_total in grand_totals:
                    calculated = subtotal + tax
                    if abs(calculated - grand_total) < 0.01:
                        best_match = {
                            "subtotal": subtotal,
                            "tax": tax,
                            "grand_total": grand_total,
                            "calculated": calculated,
                        }
                        break
                if best_match:
                    break
            if best_match:
                break

        validation_results["subtotal_plus_tax_equals_total"] = (
            best_match is not None
        )
        if best_match:
            print(
                f"   ✅ Alternative arithmetic: ${best_match['subtotal']:.2f} (subtotal) + ${best_match['tax']:.2f} (tax) = ${best_match['grand_total']:.2f} (grand total)"
            )
        else:
            print(
                f"   ❌ No valid SUBTOTAL + TAX = GRAND_TOTAL combination found"
            )
    else:
        validation_results["subtotal_plus_tax_equals_total"] = None
        print(f"   ⚠️ Missing data for SUBTOTAL + TAX = GRAND_TOTAL check")

    # Check 4: Sum of line totals equals subtotal (if subtotal is present)
    if line_totals and subtotals:
        best_subtotal_match = min(
            subtotals, key=lambda x: abs(x - line_total_sum)
        )
        difference = abs(best_subtotal_match - line_total_sum)

        # Allow up to 10% difference (some items might not be captured)
        tolerance = max(
            best_subtotal_match * 0.10, 2.0
        )  # At least $2 tolerance
        validation_results["line_totals_approximate_subtotal"] = (
            difference <= tolerance
        )

        print(
            f"   📋 Line totals sum ${line_total_sum:.2f} vs subtotal ${best_subtotal_match:.2f} (diff: ${difference:.2f}, tolerance: ${tolerance:.2f})"
        )
        if validation_results["line_totals_approximate_subtotal"]:
            print(f"   ✅ Line totals reasonably close to subtotal")
        else:
            print(
                f"   ⚠️ Line totals differ significantly from subtotal (missing items?)"
            )
    else:
        validation_results["line_totals_approximate_subtotal"] = None
        print(f"   ⚠️ Missing data for line totals vs subtotal check")

    # Check 5: Tax rate reasonableness (6-12% typical for COSTCO)
    if taxes and subtotals:
        for tax in taxes:
            for subtotal in subtotals:
                if subtotal > 0:
                    tax_rate = (tax / subtotal) * 100
                    validation_results["reasonable_tax_rate"] = (
                        0 <= tax_rate <= 15
                    )  # Allow 0% to 15%
                    print(
                        f"   💰 Tax rate: {tax_rate:.1f}% (${tax:.2f} / ${subtotal:.2f})"
                    )
                    break
    else:
        validation_results["reasonable_tax_rate"] = None

    return validation_results


async def analyze_costco_receipt(
    client: DynamoClient, image_id: str, receipt_id: int, known_total: float
) -> ReceiptAnalysis:
    """Analyze a single COSTCO receipt to discover labels."""

    receipt_identifier = f"{image_id}/{receipt_id}"

    print(f"\n🔍 ANALYZING COSTCO RECEIPT: {receipt_identifier}")
    print(f"   Known GRAND_TOTAL: ${known_total:.2f}")
    print("-" * 60)

    # Get all lines for this receipt
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
    print(f"📊 Found {len(lines)} lines")

    # Reconstruct receipt text using proper line grouping
    reconstructor = ReceiptTextReconstructor()
    formatted_text, text_groups = reconstructor.reconstruct_receipt(lines)

    # Generate clean formatted receipt text exactly like _format_receipt_lines
    # formatted_receipt_text = reconstructor.format_receipt_lines_exactly(lines)
    formatted_receipt_text = format_receipt_lines_visual_order(lines)

    print(
        f"📄 Reconstructed {len(text_groups)} visual groups from {len(lines)} lines"
    )

    # Add spatial markers
    enhanced_text = reconstructor.add_spatial_markers(
        formatted_text, text_groups
    )

    # Extract currency amounts
    currency_contexts = reconstructor.extract_currency_context(text_groups)
    print(f"💰 Found {len(currency_contexts)} currency amounts")

    # Show currency amounts found
    print("\n💹 Currency amounts discovered:")
    for ctx in currency_contexts:
        if text_groups:
            pos_pct = (
                (ctx["y_position"] - text_groups[0].centroid_y)
                / (text_groups[-1].centroid_y - text_groups[0].centroid_y)
                if len(text_groups) > 1
                else 0
            )
        else:
            pos_pct = 0
        line_range = (
            f"{ctx['line_ids'][0]}-{ctx['line_ids'][-1]}"
            if len(ctx["line_ids"]) > 1
            else str(ctx["line_ids"][0])
        )
        print(
            f"   ${ctx['value']:>8.2f} at {pos_pct:>5.1%} down - Group {ctx['group_number']} (lines {line_range}): '{ctx['full_line'][:50]}{'...' if len(ctx['full_line']) > 50 else ''}'"
        )

    # Check if known total is found
    known_total_found = any(
        abs(ctx["value"] - known_total) < 0.01 for ctx in currency_contexts
    )
    print(
        f"\n🎯 Known total ${known_total:.2f}: {'✅ FOUND' if known_total_found else '❌ NOT FOUND'}"
    )

    # Create the analysis prompt
    prompt = create_costco_labeling_prompt(
        enhanced_text, known_total, currency_contexts
    )

    print("\n📝 LANGCHAIN PROMPT READY:")
    print("=" * 50)
    print(prompt[:500] + "..." if len(prompt) > 500 else prompt)
    print("=" * 50)

    # Integrate actual LLM analysis
    discovered_labels = []
    arithmetic_validation = {}

    if LANGCHAIN_AVAILABLE and currency_contexts:
        try:
            discovered_labels = await analyze_with_ollama(
                formatted_receipt_text,
                currency_contexts,
                known_total,
                receipt_identifier,
            )
            print(
                f"\n🤖 LLM ANALYSIS COMPLETE: {len(discovered_labels)} labels discovered"
            )

            # Validate arithmetic relationships
            if discovered_labels:
                arithmetic_validation = validate_arithmetic_relationships(
                    discovered_labels, known_total
                )

        except Exception as e:
            print(f"\n❌ LLM analysis failed: {e}")
            logger.error(f"LLM analysis error: {e}")

    # Combine all validation results
    all_validation_results = {
        "known_total_found": known_total_found,
        "text_reconstructed": True,
        "currency_amounts_extracted": len(currency_contexts) > 0,
        "llm_analysis_successful": len(discovered_labels) > 0,
        **arithmetic_validation,  # Include arithmetic validation results
    }

    return ReceiptAnalysis(
        receipt_id=f"{image_id}/{receipt_id}",
        known_total=known_total,
        discovered_labels=discovered_labels,
        validation_results=all_validation_results,
        total_lines=len(text_groups),
        currency_amounts_found=len(currency_contexts),
    )


def setup_langsmith_tracing():
    """Setup LangSmith tracing for LLM interactions."""
    langchain_api_key = os.getenv("LANGCHAIN_API_KEY")

    if langchain_api_key:
        # Set environment variables for LangSmith tracing
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = langchain_api_key
        os.environ["LANGCHAIN_PROJECT"] = os.getenv(
            "LANGCHAIN_PROJECT", "receipt-word-label-description"
        )

        project_name = os.environ["LANGCHAIN_PROJECT"]
        print("🔍 LangSmith tracing enabled")
        print(f"   Project: {project_name}")
        print(
            f"   API key: {langchain_api_key[:20]}..."
            if len(langchain_api_key) > 20
            else langchain_api_key
        )
    else:
        print("⚠️ LANGCHAIN_API_KEY not found - tracing disabled")


async def main():
    """Analyze all 4 COSTCO receipts to discover labels."""

    print("🏪 COSTCO LABEL DISCOVERY USING LANGCHAIN")
    print("=" * 80)
    print(
        "Discovering GRAND_TOTAL, TAX, LINE_TOTAL, SUBTOTAL from raw OCR text"
    )
    print()

    # Setup LangSmith tracing
    setup_langsmith_tracing()
    print()

    # Initialize client
    env_vars = load_env()
    client = DynamoClient(env_vars.get("dynamodb_table_name"))

    # COSTCO receipts with known totals
    costco_receipts = [
        ("6cd1f7f5-d988-4e11-9209-cb6535fc3b04", 1, 198.93),
        ("314ac65b-2b97-45d6-81c2-e48fb0b8cef4", 1, 87.71),
        ("a861f6a6-8d6d-42bc-907c-3330d8bd2022", 1, 35.68),
        ("8388d1f1-b5d6-4560-b7dc-db273815dda1", 1, 37.66),
    ]

    results = []

    # Analyze each receipt
    for image_id, receipt_id, known_total in costco_receipts:
        try:
            result = await analyze_costco_receipt(
                client, image_id, receipt_id, known_total
            )
            results.append(result)
        except Exception as e:
            logger.error(f"Error analyzing {image_id}/{receipt_id}: {e}")
            continue

    # Generate final output with discovered labels
    print("\n" + "=" * 80)
    print("📋 DISCOVERED LABELS FOR ALL 4 COSTCO RECEIPTS")
    print("=" * 80)

    total_labels_discovered = sum(len(r.discovered_labels) for r in results)
    total_currency_amounts = sum(r.currency_amounts_found for r in results)
    receipts_with_known_total = sum(
        1 for r in results if r.validation_results.get("known_total_found")
    )

    print(f"📊 SUMMARY:")
    print(f"   Receipts analyzed: {len(results)}/4")
    print(f"   Total currency amounts found: {total_currency_amounts}")
    print(f"   Labels discovered by LLM: {total_labels_discovered}")
    print(f"   Known totals found: {receipts_with_known_total}/4")
    print()

    for i, result in enumerate(results, 1):
        status = (
            "✅"
            if result.validation_results.get("known_total_found")
            else "❌"
        )
        print(f"🧾 RECEIPT {i}: {result.receipt_id}")
        print(f"   Status: {status} Known total ${result.known_total:.2f}")
        print(f"   Currency amounts: {result.currency_amounts_found}")
        print(f"   Labels discovered: {len(result.discovered_labels)}")

        # Group labels by type
        labels_by_type = {
            "GRAND_TOTAL": [],
            "SUBTOTAL": [],
            "TAX": [],
            "LINE_TOTAL": [],
        }
        for label in result.discovered_labels:
            labels_by_type[label.label_type.value].append(label)

        # Display discovered labels
        if result.discovered_labels:
            print(f"   📄 Discovered labels:")
            for label_type, labels in labels_by_type.items():
                if labels:
                    values = [f"${label.value:.2f}" for label in labels]
                    print(f"      {label_type}: {', '.join(values)}")

            # Show arithmetic validation results
            validation_results = result.validation_results
            if validation_results.get(
                "line_totals_plus_tax_equals_grand_total"
            ):
                print(f"   ✅ SUM(LINE_TOTAL) + TAX = GRAND_TOTAL validated")
            elif validation_results.get("subtotal_plus_tax_equals_total"):
                print(f"   ✅ SUBTOTAL + TAX = GRAND_TOTAL validated")
            else:
                print(f"   ⚠️ Arithmetic validation needs review")
        else:
            print(f"   ❌ No labels discovered by LLM")
        print()

    # Write summary output
    output_file = "costco_receipt_labels_discovered.txt"
    with open(output_file, "w") as f:
        f.write("COSTCO RECEIPT LABEL DISCOVERY RESULTS\n")
        f.write("=" * 50 + "\n")
        f.write(
            f"Analysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        f.write(
            f"LLM Used: Ollama Turbo gpt-oss:20b with LangChain PydanticOutputParser\n"
        )
        f.write(
            f"Method: Raw OCR text analysis using ReceiptLine entities\n\n"
        )

        f.write(f"SUMMARY:\n")
        f.write(f"  Receipts analyzed: {len(results)}/4\n")
        f.write(f"  Total currency amounts found: {total_currency_amounts}\n")
        f.write(f"  Labels discovered by LLM: {total_labels_discovered}\n")
        f.write(f"  Known totals found: {receipts_with_known_total}/4\n\n")

        for i, result in enumerate(results, 1):
            f.write(f"RECEIPT {i}: {result.receipt_id}\n")
            f.write(f"  Known total: ${result.known_total:.2f}\n")
            f.write(
                f"  Currency amounts found: {result.currency_amounts_found}\n"
            )
            f.write(f"  Labels discovered: {len(result.discovered_labels)}\n")

            if result.discovered_labels:
                f.write(f"  Discovered labels:\n")
                labels_by_type = {
                    "GRAND_TOTAL": [],
                    "SUBTOTAL": [],
                    "TAX": [],
                    "LINE_TOTAL": [],
                }
                for label in result.discovered_labels:
                    labels_by_type[label.label_type.value].append(label)

                for label_type, labels in labels_by_type.items():
                    if labels:
                        values = [
                            f"${label.value:.2f} (conf: {label.confidence:.2f})"
                            for label in labels
                        ]
                        f.write(f"    {label_type}: {', '.join(values)}\n")

                # Arithmetic validation
                validation = result.validation_results
                if validation.get("line_totals_plus_tax_equals_grand_total"):
                    f.write(
                        f"    ✅ SUM(LINE_TOTAL) + TAX = GRAND_TOTAL validated\n"
                    )
                elif validation.get("subtotal_plus_tax_equals_total"):
                    f.write(f"    ✅ SUBTOTAL + TAX = GRAND_TOTAL validated\n")
                else:
                    f.write(f"    ⚠️ Arithmetic validation needs review\n")
            else:
                f.write(f"    ❌ No labels discovered\n")
            f.write("\n")

    print(f"📄 Detailed results written to: {output_file}")
    print(f"\n✅ LABEL DISCOVERY COMPLETE!")
    print(
        f"   {total_labels_discovered} labels discovered across {len(results)} COSTCO receipts"
    )
    print(f"   Used LangChain structured output with PydanticOutputParser")
    print(f"   Arithmetic validation performed for all receipts")

    return results


if __name__ == "__main__":
    asyncio.run(main())

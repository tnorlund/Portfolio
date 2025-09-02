#!/usr/bin/env python3
"""
Two-phase line-item analysis approach.

Phase 1: Run existing currency analysis (GRAND_TOTAL, TAX, LINE_TOTAL, SUBTOTAL)
Phase 2: For each LINE_TOTAL found, do targeted line-item component analysis

This approach should be more focused and accurate than one-shot analysis.
"""

import os
import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from pydantic import BaseModel, Field

from receipt_label.receipt_models import CurrencyLabel, LabelType
from receipt_label.llm_classifier import analyze_with_ollama
from receipt_label.constants import CORE_LABELS
from receipt_dynamo.entities import ReceiptLine

logger = logging.getLogger(__name__)

try:
    from langchain_core.output_parsers import PydanticOutputParser
    from langchain_core.prompts import PromptTemplate
    from receipt_label.langchain_validation.ollama_turbo_client import (
        create_ollama_turbo_client,
    )
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False


@dataclass
class LineItemTarget:
    """A specific line that has LINE_TOTAL and needs component analysis."""
    line_ids: List[int]
    line_text: str
    line_total_amount: float
    line_total_label: CurrencyLabel


class LineItemComponents(BaseModel):
    """Components found for a specific line item."""
    
    product_name: Optional[str] = Field(default=None, description="Product description text")
    quantity: Optional[str] = Field(default=None, description="Quantity with units (e.g., '2 lb', '3', '1.5')")
    unit_price: Optional[float] = Field(default=None, description="Price per single unit")
    
    # Validation
    arithmetic_valid: bool = Field(description="Whether quantity × unit_price ≈ line_total")
    confidence: float = Field(ge=0.0, le=1.0, description="Overall confidence in analysis")
    reasoning: str = Field(description="Explanation of component identification")


class TargetedLineAnalysisResponse(BaseModel):
    """Structured response for analyzing a single line item."""
    
    line_analysis: LineItemComponents = Field(description="Components identified for this line")


async def two_phase_line_item_analysis(
    formatted_receipt_text: str,
    currency_contexts: List[Dict],
    lines: List[ReceiptLine] = None,
    receipt_id: str = None,
) -> List[CurrencyLabel]:
    """Two-phase approach: currency analysis first, then targeted line-item analysis."""
    
    print(f"\n🔄 STARTING TWO-PHASE LINE-ITEM ANALYSIS")
    print("=" * 60)
    
    # PHASE 1: Run existing currency analysis
    print("📊 PHASE 1: Currency Analysis (GRAND_TOTAL, TAX, LINE_TOTAL, SUBTOTAL)")
    currency_labels = await analyze_with_ollama(
        formatted_receipt_text,
        currency_contexts,
        receipt_id=receipt_id,
        lines=lines  # Pass ReceiptLine data for proper visual formatting
    )
    
    # Extract LINE_TOTAL labels for Phase 2
    line_total_labels = [label for label in currency_labels if label.label_type == LabelType.LINE_TOTAL]
    
    print(f"   ✅ Found {len(line_total_labels)} LINE_TOTAL amounts")
    for label in line_total_labels:
        print(f"      ${label.value:.2f} on lines {label.line_ids}")
    
    if not line_total_labels:
        print("   ⚠️ No LINE_TOTAL amounts found - skipping Phase 2")
        return currency_labels
    
    # PHASE 2: Targeted line-item component analysis
    print(f"\n🎯 PHASE 2: Targeted Line-Item Component Analysis")
    print(f"Analyzing {len(line_total_labels)} specific lines with LINE_TOTAL amounts")
    
    # Build targets for focused analysis
    if lines:
        # Use proper visual line formatting when original ReceiptLine data is available
        targets = _build_line_item_targets_with_visual_lines(lines, line_total_labels)
        print("   Using visual line formatting for better product name detection")
    else:
        # Fallback to the old approach
        targets = _build_line_item_targets(formatted_receipt_text, line_total_labels)
        print("   Using fallback context approach")
    
    # Analyze each target line individually
    line_item_labels = []
    for i, target in enumerate(targets, 1):
        print(f"\n   Analyzing target {i}/{len(targets)}: ${target.line_total_amount:.2f}")
        print(f"   Line text: '{target.line_text}'")
        
        try:
            components = await _analyze_single_line_item(target, receipt_id)
            if components:
                # Convert to CurrencyLabel objects
                target_labels = _convert_components_to_labels(target, components)
                line_item_labels.extend(target_labels)
                
                print(f"   ✅ Found: {len(target_labels)} components")
                for label in target_labels:
                    if label.label_type in [LabelType.PRODUCT_NAME, LabelType.QUANTITY]:
                        print(f"      {label.label_type.value}: '{label.word_text}'")
                    else:
                        print(f"      {label.label_type.value}: ${label.value:.2f}")
            else:
                print(f"   ⚠️ No components found")
                
        except Exception as e:
            logger.error(f"Error analyzing line item {i}: {e}")
            continue
    
    # Combine Phase 1 and Phase 2 results
    all_labels = currency_labels + line_item_labels
    
    print(f"\n📋 TWO-PHASE ANALYSIS COMPLETE")
    print(f"   Phase 1: {len(currency_labels)} currency labels")
    print(f"   Phase 2: {len(line_item_labels)} line-item component labels") 
    print(f"   Total: {len(all_labels)} labels discovered")
    
    return all_labels


def _build_line_item_targets_with_visual_lines(lines: List[ReceiptLine], line_total_labels: List[CurrencyLabel]) -> List[LineItemTarget]:
    """Build focused targets using proper visual line formatting."""
    
    from receipt_label.prompt_formatting.lines import format_receipt_lines_visual_order
    
    # Get properly formatted visual lines
    visual_receipt_text = format_receipt_lines_visual_order(lines)
    visual_lines = visual_receipt_text.split('\n')
    
    targets = []
    
    for label in line_total_labels:
        if not label.line_ids:
            continue
            
        # Find which visual line contains the currency amount
        amount_text = f"{label.value:.2f}"
        target_visual_line = None
        
        for visual_line in visual_lines:
            if amount_text in visual_line:
                target_visual_line = visual_line
                break
        
        if target_visual_line:
            target = LineItemTarget(
                line_ids=label.line_ids,
                line_text=target_visual_line,  # Complete visual line with product name and price
                line_total_amount=label.value,
                line_total_label=label
            )
            targets.append(target)
    
    return targets


def _build_line_item_targets(formatted_receipt_text: str, line_total_labels: List[CurrencyLabel]) -> List[LineItemTarget]:
    """Build focused targets for line-item analysis with surrounding context."""
    
    targets = []
    receipt_lines = formatted_receipt_text.split('\n')
    
    for label in line_total_labels:
        if not label.line_ids:
            continue
            
        # Find the line index containing this LINE_TOTAL
        target_line_idx = None
        target_line_text = None
        
        for i, line in enumerate(receipt_lines):
            # Check if this line contains any of the line_ids from the label
            for line_id in label.line_ids:
                if line.startswith(f"{line_id}:") or f"{line_id}-" in line or f"-{line_id}:" in line:
                    target_line_idx = i
                    # Extract the text part (after the line ID prefix)
                    if ':' in line:
                        target_line_text = line.split(':', 1)[1].strip()
                        break
            if target_line_idx is not None:
                break
        
        if target_line_idx is not None:
            # Smart context building: look for product fragments with consistent offset pattern
            enhanced_context = _build_smart_context(receipt_lines, target_line_idx, label.value)
            
            target = LineItemTarget(
                line_ids=label.line_ids,
                line_text=enhanced_context,  # Now includes surrounding context
                line_total_amount=label.value,
                line_total_label=label
            )
            targets.append(target)
    
    return targets


def _build_smart_context(receipt_lines: List[str], price_line_idx: int, price_value: float) -> str:
    """Build intelligent context by identifying product fragments that map to this price."""
    
    def extract_text(line: str) -> str:
        """Extract text part from formatted line."""
        if ':' in line:
            return line.split(':', 1)[1].strip()
        return line.strip()
    
    price_text = extract_text(receipt_lines[price_line_idx])
    
    # Sprouts receipt pattern analysis:
    # CHIPS(line 15) → 6.29(line 18) = offset 3
    # GARLIC(line 16) → 3.99(line 19) = offset 3  
    # SUGAR(line 17) → 6.49(line 20) = offset 3
    # Consistent pattern: product fragment appears exactly 3 lines before price
    
    primary_offset = 3
    primary_fragment_idx = price_line_idx - primary_offset
    
    if primary_fragment_idx >= 0:
        primary_fragment = extract_text(receipt_lines[primary_fragment_idx])
        
        # Check if this looks like a product fragment (alphabetic, not numeric)
        if (len(primary_fragment) > 2 and 
            any(char.isalpha() for char in primary_fragment) and
            not primary_fragment.replace('.', '').replace(',', '').isdigit()):
            
            # Look for additional fragments 1-2 lines before the primary fragment
            additional_fragments = []
            
            for extra_offset in [1, 2]:
                extra_idx = primary_fragment_idx - extra_offset
                if extra_idx >= 0:
                    extra_text = extract_text(receipt_lines[extra_idx])
                    if (len(extra_text) > 2 and 
                        any(char.isalpha() for char in extra_text) and 
                        not extra_text.replace('.', '').replace(',', '').isdigit() and
                        extra_text not in ['SPROUTS', 'MARKET', 'BLVD', 'Store']):  # Skip header words
                        additional_fragments.append(extra_text)
            
            # Build complete product name from fragments
            if additional_fragments:
                # Reverse to get natural reading order (first fragment first)
                all_fragments = list(reversed(additional_fragments)) + [primary_fragment]
                product_name = " ".join(all_fragments)
            else:
                product_name = primary_fragment
            
            return f"PRODUCT: {product_name} → [PRICE: {price_text}]"
    
    # Fallback: use simple context window if pattern doesn't match
    context_start = max(0, price_line_idx - 2)
    context_parts = []
    for i in range(context_start, price_line_idx + 1):
        text = extract_text(receipt_lines[i])
        if i == price_line_idx:
            context_parts.append(f"[PRICE: {text}]")
        else:
            context_parts.append(text)
    return " | ".join(context_parts)


async def _analyze_single_line_item(target: LineItemTarget, receipt_id: str = None) -> Optional[LineItemComponents]:
    """Analyze a single line item for PRODUCT_NAME, QUANTITY, UNIT_PRICE components."""
    
    if not LANGCHAIN_AVAILABLE:
        return None
    
    # Create LLM client
    llm = create_ollama_turbo_client(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        api_key=os.getenv("OLLAMA_API_KEY"),
        temperature=0.0,
    )
    
    # Create output parser
    output_parser = PydanticOutputParser(pydantic_object=TargetedLineAnalysisResponse)
    
    # Adaptive prompt template that handles both visual lines and context fragments
    template = """You are analyzing a line item from a receipt to identify its components.

LINE TO ANALYZE:
Line IDs: {line_ids}
Line Text: "{line_text}"
Known LINE_TOTAL: ${line_total:.2f}

COMPONENT DEFINITIONS:
- PRODUCT_NAME: {product_name_def}
- QUANTITY: {quantity_def} 
- UNIT_PRICE: {unit_price_def}

TASK:
Analyze the line to identify components for the item with LINE_TOTAL ${line_total:.2f}:

1. **PRODUCT_NAME**: The item description (e.g., "MILK CHOCOLATE CHIPS", "ORGANIC GARLIC POWDER")
2. **QUANTITY**: The amount purchased (e.g., "2", "1 lb", "1.5")  
3. **UNIT_PRICE**: The price per unit (if present)

LINE FORMAT UNDERSTANDING:
The line text may be in one of these formats:

FORMAT A - Complete Visual Line:
"MILK CHOCOLATE CHIPS 6.29" or "ORGANIC BANANAS 2 lb @ 1.99 3.98"
→ Product name appears first, followed by quantity/price info

FORMAT B - Context Fragments:
"PRODUCT: MILK CHOCOLATE CHIPS → [PRICE: 6.29]"
→ Reconstructed from OCR fragments

EXTRACTION STRATEGY:
- Look for descriptive text (alphabetic words) as PRODUCT_NAME candidates
- Look for numeric values with units (lb, oz, ea) as QUANTITY
- Look for prices with @ symbol as UNIT_PRICE
- The LINE_TOTAL (${line_total:.2f}) should appear in the text

ARITHMETIC VALIDATION:
If QUANTITY and UNIT_PRICE are found: QUANTITY × UNIT_PRICE ≈ LINE_TOTAL (${line_total:.2f})

EXAMPLES:
Line: "MILK CHOCOLATE CHIPS 6.29"
LINE_TOTAL: $6.29
→ PRODUCT_NAME: "MILK CHOCOLATE CHIPS"
→ QUANTITY: null
→ UNIT_PRICE: null

Line: "ORGANIC BANANAS 2 lb @ 1.99 3.98"
LINE_TOTAL: $3.98
→ PRODUCT_NAME: "ORGANIC BANANAS"
→ QUANTITY: "2 lb"
→ UNIT_PRICE: 1.99

Analyze this line: "{line_text}"
Extract components for the item with LINE_TOTAL ${line_total:.2f}.

{format_instructions}"""

    partial_vars = {
        "format_instructions": output_parser.get_format_instructions(),
        "product_name_def": CORE_LABELS["PRODUCT_NAME"],
        "quantity_def": CORE_LABELS["QUANTITY"],
        "unit_price_def": CORE_LABELS["UNIT_PRICE"],
    }
    
    prompt_template = PromptTemplate(
        template=template,
        input_variables=["line_ids", "line_text", "line_total"],
        partial_variables=partial_vars,
    )
    
    # Create chain
    chain = prompt_template | llm | output_parser
    
    try:
        # Execute focused analysis on single line
        response = await chain.ainvoke(
            {
                "line_ids": target.line_ids,
                "line_text": target.line_text,
                "line_total": target.line_total_amount,
            },
            config={
                "metadata": {
                    "receipt_type": "COSTCO",
                    "receipt_id": receipt_id or "unknown",
                    "analysis_type": "targeted_line_item",
                    "line_total": target.line_total_amount,
                    "use_case": "focused_component_analysis",
                },
                "tags": [
                    "receipt-labeling",
                    "targeted-line-analysis",
                    "costco",
                    "phase-2",
                ],
            },
        )
        
        return response.line_analysis
        
    except Exception as e:
        logger.error(f"Error in targeted line analysis: {e}")
        return None


def _convert_components_to_labels(target: LineItemTarget, components: LineItemComponents) -> List[CurrencyLabel]:
    """Convert line item components back to CurrencyLabel objects."""
    
    labels = []
    
    # PRODUCT_NAME (text component)
    if components.product_name:
        labels.append(CurrencyLabel(
            word_text=components.product_name,
            label_type=LabelType.PRODUCT_NAME,
            line_ids=target.line_ids,
            confidence=components.confidence,
            reasoning=f"Product name: {components.reasoning}",
            value=0.0,  # Non-currency
            position_y=0.5,  # TODO: Calculate from line position
            context=target.line_text,
            line_number=target.line_ids[0] if target.line_ids else 1
        ))
    
    # QUANTITY (text component)
    if components.quantity:
        labels.append(CurrencyLabel(
            word_text=components.quantity,
            label_type=LabelType.QUANTITY, 
            line_ids=target.line_ids,
            confidence=components.confidence,
            reasoning=f"Quantity: {components.reasoning}",
            value=0.0,  # Non-currency
            position_y=0.5,
            context=target.line_text,
            line_number=target.line_ids[0] if target.line_ids else 1
        ))
    
    # UNIT_PRICE (currency component)
    if components.unit_price:
        labels.append(CurrencyLabel(
            word_text=f"${components.unit_price:.2f}",
            label_type=LabelType.UNIT_PRICE,
            line_ids=target.line_ids,
            confidence=components.confidence,
            reasoning=f"Unit price: {components.reasoning}",
            value=components.unit_price,
            position_y=0.5,
            context=target.line_text,
            line_number=target.line_ids[0] if target.line_ids else 1
        ))
    
    return labels


# Integration function to use in place of existing analyzer
async def analyze_costco_receipt_two_phase(
    client,
    image_id: str,
    receipt_id: int,
    update_labels: bool = False,
    dry_run: bool = False
):
    """Drop-in replacement for costco_analyzer that uses two-phase approach."""
    
    from receipt_label.text_reconstruction import ReceiptTextReconstructor
    from receipt_label.validator import validate_arithmetic_relationships
    from receipt_label.label_updater import ReceiptLabelUpdater, display_label_update_results
    from receipt_label.receipt_models import ReceiptAnalysis
    
    receipt_identifier = f"{image_id}/{receipt_id}"
    print(f"\n🔍 TWO-PHASE COSTCO RECEIPT ANALYSIS: {receipt_identifier}")
    print("-" * 60)
    
    # Get receipt data
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
    print(f"📊 Found {len(lines)} lines")
    
    # Reconstruct text
    reconstructor = ReceiptTextReconstructor()
    formatted_receipt_text, text_groups = reconstructor.reconstruct_receipt(lines)
    currency_contexts = reconstructor.extract_currency_context(text_groups)
    print(f"💰 Found {len(currency_contexts)} currency amounts")
    
    # Run two-phase analysis
    discovered_labels = await two_phase_line_item_analysis(
        formatted_receipt_text,
        currency_contexts,
        lines=lines,  # Pass original ReceiptLine data for proper visual formatting
        receipt_id=receipt_identifier
    )
    
    # Calculate validation total from discovered labels
    validation_total = 0.0
    grand_totals = [label for label in discovered_labels if label.label_type == LabelType.GRAND_TOTAL]
    if grand_totals:
        validation_total = grand_totals[0].value
    
    # Validate arithmetic relationships
    validation_results = validate_arithmetic_relationships(discovered_labels, validation_total) if validation_total else {}
    
    # Update labels if requested
    label_update_results = []
    if update_labels and discovered_labels:
        label_updater = ReceiptLabelUpdater(client)
        label_update_results = await label_updater.apply_currency_labels(
            image_id=image_id,
            receipt_id=receipt_id,
            currency_labels=discovered_labels,
            dry_run=dry_run
        )
        
        if dry_run:
            print("\n🔍 DRY RUN - Label Updates That Would Be Applied:")
        else:
            print("\n📝 Applied Label Updates:")
        display_label_update_results(label_update_results)
    
    # Calculate confidence score
    confidence_score = (
        sum(label.confidence for label in discovered_labels) / len(discovered_labels)
        if discovered_labels else 0.0
    )
    
    return ReceiptAnalysis(
        receipt_id=receipt_identifier,
        known_total=validation_total,
        discovered_labels=discovered_labels,
        validation_results=validation_results,
        total_lines=len(lines),
        confidence_score=confidence_score,
        formatted_text=formatted_receipt_text
    )


if __name__ == "__main__":
    print("🔄 Two-Phase Line-Item Analyzer")
    print("=" * 50)
    print("Phase 1: Currency analysis (GRAND_TOTAL, TAX, LINE_TOTAL, SUBTOTAL)")
    print("Phase 2: Targeted line-item components (PRODUCT_NAME, QUANTITY, UNIT_PRICE)")
    print("This approach should be more focused and accurate than one-shot analysis.")
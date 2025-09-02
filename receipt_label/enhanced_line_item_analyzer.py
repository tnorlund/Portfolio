#!/usr/bin/env python3
"""
Enhanced line-item analysis for PRODUCT_NAME, QUANTITY, UNIT_PRICE, and LINE_TOTAL.
Extends the currency analyzer to include comprehensive line-item labeling.
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from enum import Enum

from receipt_label.receipt_models import LabelType, CurrencyLabel


class LineItemLabelType(Enum):
    """Extended label types for complete line-item analysis."""
    # Currency labels (existing)
    GRAND_TOTAL = "GRAND_TOTAL"
    TAX = "TAX" 
    LINE_TOTAL = "LINE_TOTAL"
    SUBTOTAL = "SUBTOTAL"
    
    # New line-item labels
    PRODUCT_NAME = "PRODUCT_NAME"
    QUANTITY = "QUANTITY"
    UNIT_PRICE = "UNIT_PRICE"


@dataclass
class LineItemContext:
    """Context for a complete line item with all related fields."""
    line_id: int
    line_text: str
    currency_amount: Optional[float] = None  # LINE_TOTAL value
    product_candidates: List[str] = None      # Potential product names
    quantity_candidates: List[str] = None     # Potential quantities
    unit_price_candidates: List[str] = None   # Potential unit prices


class LineItemLabel(BaseModel):
    """Extended label model for complete line-item analysis."""
    word_text: str = Field(description="The exact text of the labeled word/phrase")
    label_type: LineItemLabelType = Field(description="The type of label")
    line_ids: List[int] = Field(default_factory=list, description="Receipt line IDs containing this text")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in classification")
    reasoning: str = Field(description="Explanation for the classification")
    value: Optional[float] = Field(default=None, description="Numeric value if applicable")
    
    # Relationship fields for line-item analysis
    related_line_id: Optional[int] = Field(default=None, description="Primary line ID for this item")
    arithmetic_relationship: Optional[str] = Field(default=None, description="E.g., 'quantity × unit_price = line_total'")


class LineItemAnalysis(BaseModel):
    """Complete analysis result with line-item relationships."""
    receipt_id: str
    line_items: List[Dict] = Field(default_factory=list, description="Complete line items with all fields")
    discovered_labels: List[LineItemLabel] = Field(default_factory=list)
    confidence_score: float
    arithmetic_validation: Dict = Field(default_factory=dict)


def build_line_item_contexts(lines, currency_contexts: List[Dict]) -> List[LineItemContext]:
    """Build comprehensive context for each line item."""
    
    contexts = []
    
    # Group by line_id to analyze complete line items
    lines_by_id = {line.line_id: line for line in lines}
    
    # Find lines with currency amounts (potential line totals)
    for currency_ctx in currency_contexts:
        line_id = currency_ctx.get("line_ids", [0])[0]
        if line_id not in lines_by_id:
            continue
            
        line = lines_by_id[line_id]
        
        # Get all words from this line for analysis
        line_words = [word for word in line.words if word.line_id == line_id]
        line_text = " ".join(word.text for word in line_words)
        
        # Identify potential components on this line
        product_candidates = []
        quantity_candidates = []
        unit_price_candidates = []
        
        for word in line_words:
            word_text = word.text.strip()
            
            # Skip the currency amount we're already analyzing
            if currency_ctx["raw_value"] in word_text:
                continue
                
            # Quantity patterns
            if _looks_like_quantity(word_text):
                quantity_candidates.append(word_text)
            
            # Unit price patterns (currency but not the line total)
            elif _looks_like_currency(word_text) and word_text != currency_ctx["formatted_text"]:
                unit_price_candidates.append(word_text)
                
            # Product name candidates (descriptive text)
            elif _looks_like_product_name(word_text):
                product_candidates.append(word_text)
        
        context = LineItemContext(
            line_id=line_id,
            line_text=line_text,
            currency_amount=currency_ctx["raw_value"],
            product_candidates=product_candidates,
            quantity_candidates=quantity_candidates,
            unit_price_candidates=unit_price_candidates
        )
        contexts.append(context)
    
    return contexts


def _looks_like_quantity(text: str) -> bool:
    """Check if text looks like a quantity."""
    import re
    
    # Patterns for quantities
    quantity_patterns = [
        r'^\d+$',                           # "2", "10"
        r'^\d*\.\d+$',                      # "1.5", "0.75"
        r'^\d+\s*(lb|oz|kg|g|ea|each)s?$',  # "2 lb", "5 oz"
        r'^\d+x$',                          # "2x", "10x"
        r'^@\s*\d+',                        # "@2", "@ 3"
    ]
    
    return any(re.match(pattern, text.lower()) for pattern in quantity_patterns)


def _looks_like_currency(text: str) -> bool:
    """Check if text looks like currency."""
    import re
    return bool(re.match(r'^\$?\d+\.\d{2}$', text))


def _looks_like_product_name(text: str) -> bool:
    """Check if text looks like a product name."""
    # Product names are typically:
    # - Alphabetic with possible spaces/hyphens
    # - Not pure numbers
    # - Not single characters
    # - Not common receipt keywords
    
    if len(text) < 2:
        return False
        
    # Skip obvious non-product terms
    skip_terms = {
        'total', 'tax', 'subtotal', 'discount', 'coupon',
        'cash', 'card', 'visa', 'mastercard', 'change'
    }
    if text.lower() in skip_terms:
        return False
    
    # Must have some alphabetic content
    return any(c.isalpha() for c in text)


async def analyze_line_items_with_llm(
    formatted_receipt_text: str,
    line_item_contexts: List[LineItemContext],
    receipt_id: str = None
) -> List[LineItemLabel]:
    """Enhanced LLM analysis for complete line items."""
    
    # Build enhanced prompt with line-item context
    line_item_summaries = []
    for ctx in line_item_contexts:
        summary = f"Line {ctx.line_id}: '{ctx.line_text}'"
        if ctx.currency_amount:
            summary += f" (contains ${ctx.currency_amount:.2f})"
        if ctx.product_candidates:
            summary += f" [Products: {', '.join(ctx.product_candidates)}]"
        if ctx.quantity_candidates:
            summary += f" [Qty: {', '.join(ctx.quantity_candidates)}]"
        if ctx.unit_price_candidates:
            summary += f" [Unit: {', '.join(ctx.unit_price_candidates)}]"
        line_item_summaries.append(summary)
    
    enhanced_prompt = f"""
You are analyzing a receipt to identify complete line items with all related fields.

RECEIPT TEXT (formatted with line IDs):
{formatted_receipt_text}

LINE ITEM CONTEXTS:
{chr(10).join(line_item_summaries)}

For each line item, identify:
1. PRODUCT_NAME: The item description/name
2. QUANTITY: How many/much (e.g., "2", "1.5 lb", "3x")
3. UNIT_PRICE: Price per unit (e.g., "$4.99")
4. LINE_TOTAL: Extended total for the line

Look for arithmetic relationships: QUANTITY × UNIT_PRICE = LINE_TOTAL

Output complete line items with high confidence only.
"""
    
    # This would call the LLM similar to analyze_with_ollama
    # For now, return structured format that the existing system expects
    # TODO: Implement actual LLM call with enhanced prompt
    
    return []


# Integration point: Enhanced analyzer function
async def analyze_costco_receipt_with_line_items(
    client, 
    image_id: str, 
    receipt_id: int,
    update_labels: bool = False,
    dry_run: bool = False
) -> LineItemAnalysis:
    """Enhanced receipt analysis including complete line-item labeling."""
    
    # Step 1: Get receipt data and build contexts (existing logic)
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
    
    # Step 2: Build enhanced line-item contexts
    from receipt_label.text_reconstruction import ReceiptTextReconstructor
    reconstructor = ReceiptTextReconstructor()
    formatted_receipt_text, text_groups = reconstructor.reconstruct_receipt(lines)
    currency_contexts = reconstructor.extract_currency_context(text_groups)
    
    line_item_contexts = build_line_item_contexts(lines, currency_contexts)
    
    # Step 3: Enhanced LLM analysis for complete line items
    discovered_labels = await analyze_line_items_with_llm(
        formatted_receipt_text,
        line_item_contexts,
        receipt_id=f"{image_id}/{receipt_id}"
    )
    
    # Step 4: Build complete line items with relationships
    complete_line_items = _build_complete_line_items(discovered_labels)
    
    # Step 5: Apply labels to DynamoDB (if requested)
    if update_labels:
        # TODO: Extend label updater for new label types
        pass
    
    # Return enhanced analysis
    return LineItemAnalysis(
        receipt_id=f"{image_id}/{receipt_id}",
        line_items=complete_line_items,
        discovered_labels=discovered_labels,
        confidence_score=_calculate_line_item_confidence(discovered_labels),
        arithmetic_validation=_validate_line_item_arithmetic(complete_line_items)
    )


def _build_complete_line_items(labels: List[LineItemLabel]) -> List[Dict]:
    """Group labels into complete line items."""
    
    line_items = {}
    
    # Group labels by line_id
    for label in labels:
        line_id = label.related_line_id or (label.line_ids[0] if label.line_ids else None)
        if line_id not in line_items:
            line_items[line_id] = {
                "line_id": line_id,
                "product_name": None,
                "quantity": None,
                "unit_price": None,
                "line_total": None,
                "arithmetic_valid": False
            }
        
        # Map label to appropriate field
        item = line_items[line_id]
        if label.label_type == LineItemLabelType.PRODUCT_NAME:
            item["product_name"] = label.word_text
        elif label.label_type == LineItemLabelType.QUANTITY:
            item["quantity"] = label.word_text
        elif label.label_type == LineItemLabelType.UNIT_PRICE:
            item["unit_price"] = label.value
        elif label.label_type == LineItemLabelType.LINE_TOTAL:
            item["line_total"] = label.value
    
    # Validate arithmetic for complete items
    for item in line_items.values():
        if all(item[field] is not None for field in ["quantity", "unit_price", "line_total"]):
            try:
                qty = float(item["quantity"].replace("lb", "").replace("oz", "").strip())
                expected = qty * item["unit_price"]
                item["arithmetic_valid"] = abs(expected - item["line_total"]) < 0.01
            except:
                pass
    
    return list(line_items.values())


def _calculate_line_item_confidence(labels: List[LineItemLabel]) -> float:
    """Calculate overall confidence for line-item analysis."""
    if not labels:
        return 0.0
    return sum(label.confidence for label in labels) / len(labels)


def _validate_line_item_arithmetic(line_items: List[Dict]) -> Dict:
    """Validate arithmetic relationships in line items."""
    total_items = len(line_items)
    valid_items = sum(1 for item in line_items if item.get("arithmetic_valid", False))
    
    return {
        "total_line_items": total_items,
        "arithmetically_valid": valid_items,
        "validation_rate": valid_items / total_items if total_items > 0 else 0,
        "complete_items": sum(1 for item in line_items if all(
            item.get(field) is not None 
            for field in ["product_name", "quantity", "unit_price", "line_total"]
        ))
    }


if __name__ == "__main__":
    print("Enhanced Line-Item Analyzer")
    print("=" * 50)
    print("This module extends currency analysis to include:")
    print("- PRODUCT_NAME: Item descriptions") 
    print("- QUANTITY: Counts and weights")
    print("- UNIT_PRICE: Price per unit")
    print("- LINE_TOTAL: Extended totals")
    print("- Arithmetic validation: qty × unit_price = line_total")
"""
LLM-based currency classification for receipts.
Extracted from costco_label_discovery.py to improve modularity.
"""

import os
import logging
from typing import List, Dict
from receipt_label.constants import CORE_LABELS
from receipt_label.costco_models import CurrencyLabel, CurrencyClassificationResponse, LabelType

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
    print("⚠️ LangChain not available - install with: pip install langchain-core")


async def analyze_with_ollama(
    formatted_receipt_text: str,
    currency_contexts: List[Dict],
    known_total: float = None,
    receipt_id: str = None,
    lines: List = None,  # Optional ReceiptLine data for better formatting
) -> List[CurrencyLabel]:
    """Use Ollama LLM with LangChain structured output to analyze and classify currency amounts.

    Note: This version removes the concept of 'target amounts' from the prompt input and
    asks the model to classify all currency amounts found in the provided receipt text.
    """

    if not LANGCHAIN_AVAILABLE:
        return []

    # Create authenticated Ollama Turbo client using the helper function
    llm = create_ollama_turbo_client(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        api_key=os.getenv("OLLAMA_API_KEY"),
        temperature=0.0,
    )

    # Create Pydantic output parser
    output_parser = PydanticOutputParser(
        pydantic_object=CurrencyClassificationResponse
    )

    # Create optimized prompt template (hint removed based on A/B test results)
    template = """You are analyzing a COSTCO receipt to classify currency amounts. 

RECEIPT TEXT (formatted with line IDs):
{receipt_text}

LABEL DEFINITIONS (from receipt_label.constants):
- GRAND_TOTAL: {grand_total_def} (the final total amount paid)
- TAX: {tax_def}
- LINE_TOTAL: {line_total_def}  
- SUBTOTAL: {subtotal_def}

ARITHMETIC VALIDATION:
The correct receipt arithmetic should follow:
1. SUM(LINE_TOTAL) + TAX = GRAND_TOTAL
2. SUBTOTAL + TAX = GRAND_TOTAL (where SUBTOTAL = SUM(LINE_TOTAL))

INSTRUCTIONS:
Analyze the complete receipt text above. The text is formatted with line IDs (e.g., "5:", "12-15:") showing how OCR lines are grouped.

Identify ALL currency amounts you find in the receipt text and classify them as GRAND_TOTAL, TAX, LINE_TOTAL, or SUBTOTAL based on:

1. **Surrounding text context**: Look for keywords like "TOTAL", "TAX", "SUBTOTAL", product names
2. **Line position**: Earlier lines are usually items, later lines are totals
3. **COSTCO patterns**: 
   - Tax is often $0.00 
   - Grand total appears multiple times (receipt total, payment confirmation)
   - Subtotal may be missing or equal to grand total when tax is $0.00
   - Line totals are individual item prices

4. **Arithmetic validation**: Ensure the relationships make sense

{format_instructions}"""
    
    partial_vars = {
        "format_instructions": output_parser.get_format_instructions(),
        "grand_total_def": CORE_LABELS["GRAND_TOTAL"],
        "tax_def": CORE_LABELS["TAX"],
        "line_total_def": CORE_LABELS["LINE_TOTAL"],
        "subtotal_def": CORE_LABELS["SUBTOTAL"],
    }
    
    prompt_template = PromptTemplate(
        template=template,
        input_variables=["receipt_text"],
        partial_variables=partial_vars,
    )

    # Create the chain: prompt | llm | output_parser
    chain = prompt_template | llm | output_parser

    try:
        print(
            f"\n🤖 Calling Ollama Turbo gpt-oss:120b with PydanticOutputParser..."
        )
        print(
            f"Analyzing receipt text; {len(currency_contexts)} detected currency values for matching context"
        )

        # Use proper visual formatting if ReceiptLine data is available
        if lines:
            from receipt_label.prompt_formatting.lines import format_receipt_lines_visual_order
            visual_receipt_text = format_receipt_lines_visual_order(lines, show_line_ids=True)
            receipt_text_for_prompt = visual_receipt_text
            print("   Using visual line formatting with line IDs for better receipt structure")
        else:
            receipt_text_for_prompt = formatted_receipt_text
            print("   Using fallback formatted receipt text")

        # Execute the chain with metadata for LangSmith tracing
        response = await chain.ainvoke(
            {
                "receipt_text": receipt_text_for_prompt,
            },
            config={
                "metadata": {
                    "receipt_type": "COSTCO",
                    "receipt_id": receipt_id or "unknown",
                    "known_total": known_total,
                    "currency_amounts_analyzed": len(currency_contexts),
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
            f"\n✅ Structured response received with {len(response.classifications)} classifications"
        )

        # Convert to CurrencyLabel objects
        discovered_labels = []

        for item in response.classifications:
            # Find matching currency context
            matching_ctx = None
            target_amount = float(item.amount)

            for ctx in currency_contexts:
                if abs(target_amount - ctx["value"]) < 0.01:
                    matching_ctx = ctx
                    break

            if matching_ctx:
                try:
                    label = CurrencyLabel(
                        word_text=f"${matching_ctx['value']:.2f}",
                        label_type=item.label_type,
                        line_number=matching_ctx["group_number"],
                        line_ids=matching_ctx.get("line_ids", []),
                        confidence=item.confidence,
                        reasoning=item.reasoning or f"LLM classified as {item.label_type.value}",
                        value=matching_ctx["value"],
                        position_y=matching_ctx["y_position"],
                        context=matching_ctx["context"],
                    )
                    discovered_labels.append(label)
                    
                    line_ids = matching_ctx.get("line_ids", [])
                    if len(line_ids) > 1:
                        line_span = f"{line_ids[0]}-{line_ids[-1]}"
                    else:
                        line_span = f"{line_ids[0] if line_ids else '?'}"
                    
                    print(
                        f"   📄 {item.label_type.value}: ${item.amount:.2f} (confidence: {item.confidence:.2f}) lines {line_span}"
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


# NEW: Enhanced line-item analysis
from pydantic import BaseModel, Field
from typing import Optional

class LineItemClassificationItem(BaseModel):
    """Individual line-item classification with all components."""

    # Currency amounts
    line_total: Optional[float] = Field(default=None, description="Extended price for this line item")
    unit_price: Optional[float] = Field(default=None, description="Price per single unit")
    
    # Text components  
    product_name: Optional[str] = Field(default=None, description="Descriptive product name text")
    quantity: Optional[str] = Field(default=None, description="Quantity text (e.g., '2', '1.5 lb', '3x')")
    
    # Metadata
    line_number: int = Field(description="Line number in the receipt")
    line_ids: List[int] = Field(description="Individual OCR line IDs for this grouped line")
    confidence: float = Field(ge=0.0, le=1.0, description="Overall confidence for this line item")
    reasoning: str = Field(description="Explanation for the classifications")
    arithmetic_valid: bool = Field(description="Whether quantity × unit_price ≈ line_total")


class LineItemAnalysisResponse(BaseModel):
    """Structured response for complete line-item analysis."""

    # Currency totals (existing functionality)
    grand_total: Optional[float] = Field(default=None)
    tax: Optional[float] = Field(default=None)  
    subtotal: Optional[float] = Field(default=None)
    
    # Line items with all components
    line_items: List[LineItemClassificationItem] = Field(
        description="Complete line items with product names, quantities, prices"
    )


async def analyze_with_line_items(
    formatted_receipt_text: str,
    currency_contexts: List[Dict],
    receipt_id: str = None,
) -> List[CurrencyLabel]:
    """Enhanced analysis that identifies complete line items with PRODUCT_NAME, QUANTITY, UNIT_PRICE, LINE_TOTAL."""

    if not LANGCHAIN_AVAILABLE:
        return []

    # Create authenticated Ollama Turbo client
    llm = create_ollama_turbo_client(
        model="gpt-oss:120b",
        base_url="https://ollama.com", 
        api_key=os.getenv("OLLAMA_API_KEY"),
        temperature=0.0,
    )

    # Create Pydantic output parser for enhanced response
    output_parser = PydanticOutputParser(
        pydantic_object=LineItemAnalysisResponse
    )

    # Enhanced prompt template for complete line-item analysis
    template = """You are analyzing a COSTCO receipt to identify complete line items with all components.

RECEIPT TEXT (formatted with line IDs):
{receipt_text}

LABEL DEFINITIONS:
Currency Labels:
- GRAND_TOTAL: {grand_total_def}
- TAX: {tax_def}  
- LINE_TOTAL: {line_total_def}
- SUBTOTAL: {subtotal_def}

Line-Item Component Labels:
- PRODUCT_NAME: {product_name_def}
- QUANTITY: {quantity_def}
- UNIT_PRICE: {unit_price_def}

INSTRUCTIONS:
Analyze the receipt text above to identify:

1. **CURRENCY TOTALS** (GRAND_TOTAL, TAX, SUBTOTAL)
2. **COMPLETE LINE ITEMS** for each product with:
   - PRODUCT_NAME: The item description (e.g., "ORGANIC BANANAS")
   - QUANTITY: Amount purchased (e.g., "2 lb", "3", "1.5")  
   - UNIT_PRICE: Price per unit (e.g., "$1.99")
   - LINE_TOTAL: Extended total for that line (e.g., "$3.98")

SPATIAL UNDERSTANDING:
- The receipt text is grouped by visual lines using line IDs (e.g., "37:", "25-26:")
- Each grouped line may contain multiple components: "ORGANIC BANANAS 2 lb @ $1.99 $3.98"
- Components are spatially separated but logically related

ARITHMETIC VALIDATION:
For each line item, validate: QUANTITY × UNIT_PRICE ≈ LINE_TOTAL
Example: 2 lb × $1.99 = $3.98 ✓

COSTCO PATTERNS:
- Product names are descriptive (ORGANIC BANANAS, MILK WHOLE GALLON)
- Quantities often include units (lb, oz, ea, each)
- Unit prices preceded by @ symbol: "2 lb @ $1.99"
- Line totals are rightmost currency amounts

Focus on lines that appear to be individual product purchases, not summary lines.

{format_instructions}"""

    partial_vars = {
        "format_instructions": output_parser.get_format_instructions(),
        "grand_total_def": CORE_LABELS["GRAND_TOTAL"],
        "tax_def": CORE_LABELS["TAX"],
        "line_total_def": CORE_LABELS["LINE_TOTAL"],
        "subtotal_def": CORE_LABELS["SUBTOTAL"], 
        "product_name_def": CORE_LABELS["PRODUCT_NAME"],
        "quantity_def": CORE_LABELS["QUANTITY"],
        "unit_price_def": CORE_LABELS["UNIT_PRICE"],
    }

    prompt_template = PromptTemplate(
        template=template,
        input_variables=["receipt_text"],
        partial_variables=partial_vars,
    )

    # Create the chain
    chain = prompt_template | llm | output_parser

    try:
        print(f"\n🤖 Calling Ollama Turbo for ENHANCED LINE-ITEM ANALYSIS...")
        print(f"Analyzing complete line items with product names, quantities, and prices")

        # Execute the chain
        response = await chain.ainvoke(
            {"receipt_text": formatted_receipt_text},
            config={
                "metadata": {
                    "receipt_type": "COSTCO",
                    "receipt_id": receipt_id or "unknown",
                    "analysis_type": "enhanced_line_items",
                    "use_case": "complete_line_item_classification",
                },
                "tags": [
                    "receipt-labeling",
                    "line-item-analysis", 
                    "costco",
                    "ollama-turbo",
                ],
            },
        )

        print(f"\n✅ Enhanced analysis received with {len(response.line_items)} line items")

        # Convert to CurrencyLabel objects (extended for new label types)
        discovered_labels = []

        # Add currency totals (existing logic)
        if response.grand_total:
            discovered_labels.append(_create_currency_label(
                response.grand_total, LabelType.GRAND_TOTAL, currency_contexts, "Grand total identified"
            ))
        
        if response.tax:
            discovered_labels.append(_create_currency_label(
                response.tax, LabelType.TAX, currency_contexts, "Tax amount identified"
            ))
            
        if response.subtotal:
            discovered_labels.append(_create_currency_label(
                response.subtotal, LabelType.SUBTOTAL, currency_contexts, "Subtotal identified"
            ))

        # Add line items (NEW)
        for item in response.line_items:
            item_labels = []
            
            # LINE_TOTAL (currency)
            if item.line_total:
                item_labels.append(_create_currency_label(
                    item.line_total, LabelType.LINE_TOTAL, currency_contexts, 
                    f"Line total for {item.product_name or 'item'}"
                ))
            
            # UNIT_PRICE (currency) 
            if item.unit_price:
                item_labels.append(_create_currency_label(
                    item.unit_price, LabelType.UNIT_PRICE, currency_contexts,
                    f"Unit price for {item.product_name or 'item'}"
                ))
            
            # PRODUCT_NAME (text)
            if item.product_name:
                item_labels.append(CurrencyLabel(
                    word_text=item.product_name,
                    label_type=LabelType.PRODUCT_NAME,
                    line_ids=item.line_ids,
                    confidence=item.confidence,
                    reasoning=f"Product name: {item.reasoning}",
                    value=0.0,  # Non-currency field
                    position_y=0.5,  # TODO: Calculate from line_ids
                    context=f"Line {item.line_number}",
                    line_number=item.line_number
                ))
            
            # QUANTITY (text)  
            if item.quantity:
                item_labels.append(CurrencyLabel(
                    word_text=item.quantity,
                    label_type=LabelType.QUANTITY,
                    line_ids=item.line_ids,
                    confidence=item.confidence,
                    reasoning=f"Quantity: {item.reasoning}",
                    value=0.0,  # Non-currency field
                    position_y=0.5,  # TODO: Calculate from line_ids  
                    context=f"Line {item.line_number}",
                    line_number=item.line_number
                ))
            
            discovered_labels.extend(item_labels)

        # Display results
        for label in discovered_labels:
            if label.label_type in [LabelType.PRODUCT_NAME, LabelType.QUANTITY]:
                print(f"   📄 {label.label_type.value}: {label.word_text} (confidence: {label.confidence:.2f}) lines {label.line_ids}")
            else:
                print(f"   📄 {label.label_type.value}: ${label.value:.2f} (confidence: {label.confidence:.2f}) lines {label.line_ids}")

        return discovered_labels

    except Exception as e:
        logger.error(f"Error in enhanced line-item analysis: {e}")
        # Fallback to original analysis
        return await analyze_with_ollama(formatted_receipt_text, currency_contexts, None, receipt_id)


def _create_currency_label(amount: float, label_type: LabelType, currency_contexts: List[Dict], reasoning: str) -> CurrencyLabel:
    """Helper to create CurrencyLabel from amount and contexts."""
    
    # Find matching currency context
    matching_ctx = None
    for ctx in currency_contexts:
        if abs(amount - ctx["value"]) < 0.01:
            matching_ctx = ctx
            break
    
    if not matching_ctx:
        # Create minimal label if no context found
        return CurrencyLabel(
            word_text=f"${amount:.2f}",
            label_type=label_type,
            line_ids=[],
            confidence=0.8,
            reasoning=reasoning,
            value=amount,
            position_y=0.5,
            context="",
            line_number=1
        )
    
    return CurrencyLabel(
        word_text=matching_ctx["text"],
        label_type=label_type,
        line_ids=matching_ctx["line_ids"],
        confidence=0.9,
        reasoning=reasoning,
        value=amount,
        position_y=matching_ctx.get("y_position", 0.5),
        context=matching_ctx["context"],
        line_number=matching_ctx.get("group_number", 1)
    )
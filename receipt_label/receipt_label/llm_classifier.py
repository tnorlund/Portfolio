"""
LLM-based currency classification for receipts.
Extracted from costco_label_discovery.py to improve modularity.
"""

import os
import logging
from typing import List, Dict
from receipt_label.constants import CORE_LABELS
from receipt_label.costco_models import CurrencyLabel, CurrencyClassificationResponse

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
    known_total: float,
    receipt_id: str = None,
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

    # Create prompt template using the properly formatted receipt text
    prompt_template = PromptTemplate(
        template="""You are analyzing a COSTCO receipt to classify currency amounts. 

RECEIPT TEXT (formatted with line IDs):
{receipt_text}

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

Identify ALL currency amounts you find in the receipt text and classify them as GRAND_TOTAL, TAX, LINE_TOTAL, or SUBTOTAL based on:

1. **Surrounding text context**: Look for keywords like "TOTAL", "TAX", "SUBTOTAL", product names
2. **Line position**: Earlier lines are usually items, later lines are totals
3. **COSTCO patterns**: 
   - Tax is often $0.00 
   - Grand total appears multiple times (receipt total, payment confirmation)
   - Subtotal may be missing or equal to grand total when tax is $0.00
   - Line totals are individual item prices

4. **Arithmetic validation**: Ensure the relationships make sense

{format_instructions}""",
        input_variables=["receipt_text"],
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
            f"\n🤖 Calling Ollama Turbo gpt-oss:120b with PydanticOutputParser..."
        )
        print(
            f"Analyzing receipt text; {len(currency_contexts)} detected currency values for matching context"
        )

        # Execute the chain with metadata for LangSmith tracing
        response = await chain.ainvoke(
            {
                "receipt_text": formatted_receipt_text,
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
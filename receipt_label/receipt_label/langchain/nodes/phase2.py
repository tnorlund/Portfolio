from __future__ import annotations

from typing import List, Sequence
from langgraph.types import Send
from langchain_ollama import ChatOllama

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.constants import CORE_LABELS
from receipt_label.langchain.models import (
    LineItemLabel,
    LineItemLabelType,
    Phase2Response,
    CurrencyLabelType,
)
from receipt_label.langchain.utils.retry import retry_with_backoff
from receipt_label.langchain.utils.cove import apply_chain_of_verification
from receipt_label.langchain.state.currency_validation import (
    CurrencyAnalysisState,
)


def dispatch_to_parallel_phase2(
    state: CurrencyAnalysisState, ollama_api_key: str
) -> Sequence[Send]:
    print(f"🔄 Dispatching parallel Phase 2 analysis")

    line_totals = [
        label
        for label in state.currency_labels
        if label.label_type == CurrencyLabelType.LINE_TOTAL
    ]
    if not line_totals:
        print("   No LINE_TOTAL amounts found - skipping Phase 2")
        return []

    print(f"   Creating {len(line_totals)} parallel Phase 2 tasks")

    words: List[ReceiptWord] = state.words
    words_by_line: dict[int, List[str]] = {}
    try:
        for w in words:
            words_by_line.setdefault(w.line_id, []).append(
                getattr(w, "text", "")
            )
    except Exception:
        pass

    sends = []
    for i, line_total in enumerate(line_totals):
        target_lines_text_parts: List[str] = []
        try:
            for lid in getattr(line_total, "line_ids", []) or []:
                tokens = words_by_line.get(lid, [])
                if tokens:
                    target_lines_text_parts.append(" ".join(tokens))
        except Exception:
            pass
        compiled_text = " ".join([t for t in target_lines_text_parts if t])

        send_data = {
            "line_total_index": i,
            "target_line_total": line_total,
            "target_line_text_compiled": compiled_text,
            "receipt_text": state.formatted_text,
            "receipt_id": state.receipt_id,
            "ollama_api_key": ollama_api_key,
            "currency_labels": state.currency_labels,
        }
        sends.append(Send("phase2_line_analysis", send_data))

    return sends


async def phase2_line_analysis(send_data: dict, enable_cove: bool = True) -> dict:
    """Analyze line items with optional Chain of Verification.

    Args:
        send_data: Dictionary containing analysis data
        enable_cove: Whether to apply Chain of Verification (default: True)
    """
    index = send_data["line_total_index"]
    line_total = send_data["target_line_total"]
    receipt_text = send_data["receipt_text"]
    receipt_id = send_data["receipt_id"]
    ollama_api_key = send_data["ollama_api_key"]
    currency_labels = send_data["currency_labels"]

    print(f"   🤖 Phase 2.{index}: Analyzing line with {line_total.line_text}")

    # Initialize LLM with structured outputs
    llm = ChatOllama(
        model="gpt-oss:20b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"},
            "timeout": 120,  # 2 minute timeout for reliability
        },
        format="json",  # Force JSON format output
        temperature=0.3,
    )

    # Bind to Pydantic model for structured outputs
    llm_structured = llm.with_structured_output(Phase2Response)

    subset = [
        LineItemLabelType.PRODUCT_NAME.value,
        LineItemLabelType.QUANTITY.value,
        LineItemLabelType.UNIT_PRICE.value,
    ]
    subset_definitions = "\n".join(
        f"- {l}: {CORE_LABELS[l]}" for l in subset if l in CORE_LABELS
    )

    currency_context = "\n".join(
        [
            f"- {label.label_type.value}: {label.amount}"
            for label in currency_labels
        ]
    )

    # Build messages for the LLM
    # We dynamically inject the JSON schema from the Pydantic model
    # This ensures the LLM understands the exact expected structure
    import json

    # Get JSON schema from the Pydantic model (single source of truth!)
    schema = Phase2Response.model_json_schema()
    schema_json = json.dumps(schema, indent=2)

    messages = [
        {
            "role": "user",
            "content": f"""You are analyzing a receipt snippet to identify line item components.

TARGET SNIPPET (COMPILED FROM OCR WORDS):
{send_data.get("target_line_text_compiled", "")}
TARGET AMOUNT: {str(getattr(line_total, "amount", ""))}

CURRENCY CONTEXT FROM PHASE 1:
{currency_context}

FULL RECEIPT CONTEXT:
{receipt_text}

Identify only the components on the target snippet:
{subset_definitions}

IMPORTANT:
1. Output only labels for words that could plausibly co-occur with the amount.
2. Do not include line positions or IDs; only the word text and label type.
3. Focus on PRODUCT_NAME, QUANTITY, UNIT_PRICE.

CRITICAL INSTRUCTIONS:
1. You MUST respond with valid JSON ONLY
2. NO markdown tables, NO text explanations
3. Output must match this EXACT JSON structure:

{schema_json}

Analyze the snippet and extract line item components as JSON matching the schema above."""
        }
    ]

    # Define invocation function for retry logic
    async def invoke_llm():
        return await llm_structured.ainvoke(
            messages,
            config={
                "metadata": {
                    "receipt_id": receipt_id,
                    "phase": "lineitem_analysis",
                    "model": "20b",
                    "line_index": index,
                },
                "tags": ["phase2", "line-items", "receipt-analysis"],
            },
        )

    try:
        # Get structured response with retry logic
        initial_response = await retry_with_backoff(
            invoke_llm,
            max_retries=3,
            initial_delay=2.0,  # Start with 2 second delay
        )

        # Apply Chain of Verification if enabled
        # For Phase 2, we verify against the target snippet and full receipt context
        target_snippet = send_data.get("target_line_text_compiled", "")
        verification_context = f"TARGET SNIPPET:\n{target_snippet}\n\nFULL RECEIPT:\n{receipt_text}"

        if enable_cove:
            print(f"   🔍 Applying Chain of Verification to Phase 2.{index} results...")
            response = await apply_chain_of_verification(
                initial_answer=initial_response,
                receipt_text=verification_context,
                task_description=f"Line item component extraction (PRODUCT_NAME, QUANTITY, UNIT_PRICE) for line with amount {line_total.amount}",
                response_model=Phase2Response,
                llm=llm,
                enable_cove=True,
            )
        else:
            response = initial_response

        allowed_label_types = {
            LineItemLabelType.PRODUCT_NAME.value,
            LineItemLabelType.QUANTITY.value,
            LineItemLabelType.UNIT_PRICE.value,
        }

        line_item_labels = []
        for item in response.line_item_labels:
            lt_value = (
                item.label_type.value
                if hasattr(item.label_type, "value")
                else item.label_type
            )
            if lt_value not in allowed_label_types:
                print(
                    f"⚠️ Skipping invalid label type '{lt_value}' for '{getattr(item, 'word_text', '')}' - not in Phase 2 subset"
                )
                continue

            try:
                label_type = getattr(LineItemLabelType, lt_value)
                label = LineItemLabel(
                    word_text=item.word_text,
                    label_type=label_type,
                    confidence=item.confidence,
                    reasoning=item.reasoning,
                )
                line_item_labels.append(label)
            except AttributeError:
                print(
                    f"⚠️ Warning: Unknown label type '{item.label_type}' for '{item.word_text}', skipping"
                )
                continue

        print(
            f"   ✅ Phase 2.{index}: Found {len(line_item_labels)} labels for amount {getattr(line_total, 'amount', '')}"
        )
        return {"line_item_labels": line_item_labels}
    except Exception as e:
        print(f"Phase 2.{index} failed: {e}")
        return {"line_item_labels": []}

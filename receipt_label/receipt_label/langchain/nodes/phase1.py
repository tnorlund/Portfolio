from __future__ import annotations

from langchain_ollama import ChatOllama

from receipt_label.constants import CORE_LABELS
from receipt_label.langchain.models import (
    CurrencyLabel,
    CurrencyLabelType,
    Phase1Response,
)
from receipt_label.langchain.state.currency_validation import (
    CurrencyAnalysisState,
)
from receipt_label.langchain.utils.retry import retry_with_backoff
from receipt_label.langchain.utils.cove import apply_chain_of_verification


async def phase1_currency_analysis(
    state: CurrencyAnalysisState, ollama_api_key: str, enable_cove: bool = True
):
    """Analyze currency amounts (GRAND_TOTAL, TAX, etc.) with optional Chain of Verification.

    Args:
        state: Current workflow state with receipt data
        ollama_api_key: Ollama API key for LLM inference
        enable_cove: Whether to apply Chain of Verification (default: True)
    """

    # Initialize LLM with structured outputs
    llm = ChatOllama(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"},
            "timeout": 120,  # 2 minute timeout for reliability
        },
        format="json",  # Force JSON format output
        temperature=0.3,
    )

    # Bind to Pydantic model for structured outputs (eliminates need for PydanticOutputParser)
    llm_structured = llm.with_structured_output(Phase1Response)

    # Build label definitions
    subset = [
        CurrencyLabelType.GRAND_TOTAL.value,
        CurrencyLabelType.TAX.value,
        CurrencyLabelType.SUBTOTAL.value,
        CurrencyLabelType.LINE_TOTAL.value,
    ]
    subset_definitions = "\n".join(
        f"- {l}: {CORE_LABELS[l]}" for l in subset if l in CORE_LABELS
    )

    # Build messages for the LLM
    # We dynamically inject the JSON schema from the Pydantic model
    # This ensures the LLM understands the exact expected structure
    import json

    # Get JSON schema from the Pydantic model (single source of truth!)
    schema = Phase1Response.model_json_schema()
    schema_json = json.dumps(schema, indent=2)

    messages = [
        {
            "role": "user",
            "content": f"""You are analyzing a receipt to classify currency amounts.

RECEIPT TEXT:
{state.formatted_text}

Find all currency amounts and classify them as:
{subset_definitions}

Focus on the most obvious currency amounts first.

CRITICAL INSTRUCTIONS:
1. You MUST respond with valid JSON ONLY
2. NO markdown tables, NO text explanations
3. Output must match this EXACT JSON structure:

{schema_json}

Extract all currency amounts and return them as JSON matching the schema above."""
        }
    ]

    # Define invocation function for retry logic
    async def invoke_llm():
        return await llm_structured.ainvoke(
            messages,
            config={
                "metadata": {
                    "receipt_id": state.receipt_id,
                    "phase": "currency_analysis",
                    "model": "120b",
                },
                "tags": ["phase1", "currency", "receipt-analysis"],
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
        if enable_cove:
            print("   🔍 Applying Chain of Verification to Phase 1 results...")
            response = await apply_chain_of_verification(
                initial_answer=initial_response,
                receipt_text=state.formatted_text,
                task_description="Currency amount classification (GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL)",
                response_model=Phase1Response,
                llm=llm,
                enable_cove=True,
            )
        else:
            response = initial_response

        # response is already Phase1Response - convert to CurrencyLabel
        currency_labels = [
            CurrencyLabel(
                line_text=item.line_text,
                amount=float(item.amount),  # Ensure float type
                label_type=getattr(CurrencyLabelType, item.label_type),
                line_ids=item.line_ids,
                confidence=float(item.confidence),  # Ensure float type
                reasoning=item.reasoning,
            )
            for item in response.currency_labels
        ]
        return {"currency_labels": currency_labels}
    except Exception as e:
        print(f"❌ Phase 1 failed after all retries: {e}")
        return {"currency_labels": []}

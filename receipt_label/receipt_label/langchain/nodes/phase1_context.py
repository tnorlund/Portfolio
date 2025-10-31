"""Phase 1.5: Transaction Context Analysis

Runs in parallel with Phase 1 to extract transaction-specific labels:
- DATE, TIME, PAYMENT_METHOD, COUPON, DISCOUNT, LOYALTY_ID
"""

from __future__ import annotations

from langchain_ollama import ChatOllama

from receipt_label.constants import CORE_LABELS
from receipt_label.langchain.models import (
    PhaseContextResponse,
    TransactionLabel,
    TransactionLabelType,
)
from receipt_label.langchain.state.currency_validation import (
    CurrencyAnalysisState,
)
from receipt_label.langchain.utils.retry import retry_with_backoff


async def phase1_context_analysis(
    state: CurrencyAnalysisState, ollama_api_key: str
):
    """Analyze transaction context (DATE, TIME, PAYMENT_METHOD, etc.)."""
    
    # Initialize LLM with structured outputs
    llm = ChatOllama(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"}
        },
        format="json",
        temperature=0.3,
        timeout=120,  # 2 minute timeout for reliability
    )
    
    # Bind to Pydantic model for structured outputs
    llm_structured = llm.with_structured_output(PhaseContextResponse)
    
    # Build label definitions from CORE_LABELS (single source of truth!)
    subset = [
        TransactionLabelType.DATE.value,
        TransactionLabelType.TIME.value,
        TransactionLabelType.PAYMENT_METHOD.value,
        TransactionLabelType.COUPON.value,
        TransactionLabelType.DISCOUNT.value,
        TransactionLabelType.LOYALTY_ID.value,
        TransactionLabelType.MERCHANT_NAME.value,
        TransactionLabelType.PHONE_NUMBER.value,
        TransactionLabelType.ADDRESS_LINE.value,
        TransactionLabelType.STORE_HOURS.value,         # NEW
        TransactionLabelType.WEBSITE.value,             # NEW
    ]
    # Use CORE_LABELS descriptions as-is
    subset_definitions = "\n".join(
        f"- {label}: {CORE_LABELS.get(label, 'N/A')}" 
        for label in subset 
        if label in CORE_LABELS
    )
    
    # We dynamically inject the JSON schema from the Pydantic model
    import json
    
    schema = PhaseContextResponse.model_json_schema()
    schema_json = json.dumps(schema, indent=2)
    
    # Build merchant context if available
    merchant_context = ""
    if state.receipt_metadata:
        metadata = state.receipt_metadata
        merchant_context = f"""

MERCHANT CONTEXT (for better accuracy):
- Merchant: {metadata.merchant_name}
- Category: {metadata.merchant_category or 'Unknown'}
- Address: {metadata.address or 'Unknown'}
- Phone: {metadata.phone_number or 'Unknown'}

Note: You already have merchant information from the above context. Focus on extracting 
transaction-specific information (DATE, TIME, PAYMENT_METHOD, etc.) that varies per receipt.
"""
    
    messages = [{
        "role": "user",
        "content": f"""You are analyzing a receipt to extract transaction context information.{merchant_context}

RECEIPT TEXT:
{state.formatted_text}

Find all transaction context information and classify them as:
{subset_definitions}

Look for:
1. DATE - Transaction date (usually top of receipt, formats like MM/DD/YYYY, DD/MM/YYYY, etc.)
2. TIME - Transaction time (usually near date, formats like HH:MM, HH:MM AM/PM)
3. PAYMENT_METHOD - Payment type (VISA, MASTERCARD, DEBIT, CASH, APPLE PAY, etc.)
4. COUPON - Coupon codes or descriptions
5. DISCOUNT - Discount lines (percentages, dollar amounts)
6. LOYALTY_ID - Member/rewards/customer numbers
7. MERCHANT_NAME - Store/business name printed on receipt
8. PHONE_NUMBER - Phone number printed on receipt
9. ADDRESS_LINE - Full address printed on receipt
10. STORE_HOURS - Business hours or opening times printed on receipt
11. WEBSITE - Web or email address printed on receipt

CRITICAL INSTRUCTIONS:
1. You MUST respond with valid JSON ONLY
2. NO markdown tables, NO text explanations
3. Output must match this EXACT JSON structure:

{schema_json}

Extract all transaction context labels and return them as JSON matching the schema above."""
    }]
    
    # Define invocation function for retry logic
    async def invoke_llm():
        return await llm_structured.ainvoke(
            messages,
            config={
                "metadata": {
                    "receipt_id": state.receipt_id,
                    "phase": "transaction_context",
                    "model": "120b",
                },
                "tags": ["phase1_context", "transaction", "receipt-analysis"],
            },
        )
    
    try:
        # Get structured response with retry logic
        response = await retry_with_backoff(
            invoke_llm,
            max_retries=3,
            initial_delay=2.0,  # Start with 2 second delay
        )
        
        # Convert to TransactionLabel objects
        transaction_labels = [
            TransactionLabel(
                word_text=item.word_text,
                label_type=getattr(TransactionLabelType, item.label_type),
                confidence=float(item.confidence),
                reasoning=item.reasoning,
            )
            for item in response.transaction_labels
        ]
        
        print(f"   ✅ Context: Found {len(transaction_labels)} transaction labels")
        return {"transaction_labels": transaction_labels}
    except Exception as e:
        print(f"❌ Phase 1 Context failed after all retries: {e}")
        return {"transaction_labels": []}


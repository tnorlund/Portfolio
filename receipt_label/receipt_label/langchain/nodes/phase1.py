from __future__ import annotations

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
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


async def phase1_currency_analysis(
    state: CurrencyAnalysisState, ollama_api_key: str
):
    """Analyze currency amounts (GRAND_TOTAL, TAX, etc.)."""

    llm = ChatOllama(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"}
        },
    )

    subset = [
        CurrencyLabelType.GRAND_TOTAL.value,
        CurrencyLabelType.TAX.value,
        CurrencyLabelType.SUBTOTAL.value,
        CurrencyLabelType.LINE_TOTAL.value,
    ]
    subset_definitions = "\n".join(
        f"- {l}: {CORE_LABELS[l]}" for l in subset if l in CORE_LABELS
    )

    template = """You are analyzing a receipt to classify currency amounts.

RECEIPT TEXT:
{receipt_text}

Find all currency amounts and classify them as:
{subset_definitions}

Focus on the most obvious currency amounts first.

{format_instructions}"""
    output_parser = PydanticOutputParser(pydantic_object=Phase1Response)
    prompt = PromptTemplate(
        template=template,
        input_variables=["receipt_text", "subset_definitions"],
        partial_variables={
            "format_instructions": output_parser.get_format_instructions()
        },
    )

    chain = prompt | llm | output_parser
    try:
        response = await chain.ainvoke(
            {
                "receipt_text": state.formatted_text,
                "subset_definitions": subset_definitions,
            },
            config={
                "metadata": {
                    "receipt_id": state.receipt_id,
                    "phase": "currency_analysis",
                    "model": "120b",
                },
                "tags": ["phase1", "currency", "receipt-analysis"],
            },
        )
        currency_labels = [
            CurrencyLabel(
                line_text=item.line_text,
                amount=item.amount,
                label_type=getattr(CurrencyLabelType, item.label_type),
                line_ids=item.line_ids,
                confidence=item.confidence,
                reasoning=item.reasoning,
            )
            for item in response.currency_labels
        ]
        return {"currency_labels": currency_labels}
    except Exception as e:
        print(f"Phase 1 failed: {e}")
        return {"currency_labels": []}

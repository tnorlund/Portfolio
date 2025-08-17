#!/usr/bin/env python3
"""
Real Example: How to validate receipt labels with LangChain

This is a practical example showing how to use the LangChain validation system.
"""

import asyncio
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from typing import List, Optional

# Load environment variables
load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")


# Define the validation schema
class LabelValidationResult(BaseModel):
    """Single label validation result"""
    word: str = Field(description="The word being validated")
    current_label: str = Field(description="The current label assigned")
    is_valid: bool = Field(description="True if the label is correct")
    correct_label: Optional[str] = Field(
        default=None,
        description="The correct label if current is wrong"
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence score 0-1"
    )


class ValidateLabelsInput(BaseModel):
    """Input for the validate_labels tool"""
    results: List[LabelValidationResult]


@tool
def validate_labels(input: ValidateLabelsInput) -> dict:
    """Validate multiple receipt-word labels in one batch"""
    valid_count = sum(1 for r in input.results if r.is_valid)
    total_count = len(input.results)
    
    return {
        "summary": f"Validated {total_count} labels: {valid_count} valid, {total_count - valid_count} need correction",
        "results": [r.dict() for r in input.results]
    }


# Sample receipt data (simulating what would come from DynamoDB)
SAMPLE_RECEIPT = {
    "merchant": "WALMART",
    "lines": [
        "WALMART",
        "Store #1234",
        "123 Main St",
        "Your City, ST 12345",
        "",
        "GROCERY",
        "BREAD      2.99",
        "MILK       3.49", 
        "EGGS       4.99",
        "",
        "SUBTOTAL  11.47",
        "TAX        0.92",
        "TOTAL     12.39",
        "",
        "CASH      20.00",
        "CHANGE     7.61"
    ],
    "labels_to_validate": [
        {"word": "WALMART", "label": "MERCHANT_NAME", "line": 1},
        {"word": "2.99", "label": "UNIT_PRICE", "line": 7},
        {"word": "MILK", "label": "PRODUCT_NAME", "line": 8},
        {"word": "SUBTOTAL", "label": "SUBTOTAL_AMOUNT", "line": 11},  # Wrong - should be LABEL_INDICATOR
        {"word": "11.47", "label": "SUBTOTAL_AMOUNT", "line": 11},  # Correct
        {"word": "TOTAL", "label": "GRAND_TOTAL", "line": 13},  # Wrong - should be LABEL_INDICATOR
        {"word": "12.39", "label": "GRAND_TOTAL", "line": 13},  # Correct
    ]
}

# Valid labels (from receipt_label.constants)
VALID_LABELS = [
    "MERCHANT_NAME", "MERCHANT_ADDRESS", "MERCHANT_PHONE",
    "DATE", "TIME", 
    "PRODUCT_NAME", "PRODUCT_DESCRIPTION", "PRODUCT_CODE",
    "QUANTITY", "UNIT_PRICE", "LINE_TOTAL",
    "SUBTOTAL_AMOUNT", "TAX_AMOUNT", "GRAND_TOTAL",
    "PAYMENT_METHOD", "PAYMENT_AMOUNT", "CHANGE_AMOUNT",
    "LABEL_INDICATOR",  # For words like "TOTAL", "SUBTOTAL", "TAX"
    "TRANSACTION_ID", "STORE_NUMBER",
    "OTHER"
]


async def validate_with_openai():
    """Validate receipt labels using OpenAI with tool calling"""
    
    if not OPENAI_API_KEY or OPENAI_API_KEY == "YOUR_OPENAI_API_KEY":
        print("❌ OpenAI API key not configured")
        return
    
    print("\n🤖 Validating with OpenAI GPT-4o-mini")
    print("=" * 50)
    
    # Initialize LLM with tools
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=OPENAI_API_KEY
    ).bind_tools([validate_labels])
    
    # Format the receipt context
    receipt_text = "\n".join(SAMPLE_RECEIPT["lines"])
    
    # Create validation prompt
    prompt = f"""You are validating labels for words extracted from a receipt.

RECEIPT:
{receipt_text}

LABELS TO VALIDATE:
{chr(10).join(f"- Line {l['line']}: '{l['word']}' → {l['label']}" for l in SAMPLE_RECEIPT['labels_to_validate'])}

VALIDATION RULES:
1. MERCHANT_NAME: Store/business name (e.g., "WALMART", "McDonald's")
2. PRODUCT_NAME: Actual product names (e.g., "MILK", "BREAD")
3. UNIT_PRICE/LINE_TOTAL: Numerical amounts for products
4. SUBTOTAL_AMOUNT/TAX_AMOUNT/GRAND_TOTAL: Numerical amounts for totals
5. LABEL_INDICATOR: Words that indicate what follows (e.g., "TOTAL", "SUBTOTAL", "TAX" when not followed by amount on same line)
6. Amounts should be the numbers, not the descriptive text

Valid labels: {', '.join(VALID_LABELS)}

Use the validate_labels tool to provide your validation results for each label.
"""
    
    # Get validation
    response = await llm.ainvoke(prompt)
    
    # Process results
    if response.tool_calls:
        for tool_call in response.tool_calls:
            if tool_call["name"] == "validate_labels":
                print("\n📊 Validation Results:")
                print("-" * 40)
                
                # Handle the args structure properly
                args = tool_call.get("args", {})
                if "input" in args:
                    results = args["input"].get("results", [])
                elif "results" in args:
                    results = args["results"]
                else:
                    print(f"Unexpected args structure: {args}")
                    continue
                
                for result in results:
                    status = "✅" if result.get("is_valid", False) else "❌"
                    word = result.get("word", "?")
                    current_label = result.get("current_label", result.get("label", "?"))
                    print(f"{status} '{word}' as {current_label}", end="")
                    
                    if not result["is_valid"] and result.get("correct_label"):
                        print(f" → should be {result['correct_label']}")
                    else:
                        print()
                
                # Summary
                valid = sum(1 for r in results if r["is_valid"])
                total = len(results)
                print(f"\nSummary: {valid}/{total} labels correct ({valid/total*100:.0f}%)")
    else:
        print("No tool calls in response:", response.content)


async def validate_with_ollama():
    """Validate receipt labels using Ollama (local LLM)"""
    
    print("\n🦙 Validating with Ollama")
    print("=" * 50)
    
    try:
        from langchain_ollama import ChatOllama
        
        # Initialize Ollama
        llm = ChatOllama(
            base_url=OLLAMA_BASE_URL,
            model=OLLAMA_MODEL,
            temperature=0
        )
        
        # Test connection
        print(f"Testing connection to {OLLAMA_BASE_URL}...")
        test = await llm.ainvoke("Say OK")
        print(f"✅ Ollama connected: {test.content[:20]}")
        
        # Create simpler prompt for Ollama
        prompt = f"""Validate these receipt labels. Reply with VALID or INVALID and correction if needed.

Receipt:
{chr(10).join(SAMPLE_RECEIPT['lines'][:10])}  # Just first 10 lines for brevity

Labels to check:
1. "WALMART" labeled as MERCHANT_NAME
2. "2.99" labeled as UNIT_PRICE  
3. "SUBTOTAL" labeled as SUBTOTAL_AMOUNT
4. "11.47" labeled as SUBTOTAL_AMOUNT

For each, say if the label is correct. Remember:
- MERCHANT_NAME = store name
- UNIT_PRICE = product price
- SUBTOTAL_AMOUNT = the number, not the word "SUBTOTAL"
- The word "SUBTOTAL" should be LABEL_INDICATOR"""
        
        response = await llm.ainvoke(prompt)
        print("\n📊 Validation Results:")
        print("-" * 40)
        print(response.content)
        
    except ImportError:
        print("❌ langchain-ollama not installed")
    except Exception as e:
        print(f"❌ Ollama error: {e}")
        print("\nTip: Start Ollama with: ollama serve")
        print("Then pull a model: ollama pull llama3.1:8b")


async def main():
    """Run validation examples"""
    
    print("\n🚀 Receipt Label Validation Example")
    print("=" * 50)
    
    # Show configuration
    print("\n📋 Configuration:")
    print(f"  OpenAI: {'✅ Configured' if OPENAI_API_KEY and OPENAI_API_KEY != 'YOUR_OPENAI_API_KEY' else '❌ Not configured'}")
    print(f"  Ollama: {OLLAMA_BASE_URL}")
    
    # Run validations
    if OPENAI_API_KEY and OPENAI_API_KEY != "YOUR_OPENAI_API_KEY":
        await validate_with_openai()
    
    # Uncomment to test with Ollama
    # await validate_with_ollama()
    
    print("\n" + "=" * 50)
    print("✅ Example Complete!")
    print("\n📚 Next Steps:")
    print("1. This example shows the core validation logic")
    print("2. In production, receipt data comes from DynamoDB")
    print("3. The full graph handles fetching context and updating results")
    print("4. See migration_guide.md for production deployment")


if __name__ == "__main__":
    # Disable LangSmith tracing if not configured
    if not os.getenv("LANGCHAIN_API_KEY"):
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
    
    asyncio.run(main())
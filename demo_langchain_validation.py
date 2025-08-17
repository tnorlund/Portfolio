#!/usr/bin/env python3
"""
Demo: How to use LangChain validation with your API keys

This script shows real examples of using the LangChain validation system
with both Ollama and OpenAI providers.
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add the receipt_label package to path
sys.path.insert(0, str(Path(__file__).parent / "receipt_label"))

# Load environment variables
load_dotenv()

# Sample receipt data for testing
SAMPLE_LABELS = [
    {
        "image_id": "IMG_001",
        "receipt_id": 1,
        "line_id": 1,
        "word_id": 1,
        "text": "WALMART",
        "label": "MERCHANT_NAME",
        "validation_status": "NONE"
    },
    {
        "image_id": "IMG_001", 
        "receipt_id": 1,
        "line_id": 5,
        "word_id": 2,
        "text": "12.99",
        "label": "UNIT_PRICE",
        "validation_status": "NONE"
    },
    {
        "image_id": "IMG_001",
        "receipt_id": 1,
        "line_id": 10,
        "word_id": 3,
        "text": "TOTAL",
        "label": "GRAND_TOTAL",  # This might be wrong - should be a label indicator
        "validation_status": "NONE"
    }
]

SAMPLE_LINES = [
    {"line_id": 1, "text": "WALMART", "top_left": {"y": 100}, "bottom_left": {"y": 80}},
    {"line_id": 2, "text": "Store #1234", "top_left": {"y": 80}, "bottom_left": {"y": 60}},
    {"line_id": 5, "text": "BREAD 12.99", "top_left": {"y": 50}, "bottom_left": {"y": 30}},
    {"line_id": 10, "text": "TOTAL 45.67", "top_left": {"y": 20}, "bottom_left": {"y": 0}}
]

SAMPLE_WORDS = [
    {"line_id": 1, "word_id": 1, "text": "WALMART"},
    {"line_id": 5, "word_id": 2, "text": "12.99"},
    {"line_id": 10, "word_id": 3, "text": "TOTAL"}
]


async def demo_with_ollama():
    """Demo using Ollama (local LLM)"""
    print("\n🦙 Demo: Validation with Ollama")
    print("=" * 50)
    
    try:
        from langchain_ollama import ChatOllama
        
        # Configure Ollama
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
        
        print(f"Using Ollama at: {ollama_url}")
        print(f"Model: {ollama_model}")
        
        # Create LLM instance
        llm = ChatOllama(
            base_url=ollama_url,
            model=ollama_model,
            temperature=0
        )
        
        # Test connection
        print("\nTesting Ollama connection...")
        test_response = await llm.ainvoke("Respond with 'OK' if you can read this.")
        print(f"✓ Ollama is working: {test_response.content[:50]}")
        
        # Now let's validate some labels
        print("\n📋 Validating Receipt Labels with Ollama:")
        
        # Create a simple validation prompt
        prompt = f"""
        You are validating receipt labels. Here are the labels to check:
        
        1. Word: "WALMART" → Label: "MERCHANT_NAME" 
        2. Word: "12.99" → Label: "UNIT_PRICE"
        3. Word: "TOTAL" → Label: "GRAND_TOTAL"
        
        For each label, determine if it's correct. 
        - "WALMART" as MERCHANT_NAME is correct
        - "12.99" as UNIT_PRICE is likely correct
        - "TOTAL" as GRAND_TOTAL is wrong (TOTAL is a label indicator, not a value)
        
        Respond with your validation in this format:
        1. WALMART/MERCHANT_NAME: Valid
        2. 12.99/UNIT_PRICE: Valid  
        3. TOTAL/GRAND_TOTAL: Invalid (should be LABEL_INDICATOR or similar)
        """
        
        response = await llm.ainvoke(prompt)
        print(f"\nValidation Results:\n{response.content}")
        
    except ImportError:
        print("❌ langchain-ollama not installed. Run: pip install langchain-ollama")
    except Exception as e:
        print(f"❌ Error with Ollama: {e}")
        print("\n💡 Tips:")
        print("1. Make sure Ollama is running: ollama serve")
        print("2. Pull a model if needed: ollama pull llama3.1:8b")
        print("3. Check your OLLAMA_BASE_URL in .env")


async def demo_with_openai():
    """Demo using OpenAI GPT"""
    print("\n🤖 Demo: Validation with OpenAI")
    print("=" * 50)
    
    openai_key = os.getenv("OPENAI_API_KEY", "")
    
    if not openai_key or openai_key == "YOUR_OPENAI_API_KEY":
        print("❌ OpenAI API key not set in .env")
        print("   Set OPENAI_API_KEY in your .env file to use OpenAI")
        return
    
    try:
        from langchain_openai import ChatOpenAI
        
        print("Using OpenAI GPT-4o-mini")
        
        # Create LLM instance
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=openai_key
        )
        
        # Test connection
        print("\nTesting OpenAI connection...")
        test_response = await llm.ainvoke("Respond with 'OK' if you can read this.")
        print(f"✓ OpenAI is working: {test_response.content}")
        
        # Validate with tool calling
        print("\n📋 Validating Receipt Labels with OpenAI (using tools):")
        
        from langchain_core.tools import tool
        from pydantic import BaseModel, Field
        from typing import List
        
        class ValidationResult(BaseModel):
            word: str = Field(description="The word being validated")
            label: str = Field(description="The proposed label")
            is_valid: bool = Field(description="Whether the label is correct")
            correct_label: str = Field(default=None, description="The correct label if invalid")
        
        class ValidateLabelsInput(BaseModel):
            results: List[ValidationResult] = Field(description="Validation results")
        
        @tool
        def validate_labels(input: ValidateLabelsInput) -> dict:
            """Validate receipt labels"""
            return {"results": [r.dict() for r in input.results]}
        
        # Bind tool to LLM
        llm_with_tools = llm.bind_tools([validate_labels])
        
        prompt = """
        Validate these receipt word labels:
        1. "WALMART" labeled as MERCHANT_NAME
        2. "12.99" labeled as UNIT_PRICE  
        3. "TOTAL" labeled as GRAND_TOTAL
        
        Use the validate_labels tool to provide your validation.
        Note: "TOTAL" is a label indicator, not a value.
        """
        
        response = await llm_with_tools.ainvoke(prompt)
        
        if response.tool_calls:
            print("\n✓ Tool called successfully!")
            for tool_call in response.tool_calls:
                print(f"\nTool: {tool_call['name']}")
                print("Results:")
                for result in tool_call['args']['results']:
                    status = "✓" if result['is_valid'] else "✗"
                    print(f"  {status} {result['word']}/{result['label']}", end="")
                    if not result['is_valid'] and result.get('correct_label'):
                        print(f" → should be {result['correct_label']}")
                    else:
                        print()
        else:
            print("Response (no tool calls):", response.content)
            
    except ImportError:
        print("❌ langchain-openai not installed. Run: pip install langchain-openai")
    except Exception as e:
        print(f"❌ Error with OpenAI: {e}")


async def demo_full_graph():
    """Demo using the full validation graph (requires DynamoDB)"""
    print("\n🔄 Demo: Full Validation Graph")
    print("=" * 50)
    
    try:
        # Import from the actual module
        from receipt_label.langchain_validation.graph_design import (
            ValidationState,
            format_validation_prompt
        )
        
        print("Creating validation state...")
        
        # Create a mock state that doesn't require DynamoDB
        state = ValidationState(
            image_id="IMG_001",
            receipt_id=1,
            labels_to_validate=SAMPLE_LABELS,
            receipt_lines=SAMPLE_LINES,
            receipt_words=SAMPLE_WORDS,
            receipt_metadata={"merchant_name": "Walmart"},
            all_labels=SAMPLE_LABELS,
            first_pass_labels=SAMPLE_LABELS,
            second_pass_labels=[],
            completed=False
        )
        
        # Format the prompt
        print("\nFormatting validation prompt...")
        formatted_state = await format_validation_prompt(state)
        
        print("\n📄 Generated Prompt:")
        print("-" * 40)
        print(formatted_state["formatted_prompt"][:500] + "...")
        
        print("\n✓ Graph components are working!")
        print("\nNote: Full graph execution requires:")
        print("  - DynamoDB connection for fetching receipt data")
        print("  - Proper AWS credentials in .env")
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("\nMake sure you're in the correct directory")
    except Exception as e:
        print(f"❌ Error: {e}")


async def main():
    """Run all demos"""
    print("\n🚀 LangChain Receipt Validation Demo")
    print("=" * 50)
    
    # Check environment
    print("\n📋 Environment Check:")
    langchain_key = os.getenv("LANGCHAIN_API_KEY", "")
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    
    print(f"  LANGCHAIN_API_KEY: {'✓ Set' if langchain_key and langchain_key != 'YOUR_LANGCHAIN_API_KEY' else '✗ Not set'}")
    print(f"  OLLAMA_BASE_URL: {ollama_url}")
    print(f"  OPENAI_API_KEY: {'✓ Set' if openai_key and openai_key != 'YOUR_OPENAI_API_KEY' else '✗ Not set'}")
    
    # Run demos based on what's configured
    if ollama_url:
        await demo_with_ollama()
    
    if openai_key and openai_key != "YOUR_OPENAI_API_KEY":
        await demo_with_openai()
    
    # Always try the graph demo
    await demo_full_graph()
    
    print("\n" + "=" * 50)
    print("✅ Demo Complete!")
    print("\n📚 Next Steps:")
    print("1. Update .env with your API keys")
    print("2. Start Ollama if using local models")
    print("3. Check the full implementation in:")
    print("   receipt_label/receipt_label/langchain_validation/")
    print("4. Read the migration guide for production deployment")


if __name__ == "__main__":
    asyncio.run(main())
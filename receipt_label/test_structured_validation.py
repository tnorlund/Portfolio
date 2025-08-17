#!/usr/bin/env python3
"""
Test script for structured validation with Pydantic models
"""

import asyncio
import os
import sys

# Add the parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set environment for local Ollama (change these for Ollama Turbo)
os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
os.environ["OLLAMA_MODEL"] = "llama3.1:8b"


async def test_structured_validation():
    """Test the structured validation implementation"""
    from receipt_label.langchain_validation import (
        test_ollama_connection,
        ValidationResponse,
        ValidationResult,
    )
    from receipt_label.langchain_validation.graph_design import (
        MinimalValidationState,
        validate_with_ollama,
    )
    
    print("=" * 60)
    print("TESTING STRUCTURED VALIDATION")
    print("=" * 60)
    
    # Test 1: Connection test
    print("\n1. Testing Ollama connection and structured output support...")
    if not await test_ollama_connection():
        print("   Failed to connect to Ollama")
        return
    
    # Test 2: Test structured validation
    print("\n2. Testing structured validation with sample data...")
    
    # Create test state
    test_state = MinimalValidationState(
        formatted_prompt="""You are validating receipt labels.

MERCHANT: Walmart

ALLOWED LABELS: MERCHANT_NAME, TOTAL, SUBTOTAL, TAX, PRODUCT_NAME, QUANTITY, UNIT_PRICE, DATE, TIME

TARGETS TO VALIDATE:
[
  {
    "id": "IMAGE#test123#RECEIPT#00001#LINE#00001#WORD#00001#LABEL#MERCHANT_NAME",
    "text": "Walmart",
    "proposed_label": "MERCHANT_NAME"
  },
  {
    "id": "IMAGE#test123#RECEIPT#00001#LINE#00010#WORD#00002#LABEL#TOTAL",
    "text": "$45.99",
    "proposed_label": "TOTAL"
  },
  {
    "id": "IMAGE#test123#RECEIPT#00001#LINE#00008#WORD#00001#LABEL#SUBTOTAL",
    "text": "$42.00",
    "proposed_label": "SUBTOTAL"
  }
]

TASK: For each target, determine if the proposed_label is correct.
Return a JSON object with a "results" array. Each result must have:
- "id": the exact id from the target
- "is_valid": true if the label is correct, false otherwise
- "correct_label": (only if is_valid is false) the correct label from ALLOWED LABELS
- "confidence": (optional) confidence score between 0 and 1
- "reasoning": (optional) brief explanation

Example response:
{"results": [
    {"id": "IMAGE#abc#RECEIPT#00001#...", "is_valid": true, "confidence": 0.95},
    {"id": "IMAGE#xyz#RECEIPT#00002#...", "is_valid": false, "correct_label": "SUBTOTAL", "reasoning": "Appears before tax"}
]}""",
        validation_targets=[
            {
                "id": "IMAGE#test123#RECEIPT#00001#LINE#00001#WORD#00001#LABEL#MERCHANT_NAME",
                "text": "Walmart",
                "proposed_label": "MERCHANT_NAME"
            },
            {
                "id": "IMAGE#test123#RECEIPT#00001#LINE#00010#WORD#00002#LABEL#TOTAL",
                "text": "$45.99",
                "proposed_label": "TOTAL"
            },
            {
                "id": "IMAGE#test123#RECEIPT#00001#LINE#00008#WORD#00001#LABEL#SUBTOTAL",
                "text": "$42.00",
                "proposed_label": "SUBTOTAL"
            }
        ],
        validation_results=[],
        validation_response=None,
        error=None,
        completed=False,
    )
    
    try:
        # Run validation
        result_state = await validate_with_ollama(test_state)
        
        print(f"\n3. Validation Results:")
        print(f"   Completed: {result_state['completed']}")
        
        if result_state.get('error'):
            print(f"   Error: {result_state['error']}")
        
        # Check if we got structured response
        if result_state.get('validation_response'):
            response = result_state['validation_response']
            print(f"   ✅ Got structured ValidationResponse object!")
            print(f"   Type: {type(response)}")
            
            if isinstance(response, ValidationResponse):
                print(f"\n   Statistics:")
                print(f"   - Total validated: {response.total_validated}")
                print(f"   - Valid count: {response.valid_count}")
                print(f"   - Invalid count: {response.invalid_count}")
                if response.average_confidence:
                    print(f"   - Average confidence: {response.average_confidence:.2f}")
                
                print(f"\n   Individual Results:")
                for r in response.results:
                    print(f"   - {r.id[:50]}...")
                    print(f"     Valid: {r.is_valid}")
                    if r.confidence:
                        print(f"     Confidence: {r.confidence:.2f}")
                    if r.reasoning:
                        print(f"     Reasoning: {r.reasoning}")
                    if r.correct_label:
                        print(f"     Suggested: {r.correct_label}")
        else:
            print(f"   ⚠️ Fell back to JSON mode (no structured response)")
            print(f"   Results: {result_state.get('validation_results', [])}")
        
    except Exception as e:
        print(f"\n   ❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("Test complete!")
    
    # Test 3: Test Pydantic validation
    print("\n4. Testing Pydantic model validation...")
    
    try:
        # Valid result
        valid = ValidationResult(
            id="IMAGE#test#RECEIPT#00001#LINE#00001#WORD#00001#LABEL#TEST",
            is_valid=True,
            confidence=0.95
        )
        print(f"   ✅ Valid model created: {valid.id[:30]}...")
        
        # Invalid ID format should raise error
        try:
            invalid = ValidationResult(
                id="BADFORMAT",
                is_valid=True
            )
            print(f"   ❌ Should have raised validation error for bad ID")
        except ValueError as e:
            print(f"   ✅ Correctly rejected bad ID format: {e}")
        
        # correct_label without is_valid=False should raise error
        try:
            invalid = ValidationResult(
                id="IMAGE#test#RECEIPT#00001#LINE#00001#WORD#00001#LABEL#TEST",
                is_valid=True,
                correct_label="OTHER"  # Should fail - only allowed when is_valid=False
            )
            print(f"   ❌ Should have raised validation error for correct_label with is_valid=True")
        except ValueError as e:
            print(f"   ✅ Correctly rejected correct_label with is_valid=True")
            
    except Exception as e:
        print(f"   ❌ Pydantic validation test failed: {e}")


if __name__ == "__main__":
    asyncio.run(test_structured_validation())
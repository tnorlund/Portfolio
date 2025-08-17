#!/usr/bin/env python3
"""
Test Ollama Turbo + LangSmith Integration

This script tests:
1. Ollama Turbo connection
2. LangSmith graph tracing
3. Receipt validation workflow
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add receipt_label to path
sys.path.insert(0, str(Path(__file__).parent / "receipt_label"))

# Check configuration
print("🔧 Configuration Check")
print("=" * 50)
print(f"OLLAMA_API_KEY: {'✅ Set' if os.getenv('OLLAMA_API_KEY') else '❌ Not set'}")
print(f"LANGCHAIN_API_KEY: {'✅ Set' if os.getenv('LANGCHAIN_API_KEY') else '❌ Not set'}")
print(f"LANGCHAIN_TRACING_V2: {os.getenv('LANGCHAIN_TRACING_V2', 'false')}")
print(f"LANGCHAIN_PROJECT: {os.getenv('LANGCHAIN_PROJECT', 'receipt-validation')}")
print()

# Enable LangSmith tracing
if os.getenv('LANGCHAIN_API_KEY'):
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    print("✅ LangSmith tracing enabled")
    print(f"   View traces at: https://smith.langchain.com/o/YOUR_ORG/projects/p/{os.getenv('LANGCHAIN_PROJECT', 'receipt-validation')}")
else:
    print("⚠️  LangSmith tracing disabled (no API key)")
print()


async def test_ollama_turbo():
    """Test Ollama Turbo connection"""
    print("🦙 Testing Ollama Turbo")
    print("-" * 40)
    
    try:
        import httpx
        
        base_url = os.getenv("OLLAMA_BASE_URL", "https://api.ollama.ai")
        api_key = os.getenv("OLLAMA_API_KEY")
        model = os.getenv("OLLAMA_MODEL", "turbo")
        
        if not api_key:
            print("❌ OLLAMA_API_KEY not set")
            return False
        
        # Test connection
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {api_key}"}
            response = await client.post(
                f"{base_url}/api/generate",
                headers=headers,
                json={
                    "model": model,
                    "prompt": "Say 'Connected!'",
                    "stream": False
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                print(f"✅ Ollama Turbo connected!")
                print(f"   Model: {model}")
                print(f"   Response: {response.json().get('response', '')[:50]}")
                return True
            else:
                print(f"❌ Connection failed: {response.status_code}")
                return False
                
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def test_langchain_graph():
    """Test the LangChain graph with Ollama"""
    print("\n🔄 Testing LangChain Graph")
    print("-" * 40)
    
    try:
        from langchain_community.llms import Ollama
        from langgraph.graph import StateGraph, END
        from typing import TypedDict, List
        import json
        
        # Define a simple state
        class ValidationState(TypedDict):
            input: str
            labels: List[dict]
            validation_results: List[dict]
            output: str
        
        # Create Ollama LLM
        base_url = os.getenv("OLLAMA_BASE_URL", "https://api.ollama.ai")
        api_key = os.getenv("OLLAMA_API_KEY")
        model = os.getenv("OLLAMA_MODEL", "turbo")
        
        print(f"Creating Ollama LLM with model: {model}")
        
        # For Ollama Turbo, we need to use httpx directly or a custom client
        # since langchain-community's Ollama doesn't support auth headers yet
        
        # Let's create a custom async function that uses Ollama Turbo
        async def validate_labels(state: ValidationState) -> ValidationState:
            """Validate receipt labels using Ollama Turbo"""
            import httpx
            
            prompt = f"""Validate these receipt labels:
            {json.dumps(state['labels'], indent=2)}
            
            Reply with VALID or INVALID for each."""
            
            async with httpx.AsyncClient() as client:
                headers = {"Authorization": f"Bearer {api_key}"}
                response = await client.post(
                    f"{base_url}/api/generate",
                    headers=headers,
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False
                    },
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    result = response.json().get('response', '')
                    state['validation_results'] = [{"result": result}]
                    state['output'] = f"Validation complete: {result[:100]}"
                else:
                    state['output'] = f"Validation failed: {response.status_code}"
            
            return state
        
        # Build the graph
        workflow = StateGraph(ValidationState)
        
        # Add nodes
        workflow.add_node("validate", validate_labels)
        
        # Add edges
        workflow.add_edge("validate", END)
        
        # Set entry point
        workflow.set_entry_point("validate")
        
        # Compile
        app = workflow.compile()
        
        print("✅ Graph compiled successfully")
        
        # Test with sample data
        test_input = {
            "input": "Test validation",
            "labels": [
                {"word": "WALMART", "label": "MERCHANT_NAME"},
                {"word": "12.99", "label": "UNIT_PRICE"}
            ],
            "validation_results": [],
            "output": ""
        }
        
        print("\nRunning validation...")
        result = await app.ainvoke(test_input)
        
        print(f"✅ Validation complete!")
        print(f"   Output: {result['output'][:100]}")
        
        if os.getenv('LANGCHAIN_API_KEY'):
            print(f"\n📊 View trace in LangSmith:")
            print(f"   https://smith.langchain.com")
        
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("   Run: pip install langgraph langchain-community")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def test_full_validation_pipeline():
    """Test the complete validation pipeline"""
    print("\n🏭 Testing Full Validation Pipeline")
    print("-" * 40)
    
    try:
        # Import the actual validation module
        from receipt_label.langchain_validation.graph_design import (
            ValidationState,
            format_validation_prompt
        )
        
        # Create test state
        test_state = ValidationState(
            image_id="TEST_001",
            receipt_id=1,
            labels_to_validate=[
                {
                    "image_id": "TEST_001",
                    "receipt_id": 1,
                    "line_id": 1,
                    "word_id": 1,
                    "text": "WALMART",
                    "label": "MERCHANT_NAME",
                    "validation_status": "NONE"
                },
                {
                    "image_id": "TEST_001",
                    "receipt_id": 1,
                    "line_id": 5,
                    "word_id": 2,
                    "text": "12.99",
                    "label": "UNIT_PRICE",
                    "validation_status": "NONE"
                }
            ],
            receipt_lines=[
                {"line_id": 1, "text": "WALMART", "top_left": {"y": 100}, "bottom_left": {"y": 80}},
                {"line_id": 5, "text": "BREAD 12.99", "top_left": {"y": 50}, "bottom_left": {"y": 30}}
            ],
            receipt_words=[
                {"line_id": 1, "word_id": 1, "text": "WALMART"},
                {"line_id": 5, "word_id": 2, "text": "12.99"}
            ],
            receipt_metadata={"merchant_name": "Walmart"},
            all_labels=[],
            first_pass_labels=[],
            second_pass_labels=[],
            completed=False
        )
        
        # Test prompt formatting
        print("Testing prompt formatting...")
        formatted = await format_validation_prompt(test_state)
        print(f"✅ Prompt formatted: {len(formatted['formatted_prompt'])} chars")
        
        print("\n📝 Sample of formatted prompt:")
        print(formatted['formatted_prompt'][:300] + "...")
        
        return True
        
    except ImportError as e:
        print(f"⚠️  Module not found: {e}")
        print("   This is expected if not in the right directory")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def main():
    """Run all tests"""
    print("\n🚀 Ollama Turbo + LangSmith Integration Test")
    print("=" * 50)
    
    # Test Ollama Turbo
    ollama_ok = await test_ollama_turbo()
    
    if ollama_ok:
        # Test LangChain graph
        graph_ok = await test_langchain_graph()
        
        # Test full pipeline
        pipeline_ok = await test_full_validation_pipeline()
        
        print("\n" + "=" * 50)
        print("📊 Test Summary:")
        print(f"   Ollama Turbo: {'✅' if ollama_ok else '❌'}")
        print(f"   LangChain Graph: {'✅' if graph_ok else '❌'}")
        print(f"   Full Pipeline: {'✅' if pipeline_ok else '❌'}")
    else:
        print("\n⚠️  Fix Ollama Turbo connection first")
    
    print("\n📚 Next Steps:")
    print("1. Ensure both API keys are set in .env:")
    print("   - OLLAMA_API_KEY")
    print("   - LANGCHAIN_API_KEY")
    print("2. Run this test to verify everything works")
    print("3. Check LangSmith dashboard for traces")
    print("4. Use the full validation system with your data")


if __name__ == "__main__":
    asyncio.run(main())
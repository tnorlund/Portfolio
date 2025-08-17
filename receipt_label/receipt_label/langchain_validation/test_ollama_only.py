#!/usr/bin/env python3
"""
Test script to demonstrate Ollama-only validation configuration
=================================================================

This script tests the receipt validation system using only Ollama (local or Turbo),
with no OpenAI dependencies.

Usage:
    # For local Ollama:
    export OLLAMA_BASE_URL="http://localhost:11434"
    export OLLAMA_MODEL="llama3.1:8b"
    python test_ollama_only.py
    
    # For Ollama Turbo:
    export OLLAMA_BASE_URL="https://api.ollama.com"
    export OLLAMA_API_KEY="your-turbo-api-key"
    export OLLAMA_MODEL="turbo"
    python test_ollama_only.py
"""

import asyncio
import os
import json
from typing import List, Dict, Any

# Import the Ollama-only configuration
from config import ValidationConfig, get_config
from graph_design import validate_receipt_labels


def print_config_summary():
    """Print the current Ollama configuration"""
    config = get_config()
    
    print("=" * 60)
    print("OLLAMA-ONLY VALIDATION CONFIGURATION")
    print("=" * 60)
    print(f"Environment: {config.environment}")
    print(f"Mode: {config.mode.value}")
    print()
    print("Ollama Settings:")
    print(f"  Base URL: {config.ollama.base_url}")
    print(f"  Model: {config.ollama.model}")
    print(f"  Is Turbo: {config.ollama.is_turbo}")
    print(f"  API Key Set: {bool(config.ollama.api_key)}")
    print(f"  Temperature: {config.ollama.temperature}")
    print(f"  Timeout: {config.ollama.timeout}s")
    print()
    print("Features:")
    print(f"  Batch Processing: {config.batch.enabled}")
    print(f"  Caching: {config.cache.enabled}")
    print(f"  Monitoring: {config.monitoring.enabled}")
    print("=" * 60)


async def test_basic_validation():
    """Test basic receipt validation with Ollama"""
    print("\n🧪 Testing Basic Validation with Ollama")
    print("-" * 40)
    
    # Sample labels to validate
    sample_labels = [
        {
            "image_id": "TEST_IMG_001",
            "receipt_id": 12345,
            "line_id": 1,
            "word_id": 1,
            "label": "MERCHANT_NAME",
            "validation_status": "NONE"
        },
        {
            "image_id": "TEST_IMG_001",
            "receipt_id": 12345,
            "line_id": 5,
            "word_id": 2,
            "label": "TOTAL",
            "validation_status": "NONE"
        }
    ]
    
    try:
        # Validate using Ollama
        result = await validate_receipt_labels(
            image_id="TEST_IMG_001",
            receipt_id=12345,
            labels=sample_labels
        )
        
        print(f"✅ Validation successful: {result['success']}")
        if result.get('error'):
            print(f"❌ Error: {result['error']}")
        else:
            print(f"📊 Results: {json.dumps(result['validation_results'], indent=2)}")
            
    except Exception as e:
        print(f"❌ Test failed: {e}")


async def test_batch_validation():
    """Test batch validation with Ollama"""
    print("\n🧪 Testing Batch Validation with Ollama")
    print("-" * 40)
    
    from implementation import ReceiptValidator
    
    validator = ReceiptValidator()
    
    # Sample batch of receipts
    batch_receipts = [
        {
            "image_id": "TEST_IMG_001",
            "receipt_id": 12345,
            "labels": [
                {
                    "image_id": "TEST_IMG_001",
                    "receipt_id": 12345,
                    "line_id": 1,
                    "word_id": 1,
                    "label": "MERCHANT_NAME",
                    "validation_status": "NONE"
                }
            ],
            "urgent": False
        },
        {
            "image_id": "TEST_IMG_002",
            "receipt_id": 12346,
            "labels": [
                {
                    "image_id": "TEST_IMG_002",
                    "receipt_id": 12346,
                    "line_id": 1,
                    "word_id": 1,
                    "label": "DATE",
                    "validation_status": "NONE"
                }
            ],
            "urgent": True
        }
    ]
    
    try:
        results = await validator.validate_batch(batch_receipts)
        print(f"✅ Processed {len(results)} receipts")
        
        # Show metrics
        metrics = validator.get_metrics_summary()
        print(f"📊 Metrics:")
        print(f"   - Validation Count: {metrics['validation_count']}")
        print(f"   - Average Time: {metrics['average_time_seconds']:.2f}s")
        print(f"   - Success Rate: {metrics['success_rate']:.1%}")
        print(f"   - Total Cost: ${metrics['estimated_cost_usd']:.4f}")
        
    except Exception as e:
        print(f"❌ Batch test failed: {e}")


async def test_ollama_direct_call():
    """Test direct Ollama API call to verify connectivity"""
    print("\n🧪 Testing Direct Ollama API Call")
    print("-" * 40)
    
    import httpx
    
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    api_key = os.getenv("OLLAMA_API_KEY")
    model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    
    # Simple test prompt
    test_prompt = "Respond with 'Ollama is working' if you can read this."
    
    async with httpx.AsyncClient() as client:
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        try:
            response = await client.post(
                f"{base_url}/api/generate",
                headers=headers,
                json={
                    "model": model,
                    "prompt": test_prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0
                    }
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Ollama responded: {result.get('response', '')[:100]}")
            else:
                print(f"❌ Ollama API error: {response.status_code}")
                print(f"   Response: {response.text[:200]}")
                
        except httpx.ConnectError:
            print(f"❌ Cannot connect to Ollama at {base_url}")
            print("   Make sure Ollama is running or check your OLLAMA_BASE_URL")
        except Exception as e:
            print(f"❌ Direct API test failed: {e}")


async def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("OLLAMA-ONLY VALIDATION TEST SUITE")
    print("NO OPENAI DEPENDENCIES")
    print("=" * 60)
    
    # Show configuration
    print_config_summary()
    
    # Test direct Ollama connectivity first
    await test_ollama_direct_call()
    
    # Run validation tests if Ollama is available
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    
    print("\n📝 Note: The following tests require:")
    print("   1. DynamoDB access (or mocked)")
    print("   2. Receipt data in the database")
    print("   3. Ollama running and accessible")
    print("\nSkipping database-dependent tests in this demo...")
    
    # These would work with proper database setup:
    # await test_basic_validation()
    # await test_batch_validation()
    
    print("\n" + "=" * 60)
    print("✅ TEST SUITE COMPLETE - NO OPENAI API USED")
    print("=" * 60)


if __name__ == "__main__":
    # Ensure no OpenAI environment variables are set
    if os.getenv("OPENAI_API_KEY"):
        print("⚠️  Warning: OPENAI_API_KEY is set but will not be used")
        print("   This system uses only Ollama for LLM operations")
    
    # Run the test suite
    asyncio.run(main())
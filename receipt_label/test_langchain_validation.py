#\!/usr/bin/env python3
"""
Simple test to verify LangChain + Ollama validation works
"""

import asyncio
import os

# Set environment for Ollama Turbo (these will be overridden by .env if it exists)
# Comment these out to use settings from .env file
# os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
# os.environ["OLLAMA_MODEL"] = "llama3.1:8b"

from receipt_label.langchain_validation import (
    test_ollama_connection,
    get_ollama_llm,
    OptimizedReceiptValidator
)


async def main():
    print("=" * 60)
    print("TESTING LANGCHAIN + OLLAMA VALIDATION")
    print("=" * 60)
    
    # Test 1: Check Ollama connection
    print("\n1. Testing Ollama connection...")
    if await test_ollama_connection():
        print("   ✅ Ollama is working with LangChain!")
    else:
        print("   ❌ Ollama connection failed")
        print("   Make sure Ollama is running: ollama serve")
        return
    
    # Test 2: Check LLM configuration
    print("\n2. Checking LLM configuration...")
    llm = get_ollama_llm()
    print(f"   Model: {llm.model}")
    print(f"   Base URL: {llm.base_url}")
    print(f"   Temperature: {llm.temperature}")
    
    # Test 3: Create validator
    print("\n3. Creating validator...")
    validator = OptimizedReceiptValidator()
    print("   ✅ Validator created successfully")
    
    print("\n" + "=" * 60)
    print("All tests passed! LangChain + Ollama is properly configured.")
    print("\nTo use with Ollama Turbo instead:")
    print('  export OLLAMA_BASE_URL="https://api.ollama.com"')
    print('  export OLLAMA_API_KEY="your-api-key"')
    print('  export OLLAMA_MODEL="turbo"')
    

if __name__ == "__main__":
    asyncio.run(main())
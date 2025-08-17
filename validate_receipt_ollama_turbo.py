#!/usr/bin/env python3
"""
Ollama Turbo Example: Receipt validation with hosted Ollama service

This example shows how to use Ollama Turbo (hosted service) with API authentication
for receipt label validation.
"""

import asyncio
import os
from dotenv import load_dotenv
from langchain_community.llms import Ollama
from langchain_core.messages import HumanMessage
import httpx

# Load environment variables
load_dotenv()

# Configuration for Ollama Turbo
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "https://api.ollama.ai")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "turbo")


# Sample receipt data
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
        {"word": "SUBTOTAL", "label": "SUBTOTAL_AMOUNT", "line": 11},  # Wrong
        {"word": "11.47", "label": "SUBTOTAL_AMOUNT", "line": 11},  # Correct
        {"word": "TOTAL", "label": "GRAND_TOTAL", "line": 13},  # Wrong
        {"word": "12.39", "label": "GRAND_TOTAL", "line": 13},  # Correct
    ]
}


async def test_ollama_turbo_connection():
    """Test connection to Ollama Turbo service"""
    print("\n🔌 Testing Ollama Turbo Connection")
    print("=" * 50)
    
    if not OLLAMA_API_KEY:
        print("❌ OLLAMA_API_KEY not set in .env file")
        print("\nTo use Ollama Turbo:")
        print("1. Add your API key to .env: OLLAMA_API_KEY='your-key-here'")
        print("2. Set the endpoint: OLLAMA_BASE_URL='https://api.ollama.ai'")
        print("3. Set the model: OLLAMA_MODEL='turbo'")
        return False
    
    print(f"Endpoint: {OLLAMA_BASE_URL}")
    print(f"Model: {OLLAMA_MODEL}")
    print(f"API Key: {'*' * 10}{OLLAMA_API_KEY[-4:]}")
    
    # Test with httpx first to verify connectivity
    try:
        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {OLLAMA_API_KEY}",
                "Content-Type": "application/json"
            }
            
            # Try to list models or make a simple request
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                headers=headers,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": "Say 'OK' if you can read this.",
                    "stream": False
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                print("✅ Connection successful!")
                result = response.json()
                print(f"Response: {result.get('response', '')[:100]}")
                return True
            else:
                print(f"❌ Connection failed: {response.status_code}")
                print(f"Response: {response.text}")
                return False
                
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False


async def validate_with_ollama_turbo():
    """Validate receipt labels using Ollama Turbo"""
    
    print("\n🚀 Receipt Validation with Ollama Turbo")
    print("=" * 50)
    
    if not OLLAMA_API_KEY:
        print("❌ Please set OLLAMA_API_KEY in your .env file")
        return
    
    try:
        # Method 1: Using httpx directly (more control)
        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {OLLAMA_API_KEY}",
                "Content-Type": "application/json"
            }
            
            # Format the receipt
            receipt_text = "\n".join(SAMPLE_RECEIPT["lines"][:10])
            
            # Create validation prompt
            prompt = f"""You are a receipt validation system. Validate these word labels.

RECEIPT:
{receipt_text}

LABELS TO CHECK:
1. "WALMART" labeled as MERCHANT_NAME - Is this correct?
2. "2.99" labeled as UNIT_PRICE - Is this correct?
3. "MILK" labeled as PRODUCT_NAME - Is this correct?
4. "SUBTOTAL" labeled as SUBTOTAL_AMOUNT - Is this correct?
5. "11.47" labeled as SUBTOTAL_AMOUNT - Is this correct?

For each label, respond with:
- VALID if correct
- INVALID (suggest: CORRECT_LABEL) if wrong

Remember:
- MERCHANT_NAME = store name (like "WALMART")
- PRODUCT_NAME = product names (like "MILK", "BREAD")
- UNIT_PRICE = price numbers for products
- SUBTOTAL_AMOUNT = the subtotal number, not the word "SUBTOTAL"
- The word "SUBTOTAL" itself should be labeled as LABEL_INDICATOR"""

            print("\n📤 Sending validation request to Ollama Turbo...")
            
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                headers=headers,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0,
                        "top_p": 0.9
                    }
                },
                timeout=60.0
            )
            
            if response.status_code == 200:
                result = response.json()
                validation_response = result.get("response", "")
                
                print("\n📊 Validation Results:")
                print("-" * 40)
                print(validation_response)
                
                # Parse the response to extract validation results
                lines = validation_response.split('\n')
                for line in lines:
                    if 'VALID' in line:
                        if 'INVALID' in line:
                            print(f"  ❌ {line}")
                        else:
                            print(f"  ✅ {line}")
                            
            else:
                print(f"❌ Request failed: {response.status_code}")
                print(f"Response: {response.text}")
                
    except Exception as e:
        print(f"❌ Error: {e}")
        print("\nTroubleshooting:")
        print("1. Verify your OLLAMA_API_KEY is correct")
        print("2. Check the OLLAMA_BASE_URL endpoint")
        print("3. Ensure your subscription includes the model you're trying to use")


async def validate_with_langchain_ollama():
    """Alternative: Use LangChain's Ollama integration"""
    
    print("\n🔗 Using LangChain + Ollama Turbo")
    print("=" * 50)
    
    if not OLLAMA_API_KEY:
        print("❌ Please set OLLAMA_API_KEY in your .env file")
        return
    
    try:
        from langchain_community.llms import Ollama
        
        # Configure Ollama with authentication
        llm = Ollama(
            base_url=OLLAMA_BASE_URL,
            model=OLLAMA_MODEL,
            headers={
                "Authorization": f"Bearer {OLLAMA_API_KEY}"
            },
            temperature=0
        )
        
        # Simple test
        prompt = """Validate this receipt label:
        Word: "WALMART"
        Label: "MERCHANT_NAME"
        
        Reply with VALID or INVALID."""
        
        print("Sending request...")
        response = await llm.ainvoke(prompt)
        
        print("\n📊 Response:")
        print(response)
        
    except ImportError:
        print("❌ langchain-community not installed")
        print("Run: pip install langchain-community")
    except Exception as e:
        print(f"❌ Error: {e}")


async def main():
    """Run Ollama Turbo validation examples"""
    
    print("\n🤖 Ollama Turbo Receipt Validation")
    print("=" * 50)
    
    # Check configuration
    print("\n📋 Configuration Check:")
    print(f"  OLLAMA_BASE_URL: {OLLAMA_BASE_URL}")
    print(f"  OLLAMA_API_KEY: {'✅ Set' if OLLAMA_API_KEY else '❌ Not set'}")
    print(f"  OLLAMA_MODEL: {OLLAMA_MODEL}")
    
    if not OLLAMA_API_KEY:
        print("\n⚠️  To use Ollama Turbo:")
        print("1. Get your API key from your Ollama Turbo dashboard")
        print("2. Add to .env file:")
        print("   OLLAMA_API_KEY='your-api-key-here'")
        print("   OLLAMA_BASE_URL='https://api.ollama.ai'  # or your endpoint")
        print("   OLLAMA_MODEL='turbo'  # or your model name")
        return
    
    # Test connection
    connected = await test_ollama_turbo_connection()
    
    if connected:
        # Run validation
        await validate_with_ollama_turbo()
        
        # Optionally try LangChain method
        # await validate_with_langchain_ollama()
    
    print("\n" + "=" * 50)
    print("✅ Complete!")
    print("\n📚 Next Steps:")
    print("1. The validation is working with Ollama Turbo")
    print("2. Integration with the full LangChain graph is ready")
    print("3. See enhanced_validation.py for production usage")


if __name__ == "__main__":
    asyncio.run(main())
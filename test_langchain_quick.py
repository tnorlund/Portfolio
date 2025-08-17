#!/usr/bin/env python3
"""
Quick test script to verify LangChain validation setup
"""
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def test_langchain_setup():
    """Test basic LangChain setup"""
    print("🔧 Testing LangChain Validation Setup\n")
    
    # Check API keys
    print("1️⃣ Checking API Keys:")
    langchain_key = os.getenv("LANGCHAIN_API_KEY", "")
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    
    print(f"   ✓ LANGCHAIN_API_KEY: {'Set' if langchain_key and langchain_key != 'YOUR_LANGCHAIN_API_KEY' else '❌ Not set'}")
    print(f"   ✓ OLLAMA_BASE_URL: {ollama_url}")
    print(f"   ✓ OPENAI_API_KEY: {'Set' if openai_key and openai_key != 'YOUR_OPENAI_API_KEY' else '⚠️ Not set (optional)'}")
    
    # Test Ollama connection
    print("\n2️⃣ Testing Ollama Connection:")
    try:
        from langchain_ollama import ChatOllama
        
        # Try to initialize Ollama
        llm = ChatOllama(
            base_url=ollama_url,
            model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            temperature=0
        )
        
        # Test with a simple prompt
        response = await llm.ainvoke("Say 'Hello, LangChain!' in exactly 3 words.")
        print(f"   ✓ Ollama Response: {response.content}")
    except Exception as e:
        print(f"   ❌ Ollama Error: {e}")
        print("   💡 Make sure Ollama is running and accessible at the configured URL")
    
    # Test the validation graph
    print("\n3️⃣ Testing Validation Graph:")
    try:
        from receipt_label.langchain_validation.graph_design import create_validation_graph
        
        # Create the graph
        graph = create_validation_graph()
        print("   ✓ Graph created successfully")
        
        # Create test data
        test_state = {
            "image_id": "TEST_IMG_001",
            "receipt_id": 1,
            "labels_to_validate": [
                {
                    "image_id": "TEST_IMG_001",
                    "receipt_id": 1,
                    "line_id": 1,
                    "word_id": 1,
                    "label": "MERCHANT_NAME",
                    "validation_status": "NONE"
                }
            ],
            "completed": False
        }
        
        print("   ✓ Test state created")
        print("\n   Note: Full graph execution requires DynamoDB access.")
        print("   For a complete test, use the full test script: python test_langchain_validation.py")
        
    except ImportError as e:
        print(f"   ❌ Import Error: {e}")
        print("   💡 Make sure you're in the right directory and the files exist")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # Show next steps
    print("\n4️⃣ Next Steps:")
    print("   1. Update your .env file with actual API keys")
    print("   2. Start Ollama if using local models: ollama serve")
    print("   3. Run the full test: python receipt_label/receipt_label/langchain_validation/test_langchain_validation.py")
    print("   4. Check the migration guide: receipt_label/receipt_label/langchain_validation/migration_guide.md")

if __name__ == "__main__":
    asyncio.run(test_langchain_setup())
# 🚀 Ollama Turbo + LangSmith Setup Guide

This guide shows you how to test Ollama Turbo with LangSmith graph tracing for receipt validation.

## 📋 Prerequisites

### 1. Get Your API Keys

#### Ollama Turbo
- Get your API key from your Ollama Turbo dashboard
- Note your endpoint URL (usually `https://api.ollama.ai`)
- Check which model you have access to (e.g., `turbo`, `llama3.1-turbo`)

#### LangSmith (for graph tracing)
1. Go to https://smith.langchain.com
2. Sign up/login
3. Go to Settings → API Keys
4. Create a new API key
5. Note your organization name

## 🔧 Configuration

### 2. Update Your `.env` File

```bash
# Ollama Turbo Configuration
OLLAMA_BASE_URL='https://api.ollama.ai'
OLLAMA_API_KEY='your-ollama-turbo-api-key-here'
OLLAMA_MODEL='turbo'  # or your specific model

# LangSmith Configuration (for graph tracing)
LANGCHAIN_API_KEY='your-langsmith-api-key-here'
LANGCHAIN_PROJECT='receipt-validation'  # or your project name
LANGCHAIN_TRACING_V2='true'

# Optional: Use Ollama for validation
LLM_PROVIDER='ollama'
```

## 🧪 Testing

### 3. Run the Test Script

```bash
# Basic connection test
python test_ollama_langsmith.py
```

This will:
1. ✅ Test Ollama Turbo connection
2. ✅ Create a LangChain graph
3. ✅ Run a validation
4. ✅ Send traces to LangSmith

### 4. View Your Traces in LangSmith

1. Go to https://smith.langchain.com
2. Navigate to your project (e.g., "receipt-validation")
3. You'll see:
   - Graph execution traces
   - Input/output for each node
   - Timing information
   - Token usage
   - Error tracking

## 📊 What You'll See in LangSmith

### Graph Visualization
```
[Start] → [Validate Labels] → [Format Results] → [End]
```

### Trace Details
- **Input**: Your receipt labels
- **LLM Calls**: Ollama Turbo prompts and responses
- **Output**: Validation results
- **Metrics**: Latency, tokens, cost

## 🎯 Quick Test Commands

### Test 1: Simple Validation
```bash
python validate_receipt_ollama_turbo.py
```

### Test 2: Graph with Tracing
```bash
python test_ollama_langsmith.py
```

### Test 3: Full Pipeline
```bash
python validate_receipt_example.py
```

## 🔍 Debugging

### If Ollama Turbo isn't connecting:
```python
# Test raw connection
import httpx
import asyncio

async def test():
    headers = {"Authorization": f"Bearer YOUR_API_KEY"}
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.ollama.ai/api/generate",
            headers=headers,
            json={"model": "turbo", "prompt": "Hello"},
            timeout=30
        )
        print(response.status_code, response.text)

asyncio.run(test())
```

### If LangSmith isn't recording:
1. Check LANGCHAIN_TRACING_V2 is "true" (not True)
2. Verify API key is correct
3. Check project exists in dashboard
4. Look for errors in terminal output

## 🚦 Expected Output

When everything works, you'll see:

```
🔧 Configuration Check
==================================================
OLLAMA_API_KEY: ✅ Set
LANGCHAIN_API_KEY: ✅ Set
LANGCHAIN_TRACING_V2: true
LANGCHAIN_PROJECT: receipt-validation

✅ LangSmith tracing enabled
   View traces at: https://smith.langchain.com/...

🦙 Testing Ollama Turbo
----------------------------------------
✅ Ollama Turbo connected!
   Model: turbo
   Response: Connected!

🔄 Testing LangChain Graph
----------------------------------------
✅ Graph compiled successfully
✅ Validation complete!

📊 View trace in LangSmith:
   https://smith.langchain.com
```

## 💡 Using in Production

### With Enhanced Validation (includes statistics)
```python
from receipt_label.langchain_validation.enhanced_validation import (
    create_enhanced_validator,
    ValidationStatsManager
)

# Create stats manager
stats_manager = ValidationStatsManager()

# Create validator
validator, _ = create_enhanced_validator(stats_manager)

# Validate with Ollama Turbo
results = await validator(
    image_id="IMG_001",
    receipt_id=1,
    labels_to_validate=labels,
    merchant_name="Walmart"
)
```

### View in LangSmith
Every validation will appear in your LangSmith dashboard with:
- Full graph execution
- All LLM calls to Ollama Turbo
- Performance metrics
- Error tracking
- Cost estimation

## 📈 Benefits of This Setup

1. **Ollama Turbo**: Fast, hosted LLM without local setup
2. **LangSmith Tracing**: See exactly what's happening in your graphs
3. **Statistical Tracking**: Build confidence over time
4. **Cost Optimization**: Skip validation for high-confidence labels
5. **Debugging**: Trace every step of the validation process

## 🆘 Need Help?

1. **Ollama Turbo Issues**: Check your subscription and API key
2. **LangSmith Issues**: Verify project exists and API key is valid
3. **Code Issues**: See the test scripts for working examples

## 🎉 Success!

Once you see traces in LangSmith, you have:
- ✅ Ollama Turbo working for LLM inference
- ✅ LangSmith recording your graph executions
- ✅ Full observability into your validation pipeline
- ✅ Ready for production deployment

The graphs are now being stored and traced in LangSmith, giving you complete visibility into your receipt validation workflow!
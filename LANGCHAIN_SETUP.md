# LangChain Receipt Validation Setup Guide

Quick setup guide for the LangChain receipt validation system.

## Prerequisites

- Python 3.12+
- Valid API keys for your chosen provider(s)
- AWS credentials (for DynamoDB access)

## Quick Setup

### 1. Environment Configuration

```bash
# Copy and edit the environment template
cp .example.env .env
```

**Required for OpenAI:**
```env
OPENAI_API_KEY=sk-your-openai-key-here
```

**Required for Ollama (local):**
```bash
# Install and start Ollama
curl -fsSL https://ollama.ai/install.sh | sh
ollama serve

# Pull a model
ollama pull llama3.1:8b
```

**Required for database:**
```env
DYNAMODB_TABLE_NAME=receipt-dev
AWS_ACCESS_KEY_ID=your-aws-access-key
AWS_SECRET_ACCESS_KEY=your-aws-secret-key
```

### 2. Install Dependencies

```bash
cd receipt_label
pip install -e .[full]
```

### 3. Test Installation

```bash
# Test basic configuration
python test_langchain_validation.py --config-only

# Test with OpenAI (requires API key)
python test_langchain_validation.py --provider openai

# Test with Ollama (requires local installation)
python test_langchain_validation.py --provider ollama
```

## Basic Usage

```python
from receipt_label.langchain_validation import validate_receipt_labels_v2

# Sample receipt labels
labels = [
    {
        "image_id": "IMG_001",
        "receipt_id": 12345,
        "line_id": 1,
        "word_id": 1,
        "label": "MERCHANT_NAME",
        "validation_status": "NONE"
    }
]

# Validate with OpenAI
result = await validate_receipt_labels_v2(
    "IMG_001", 12345, labels, llm_provider="openai"
)

print(f"Success: {result['success']}")
print(f"Results: {result['validation_results']}")
```

## Configuration Options

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `LLM_PROVIDER` | `openai` | `openai` or `ollama` |
| `VALIDATION_MODE` | `smart_batch` | `realtime`, `batch`, or `smart_batch` |
| `BATCH_SIZE` | `10` | Number of receipts per batch |
| `CACHE_ENABLED` | `true` | Enable result caching |
| `LANGCHAIN_TRACING_V2` | `false` | Enable LangSmith monitoring |

## Providers Setup

### OpenAI Setup
1. Get API key from [OpenAI Platform](https://platform.openai.com)
2. Set `OPENAI_API_KEY=sk-your-key-here`
3. Choose model: `OPENAI_MODEL=gpt-4o-mini` (recommended)

### Ollama Setup
1. Install Ollama: `curl -fsSL https://ollama.ai/install.sh | sh`
2. Start service: `ollama serve`
3. Pull model: `ollama pull llama3.1:8b`
4. Set `OLLAMA_BASE_URL=http://localhost:11434`

## Verification

Run the test suite to verify everything works:

```bash
# Full test suite
python test_langchain_validation.py

# Expected output:
# ✅ Configuration - 0.01s
# ✅ OpenAI Provider - 2.34s
# ✅ Ollama Provider - 4.12s
# ✅ Batch Validation - 3.45s
# ✅ Metrics Collection - 1.23s
# ✅ Result Caching - 0.89s
# ✅ Error Handling - 0.12s
#
# TEST SUMMARY
# Total tests: 7
# Passed: 7
# Failed: 0
# Success rate: 100.0%
```

## Next Steps

- Review the full documentation: `/receipt_label/langchain_validation/README.md`
- Explore advanced configuration options
- Set up monitoring with LangSmith
- Integrate with your existing receipt processing pipeline

## Troubleshooting

**Import errors:** Install dependencies with `pip install -e .[full]`

**API key errors:** Verify your API keys are correctly set in `.env`

**Ollama connection errors:** Ensure Ollama is running with `ollama serve`

**DynamoDB errors:** Check AWS credentials and table name configuration
# Merchant Validation with Open-Source LLMs

This document describes the current merchant validation implementation and how to adapt it for use with open-source language models (Ollama, vLLM, etc.) running locally.

## Table of Contents

- [Current Architecture](#current-architecture)
- [The Metadata Creation Pattern](#the-metadata-creation-pattern)
- [OSS Model Integration](#oss-model-integration)
- [Testing Strategy](#testing-strategy)
- [Configuration Guide](#configuration-guide)
- [Performance Optimization](#performance-optimization)
- [Troubleshooting](#troubleshooting)

## Current Architecture

### Overview

The merchant validation system uses an agent-based architecture with function tools to validate and enrich receipt merchant data. The system combines:

1. **Agent Framework**: Uses `openai-agents` library (v0.1.0) for orchestration
2. **Google Places API**: For merchant lookup and validation
3. **DynamoDB Caching**: PlacesCache entities store API results
4. **Structured Output**: Guarantees ReceiptMetadata creation through tool enforcement

### Component Flow

```
Receipt Data → MerchantValidationAgent → Function Tools → ReceiptMetadata
                         ↓
                   [5 Tools Available]
                   1. search_by_phone
                   2. search_by_address
                   3. search_nearby
                   4. search_by_text
                   5. tool_return_metadata (REQUIRED)
```

### Key Files

- `receipt_label/merchant_validation/handler.py` - Main handler class
- `receipt_label/merchant_validation/agent.py` - Agent configuration
- `receipt_label/data/places_api.py` - Google Places API client with caching
- `receipt_dynamo/entities/places_cache.py` - Cache entity for DynamoDB

## The Metadata Creation Pattern

### The Challenge

The agent must return a structured `ReceiptMetadata` object with specific fields. Without enforcement, agents might:

- Return unstructured text
- Miss required fields
- Fail to call the final tool

### The Solution: tool_return_metadata

The system enforces metadata creation through a required function tool that the agent MUST call:

```python
@function_tool
def tool_return_metadata(
    place_id: str,
    merchant_name: str,
    address: str,
    phone_number: str,
    merchant_category: str,
    matched_fields: List[str],
    validated_by: ValidatedBy,
    reasoning: str,
) -> Dict[str, Any]:
    """
    Return the final merchant metadata as a structured object.
    This tool MUST be called to complete the validation.
    """
```

### Why This Pattern Works

1. **Structured Output**: Forces the agent to provide all required fields
2. **Type Validation**: Ensures correct data types through function parameters
3. **Retry Logic**: If agent doesn't call the tool, system retries (up to MAX_AGENT_ATTEMPTS)
4. **Partial Results**: Collects intermediate results even if final tool isn't called
5. **Fallback Strategy**: Uses partial results to build metadata if agent fails

### Agent Instructions

The agent is explicitly instructed to call `tool_return_metadata`:

```
After deciding on the final metadata, call the function `tool_return_metadata`
with the exact values for each field (place_id, merchant_name, etc.) and then stop.
```

### Retry and Recovery

```python
for attempt in range(1, max_attempts + 1):
    run_result = Runner.run_sync(agent, messages)

    # Check if tool_return_metadata was called
    metadata = extract_metadata_from_result(run_result)
    if metadata:
        return metadata

    # Collect partial results for fallback
    partial_results.extend(extract_partial_results(run_result))

# If all attempts fail, use partial results
return build_metadata_from_partial_results(partial_results)
```

## OSS Model Integration

### Setting Up Local Models

#### Option 1: Ollama

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull gpt-oss:20b

# Start Ollama server (default port 11434)
ollama serve
```

#### Option 2: vLLM

```bash
# Install vLLM
pip install vllm

# Start vLLM server
python -m vllm.entrypoints.openai.api_server \
    --model meta-llama/Llama-2-13b-hf \
    --port 8000
```

### Configuring the Agent for OSS Models

The key is using `OpenAIChatCompletionsModel` with a custom client:

```python
from openai import AsyncOpenAI
from agents import Agent, OpenAIChatCompletionsModel

# For Ollama
client = AsyncOpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",  # Any string works for Ollama
)

# For vLLM
client = AsyncOpenAI(
    base_url="http://localhost:8000/v1",
    api_key="EMPTY",  # vLLM doesn't need API key
)

# Configure agent with OSS model
model_config = OpenAIChatCompletionsModel(
    model="llama2:13b",  # or your model name
    openai_client=client,
)

agent = Agent(
    name="ReceiptMerchantAgent",
    instructions=AGENT_INSTRUCTIONS,
    model=model_config,
    tools=tools,
)
```

### Async vs Sync Execution

The current implementation uses `Runner.run_sync` with ThreadPoolExecutor for timeout control:

```python
with ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(
        Runner.run_sync,
        agent,
        messages,
        max_turns=MAX_AGENT_TURNS,
    )
    result = future.result(timeout=timeout_seconds)
```

For local models, this pattern still works but consider:

- Local models don't have network latency
- Timeout might need adjustment (local models can be slower)
- Thread overhead might not be necessary for local execution

### Model-Specific Considerations

#### Context Length

- **OpenAI GPT-3.5**: 4096 tokens
- **Llama 2**: 4096 tokens
- **Mistral**: 8192 tokens
- **GPT-OSS:20b**: Check your specific model

Adjust prompt length and receipt data accordingly.

#### Response Quality

Local models may need prompt engineering:

- More explicit instructions
- Examples in the prompt
- Structured output templates

## Testing Strategy

### Unit Tests with Mocks

The existing test infrastructure uses mocks effectively:

```python
@pytest.fixture
def mock_places_api(mocker):
    class DummyAPI:
        def search_by_phone(self, phone):
            return {"place_id": "test_id", "name": "Test Merchant"}

    return mocker.patch(
        'receipt_label.data.places_api.PlacesAPI',
        DummyAPI
    )
```

### Integration Tests with Local Models

Create deterministic tests using controlled prompts:

```python
def test_merchant_validation_with_local_model():
    # Use a simple, deterministic case
    receipt_data = {
        "merchant_name": "McDonald's",
        "phone": "555-1234",
        "address": "123 Main St",
    }

    # Configure agent with temperature=0 for consistency
    agent = create_oss_agent(temperature=0)

    # Run validation
    metadata, partial = agent.validate_receipt(receipt_data)

    # Assert structure, not exact values
    assert metadata is not None
    assert "merchant_name" in metadata
    assert "place_id" in metadata
```

### Performance Testing

Compare models systematically:

```python
def benchmark_models():
    models = [
        ("gpt-3.5-turbo", create_openai_agent),
        ("llama2:13b", create_ollama_agent),
        ("mistral:7b", create_mistral_agent),
    ]

    for model_name, create_fn in models:
        agent = create_fn()

        start = time.time()
        metadata, _ = agent.validate_receipt(test_data)
        elapsed = time.time() - start

        print(f"{model_name}: {elapsed:.2f}s")
        print(f"  Success: {metadata is not None}")
        print(f"  Fields: {len(metadata) if metadata else 0}")
```

### Mock Local Model for CI/CD

For continuous integration without local models:

```python
class MockLocalModel:
    """Deterministic mock for testing without real LLM"""

    def complete(self, messages, tools):
        # Always return a tool_return_metadata call
        return {
            "tool_calls": [{
                "function": "tool_return_metadata",
                "arguments": {
                    "place_id": "mock_place_123",
                    "merchant_name": "Mock Merchant",
                    # ... other fields
                }
            }]
        }
```

## Configuration Guide

### Environment Variables

```bash
# Google Places API (required)
export GOOGLE_PLACES_API_KEY="your-key-here"

# Model Selection
export LLM_PROVIDER="ollama"  # or "vllm", "openai"
export LLM_MODEL="llama2:13b"
export LLM_BASE_URL="http://localhost:11434/v1"

# Performance Tuning
export MAX_AGENT_ATTEMPTS=3
export AGENT_TIMEOUT_SECONDS=60
export MAX_AGENT_TURNS=10

# Logging
export LOG_AGENT_FUNCTION_CALLS=true
```

### Configuration File (optional)

Create `merchant_validation_config.yaml`:

```yaml
llm:
  provider: ollama
  model: llama2:13b
  base_url: http://localhost:11434/v1
  temperature: 0.3
  max_tokens: 2000

agent:
  max_attempts: 3
  timeout_seconds: 60
  max_turns: 10

cache:
  ttl_seconds: 86400 # 24 hours
  enable_dynamo: true

logging:
  level: INFO
  log_function_calls: true
```

## Performance Optimization

### Caching Strategy

The system already implements comprehensive caching:

1. **Google Places API Cache**

   - Stored in DynamoDB via PlacesCache entity
   - TTL-based expiration
   - Query count tracking
   - Normalized address matching

2. **Cache Key Design**

   ```python
   # Phone lookups
   cache_key = f"PHONE#{normalized_phone}"

   # Address lookups
   cache_key = f"ADDRESS#{normalized_address_hash}"

   # Nearby searches
   cache_key = f"{lat},{lng}:{radius}:{keyword}"
   ```

3. **Cache Hit Optimization**
   - Check cache before any API call
   - Cache both successful and failed lookups
   - Increment query counters for analytics

### Reducing Agent Calls

Consider pre-filtering before invoking the agent:

```python
def should_use_agent(receipt_data):
    """Determine if agent is needed or if patterns suffice"""

    # Known merchant patterns
    if is_known_merchant(receipt_data["merchant_name"]):
        return False

    # High-confidence phone match
    if has_valid_phone(receipt_data["phone"]):
        cached = get_cached_phone_lookup(receipt_data["phone"])
        if cached and cached["confidence"] > 0.9:
            return False

    return True
```

### Batch Processing

For multiple receipts, consider parallel processing:

```python
async def validate_receipts_batch(receipts, max_concurrent=5):
    """Process multiple receipts with concurrency limit"""

    semaphore = asyncio.Semaphore(max_concurrent)

    async def validate_with_limit(receipt):
        async with semaphore:
            return await validate_receipt_async(receipt)

    tasks = [validate_with_limit(r) for r in receipts]
    return await asyncio.gather(*tasks)
```

## Troubleshooting

### Common Issues

#### 1. Agent Doesn't Call tool_return_metadata

**Symptoms**: Validation fails after all retry attempts

**Solutions**:

- Increase MAX_AGENT_TURNS (agent might need more steps)
- Adjust prompt to be more explicit about calling the tool
- Check if model understands function calling syntax
- Use a more capable model

#### 2. Local Model Timeout

**Symptoms**: ThreadPoolExecutor timeout errors

**Solutions**:

- Increase AGENT_TIMEOUT_SECONDS
- Use smaller/faster model
- Optimize prompt length
- Pre-load model in memory

#### 3. Inconsistent Results

**Symptoms**: Same input produces different outputs

**Solutions**:

- Set temperature=0 for deterministic output
- Use structured prompt templates
- Implement result validation and retry logic
- Consider fine-tuning for your use case

#### 4. Memory Issues with Local Models

**Symptoms**: OOM errors or slow performance

**Solutions**:

- Use quantized models (4-bit, 8-bit)
- Reduce batch size
- Implement model unloading between batches
- Use streaming responses where possible

### Debug Logging

Enable detailed logging for troubleshooting:

```python
import logging

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

# Log all function calls
os.environ["LOG_AGENT_FUNCTION_CALLS"] = "true"

# Log model interactions
logger = logging.getLogger("receipt_label.merchant_validation")
logger.setLevel(logging.DEBUG)
```

### Performance Profiling

Profile the validation pipeline:

```python
import cProfile
import pstats

def profile_validation():
    profiler = cProfile.Profile()
    profiler.enable()

    # Run validation
    metadata, _ = agent.validate_receipt(test_data)

    profiler.disable()
    stats = pstats.Stats(profiler)
    stats.sort_stats('cumulative')
    stats.print_stats(20)  # Top 20 functions
```

## Future Improvements

### Potential Enhancements

1. **Streaming Responses**: Use streaming API for faster first token
2. **Model Router**: Automatically select best model based on receipt complexity
3. **Fine-tuning**: Train models specifically for receipt validation
4. **Embedding Cache**: Cache receipt embeddings for similarity matching
5. **Confidence Scoring**: Add confidence scores to metadata fields
6. **Multi-Model Ensemble**: Combine outputs from multiple models
7. **Active Learning**: Track corrections and improve over time

### Architecture Evolution

Consider moving to a more modular architecture:

```
Receipt → Pattern Detector → Confidence Check → Agent (if needed) → Metadata
              ↓                    ↓                    ↓
         Known Patterns      High Confidence      Complex Cases
              ↓                    ↓                    ↓
         Direct Return        Cache Lookup         LLM Processing
```

This reduces LLM calls while maintaining accuracy.

## Conclusion

The merchant validation system's use of function tools to enforce structured output is a robust pattern that works well with both cloud and local models. The key to successful OSS integration is:

1. Understanding the `tool_return_metadata` enforcement pattern
2. Properly configuring OpenAI-compatible clients for local models
3. Adjusting timeouts and retry logic for local model characteristics
4. Leveraging the existing caching infrastructure
5. Implementing appropriate testing strategies

The system is designed to be resilient, with multiple fallback strategies ensuring that merchant metadata is always created, even when the agent behaves unexpectedly.

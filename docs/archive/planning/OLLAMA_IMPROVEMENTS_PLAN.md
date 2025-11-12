# Ollama Improvements Plan

## Current Status ✅

### Already Implemented
- ✅ Structured outputs (`format="json"`)
- ✅ Dynamic schema injection from Pydantic models
- ✅ `with_structured_output()` for type safety
- ✅ Temperature control (0.3)
- ✅ Float enforcement for amounts

## High-Value Improvements

### 1. 🎯 Add Timeout Configuration

**Why**: Network issues or slow API responses can hang the workflow.

**Implementation**:
```python
llm = ChatOllama(
    model="gpt-oss:120b",
    base_url="https://ollama.com",
    client_kwargs={
        "headers": {"Authorization": f"Bearer {ollama_api_key}"}
    },
    format="json",
    temperature=0.3,
    timeout=120,  # 2 minute timeout
)
```

**Files**: `phase1.py`, `phase2.py`

---

### 2. 🎯 Add Retry Logic with Exponential Backoff

**Why**: API failures are transient. Automatic retries improve reliability.

**Implementation**:
```python
from langchain_core.runnables import RunnableLambda

async def phase1_with_retry(state: CurrencyAnalysisState, ollama_api_key: str, max_retries=3):
    """Phase 1 with automatic retry logic."""
    for attempt in range(max_retries):
        try:
            return await phase1_currency_analysis(state, ollama_api_key)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            print(f"⚠️ Attempt {attempt + 1} failed, retrying...")
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
    return {"currency_labels": []}
```

**Files**: `currency_validation.py` or new `utils/retry.py`

---

### 3. 🎯 Stream Logging for Development

**Why**: Better debugging and monitoring of LLM progress.

**Implementation**:
```python
# In analyze_receipt_simple
async def analyze_with_streaming(state, graph):
    """Analyze with streaming for better debugging."""
    async for state_update in graph.astream(state, config=config):
        node_name = list(state_update.keys())[0]
        print(f"📍 Node: {node_name}")
        if "currency_labels" in state_update.get(node_name, {}):
            count = len(state_update[node_name]["currency_labels"])
            print(f"   Found {count} currency labels")
```

**Files**: `currency_validation.py`

---

### 4. 🎯 Explore Vision Models (BIGGEST POTENTIAL IMPROVEMENT)

**Current Flow**:
```
Receipt Image → OCR (LayoutLM) → Text → LangGraph → Labels
```

**Potential New Flow**:
```
Receipt Image → Vision Model → Structured Labels
```

**Benefits**:
- Eliminate OCR step entirely
- Single API call instead of multiple steps
- Potentially better accuracy

**Implementation**:
```python
async def phase1_with_vision(image_url: str, ollama_api_key: str):
    """Use vision model to analyze receipt image directly."""
    vision_llm = ChatOllama(
        model="llama3.2-vision:90b",  # or gemma-vision, mistral-small
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"}
        },
        format="json",
    )
    
    response = await vision_llm.ainvoke({
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Extract currency amounts from this receipt."},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
        }]
    })
    
    return response
```

**Files**: New `nodes/phase1_vision.py`

**Priority**: ⭐⭐⭐ **HIGH VALUE** - Could completely simplify the architecture

---

### 5. 🎯 Model Size Optimization

**Current**:
- Phase 1: `gpt-oss:120b` (large, slow, expensive)
- Phase 2: `gpt-oss:20b` (smaller, faster)

**Consider**:
- Phase 1: `gpt-oss:20b` or `llama3.2:90b` (faster, cheaper, may be accurate enough)
- A/B test: Compare accuracy vs cost

**Benefits**:
- Lower API latency
- Lower cost
- Better throughput

**Implementation**:
```python
MODELS = {
    "fast": "gpt-oss:20b",      # Faster, cheaper
    "balanced": "llama3.2:90b", # Balanced
    "accurate": "gpt-oss:120b" # Slow, expensive, most accurate
}

# Make it configurable
MODEL_CONFIG = os.getenv("OLLAMA_MODEL", "balanced")
```

**Files**: `phase1.py`, `phase2.py`

---

### 6. 🎯 Better Error Handling and Logging

**Current**: Basic print statements

**Improvement**:
```python
import logging

logger = logging.getLogger("langgraph.phase1")

async def phase1_currency_analysis(state, ollama_api_key):
    try:
        logger.info(f"Starting Phase 1 for receipt {state.receipt_id}")
        
        response = await llm_structured.ainvoke(messages, config=config)
        
        logger.info(
            f"Phase 1 complete: {len(response.currency_labels)} labels, "
            f"confidence: {response.confidence}"
        )
        
        return {"currency_labels": currency_labels}
    except Exception as e:
        logger.error(f"Phase 1 failed: {e}", exc_info=True)
        # Emit metrics to CloudWatch
        # Send to monitoring service
        return {"currency_labels": []}
```

**Files**: All nodes

---

### 7. 🎯 Parallel Processing of Multiple Receipts

**Current**: Process one receipt at a time

**Potential**: Batch multiple receipts

```python
async def analyze_batch(receipt_batch: List[ReceiptData]):
    """Analyze multiple receipts in parallel."""
    tasks = [
        analyze_receipt_simple(client, r.image_id, r.receipt_id, key)
        for r in receipt_batch
    ]
    return await asyncio.gather(*tasks)
```

**Benefits**:
- Better resource utilization
- Faster overall processing

---

## Recommended Implementation Order

### Immediate (Quick Wins)
1. **Add timeouts** (5 minutes) ✅ Easy
2. **Better error handling** (30 minutes) ✅ Medium
3. **Model size config** (15 minutes) ✅ Easy

### Short-Term (High Impact)
4. **Retry logic** (1 hour) ⭐ High value
5. **Streaming logging** (1 hour) ⭐ Good for debugging
6. **Vision model POC** (2-3 hours) 🚀 **BIGGEST IMPACT**

### Long-Term (Optimization)
7. **Batch processing** (2-3 hours)
8. **Performance monitoring** (2-3 hours)

---

## Cost Implications

### Current Monthly Cost
- Ollama Turbo: $20/month flat fee
- ~1000 receipts/month: ~$0.02 per receipt

### With Optimizations
- Vision models: Same cost, same quality, fewer steps
- Smaller models: Same quality, lower latency
- Batch processing: Better efficiency

**Estimated savings**: 20-30% time reduction

---

## Testing Strategy

### 1. Test Timeout
```python
def test_timeout():
    # Should timeout after 2 minutes
    llm = ChatOllama(timeout=120)
```

### 2. Test Retry Logic
```python
def test_retry():
    # First 2 attempts fail, 3rd succeeds
    # Should succeed after retries
```

### 3. Test Vision Model
```python
def test_vision_analysis():
    # Compare OCR + analysis vs vision-only
    # Accuracy comparison
```

---

## Success Metrics

- **Latency**: Target < 20 seconds per receipt (currently ~19s)
- **Accuracy**: Maintain current accuracy (95%+)
- **Reliability**: 99%+ success rate with retries
- **Cost**: No increase in API costs


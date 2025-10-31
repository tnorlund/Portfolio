# LangGraph Best Practices - Receipt Labeling Implementation

## Research-Based Best Practices for Our Implementation

### 1. ✅ Implement Retry Logic for Structured Outputs

**Current Issue**: We just have `try/except` that returns empty list on failure.

**Best Practice**: Add retry logic with exponential backoff.

```python
# RECOMMENDED APPROACH
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.utils import Input, Output
import asyncio

async def phase1_currency_analysis_with_retry(
    state: CurrencyAnalysisState, ollama_api_key: str
):
    """Phase 1 with automatic retry on parsing errors."""
    
    llm = ChatOllama(...)
    llm_structured = llm.with_structured_output(Phase1Response)
    
    # Retry wrapper
    async def invoke_with_retry():
        for attempt in range(3):  # 3 attempts
            try:
                response = await llm_structured.ainvoke(messages, config={...})
                return response
            except Exception as e:
                if attempt < 2:
                    wait_time = 2 ** attempt  # Exponential backoff
                    print(f"   ⚠️ Attempt {attempt + 1} failed: {e}")
                    print(f"   🔄 Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise
    
    response = await invoke_with_retry()
    # ... rest of logic
```

**Why**: Parsing errors are common with LLMs. Retrying often fixes them.

---

### 2. ✅ Better Pydantic Field Descriptions

**Current**: Basic descriptions
**Best Practice**: More detailed descriptions guide the model better

```python
class CurrencyLabel(BaseModel):
    line_text: str = Field(
        ...,
        description="The exact text containing the currency amount as it appears on the receipt line"
    )
    amount: float = Field(
        ...,
        description="The numeric currency value (e.g., 15.02 for $15.02)"
    )
    label_type: CurrencyLabelType = Field(
        ...,
        description="One of: GRAND_TOTAL (final amount due), TAX (sales tax), SUBTOTAL (before tax), LINE_TOTAL (individual item total)"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Your confidence in this classification from 0.0 (uncertain) to 1.0 (certain)"
    )
    reasoning: str = Field(
        ...,
        description="Brief explanation of why you classified this amount (e.g., 'Appears at bottom of receipt', 'Explicitly labeled as TOTAL')"
    )
```

**Why**: Better descriptions = better model performance.

---

### 3. ✅ Add Fallback When with_structured_output Fails

**Current**: Returns empty list on error
**Best Practice**: Fallback to manual parsing

```python
async def phase1_currency_analysis(state, ollama_api_key):
    llm_structured = llm.with_structured_output(Phase1Response)
    
    try:
        response = await llm_structured.ainvoke(messages)
    except Exception as structured_error:
        print(f"⚠️ Structured output failed: {structured_error}")
        
        # FALLBACK: Try without structured binding
        try:
            fallback_llm = ChatOllama(..., format="json")
            fallback_response = await fallback_llm.ainvoke(messages)
            
            # Parse manually
            import json
            parsed = json.loads(fallback_response.content)
            response = Phase1Response(**parsed)
        except Exception as fallback_error:
            print(f"❌ Fallback also failed: {fallback_error}")
            return {"currency_labels": []}
    
    return {"currency_labels": ...}
```

**Why**: Graceful degradation when structured outputs fail.

---

### 4. ✅ Validate Outputs Before Returning

**Current**: Returns whatever comes back
**Best Practice**: Validate and clean data

```python
# After getting response from LLM
currency_labels = []

for item in response.currency_labels:
    # Validate each label
    if item.amount <= 0:
        print(f"⚠️ Skipping invalid amount: {item.amount}")
        continue
        
    if not item.line_text.strip():
        print(f"⚠️ Skipping empty line_text")
        continue
    
    if not (0 <= item.confidence <= 1):
        print(f"⚠️ Invalid confidence {item.confidence}, clamping to 0.5")
        item.confidence = 0.5
    
    # Only keep high-confidence labels for currency
    if item.label_type in ["GRAND_TOTAL", "TAX"] and item.confidence < 0.7:
        print(f"⚠️ Low confidence for {item.label_type}")
        continue
    
    currency_labels.append(item)

if not currency_labels:
    print("⚠️ No valid currency labels found")
    return {"currency_labels": []}

return {"currency_labels": currency_labels}
```

**Why**: Ensures data quality and prevents downstream errors.

---

### 5. ✅ Use Explicit JSON Schema in Prompts

**Current**: Just added JSON schema to prompts ✅
**Best Practice**: Keep this - it's working!

```python
content = f"""
Output a JSON object with this EXACT structure:
{{
  "currency_labels": [
    {{
      "line_text": "exact text containing the amount",
      "amount": 15.02,
      "label_type": "LINE_TOTAL",
      "line_ids": [1, 2],
      "confidence": 0.95,
      "reasoning": "why this classification"
    }}
  ],
  "reasoning": "overall explanation",
  "confidence": 0.97
}}
"""
```

**Why**: Explicit schemas dramatically improve compliance.

---

### 6. ✅ Implement Circuit Breaker Pattern

**Current**: No circuit breaker
**Best Practice**: Track failures and skip on repeated failures

```python
class CircuitBreaker:
    def __init__(self, failure_threshold=3):
        self.failures = 0
        self.threshold = failure_threshold
    
    def should_skip(self) -> bool:
        return self.failures >= self.threshold
    
    def record_failure(self):
        self.failures += 1
    
    def record_success(self):
        self.failures = 0

# In phase1
circuit_breaker = CircuitBreaker()

if circuit_breaker.should_skip():
    print("⚠️ Too many failures - skipping to save resources")
    return {"currency_labels": []}

try:
    response = await llm_structured.ainvoke(...)
    circuit_breaker.record_success()
except Exception as e:
    circuit_breaker.record_failure()
    raise
```

**Why**: Prevents wasting resources on repeatedly failing calls.

---

### 7. ✅ Add Node-Level Monitoring

**Current**: Basic print statements
**Best Practice**: Structured logging + metrics

```python
import logging
import time

logger = logging.getLogger(__name__)

async def phase1_currency_analysis(state, ollama_api_key):
    start_time = time.time()
    
    try:
        logger.info(f"Phase 1 started for receipt {state.receipt_id}")
        
        response = await llm_structured.ainvoke(...)
        
        duration = time.time() - start_time
        logger.info(
            f"Phase 1 completed in {duration:.2f}s, "
            f"found {len(response.currency_labels)} labels"
        )
        
        return {"currency_labels": response.currency_labels}
        
    except Exception as e:
        duration = time.time() - start_time
        logger.error(
            f"Phase 1 failed after {duration:.2f}s: {e}",
            exc_info=True
        )
        return {"currency_labels": []}
```

**Why**: Better observability and debugging.

---

### 8. ✅ Use Async Context Managers for Cleanup

**Current**: Manual cleanup
**Best Practice**: Use context managers

```python
class LLMManager:
    def __init__(self, api_key):
        self.api_key = api_key
        self.llm = None
    
    async def __aenter__(self):
        self.llm = ChatOllama(...)
        return self.llm.with_structured_output(Phase1Response)
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Cleanup if needed
        pass

# Usage
async def phase1(state, api_key):
    async with LLMManager(api_key) as llm_structured:
        return await llm_structured.ainvoke(messages)
```

**Why**: Cleaner resource management.

---

## Recommended Implementation Priority

### High Priority (Do Now)

1. ✅ **Retry Logic** - Add 2-3 retry attempts with backoff
2. ✅ **Better Field Descriptions** - Improve Pydantic model descriptions
3. ✅ **Output Validation** - Validate currency amounts, confidence scores
4. ✅ **Structured Logging** - Use logging module instead of print

### Medium Priority

5. **Circuit Breaker** - Skip after repeated failures
6. **Fallback Parsing** - Manual JSON parsing if structured fails
7. **Async Context Managers** - Better resource management

### Low Priority

8. **Metrics Collection** - Track success/failure rates
9. **Performance Monitoring** - Track API call durations

---

## Specific Recommendations for Our Code

### File: `receipt_label/langchain/nodes/phase1.py`

**Add**:
```python
# 1. Retry wrapper
async def phase1_with_retry(max_attempts=3):
    for attempt in range(max_attempts):
        try:
            return await llm_structured.ainvoke(messages)
        except Exception as e:
            if attempt < max_attempts - 1:
                await asyncio.sleep(2 ** attempt)
            raise

# 2. Output validation
def validate_labels(labels):
    """Validate and clean currency labels."""
    valid = []
    for label in labels:
        if label.amount > 0 and label.confidence >= 0.3:
            valid.append(label)
    return valid

# 3. Use in function
response = await phase1_with_retry()
validated = validate_labels(response.currency_labels)
return {"currency_labels": validated}
```

### File: `receipt_label/langchain/nodes/phase2.py`

Same pattern for Phase 2.

---

## Architecture Patterns We're Already Using (Keep These!)

1. ✅ **Closure-based API key injection** - Secure, no keys in state
2. ✅ **Parallel execution** - Phase 2 runs in parallel
3. ✅ **State reduction** - `operator.add` for merging results
4. ✅ **Conditional dispatching** - Smart routing based on Phase 1 results
5. ✅ **Dry-run mode** - Safe testing
6. ✅ **LangSmith tracing** - Good observability

**These are excellent patterns - keep them!**

---

## Summary of Improvements Needed

### Critical
- [ ] Add retry logic to both phases
- [ ] Add output validation
- [ ] Improve Pydantic descriptions

### Important  
- [ ] Add structured logging
- [ ] Add circuit breaker for reliability

### Nice to Have
- [ ] Add metrics/monitoring
- [ ] Add fallback parsing
- [ ] Use context managers

---

## Testing the Improvements

```bash
# Test with retry logic
python dev.test_simple_currency_validation.py

# Should see:
# - Multiple attempts if first fails
# - Graceful degradation
# - Better error messages
```


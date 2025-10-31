# Ollama Upgrade Analysis - Latest Features (2025)

## Current Implementation

Your current code in `receipt_label/langchain/nodes/phase1.py` and `phase2.py`:

```python
llm = ChatOllama(
    model="gpt-oss:120b",
    base_url="https://ollama.com",
    client_kwargs={
        "headers": {"Authorization": f"Bearer {ollama_api_key}"}
    },
)
```

**Current Setup**: Using Ollama Cloud (Turbo service) with `gpt-oss:120b` and `gpt-oss:20b`.

---

## New Ollama Features (2025) & Recommendations

### 1. ✅ You're Already Using: Turbo Cloud Service

**Current**: You're using `https://ollama.com` with API key authentication

**What it is**: Ollama's Turbo cloud inference service ($20/month)

**Status**: ✅ **Already implemented correctly**

**Benefits you're getting**:
- Fast inference (300+ tokens/sec for 120B model)
- Datacenter-grade hardware
- No local hardware requirements

**Recommendation**: Keep this setup - it's the best option for your use case.

---

### 2. 🎯 Recommended Upgrade: Structured Outputs (JSON Schema)

**What it is**: Type-safe API responses using JSON schemas

**Your Current Code**:
```python
output_parser = PydanticOutputParser(pydantic_object=Phase1Response)
```

**New Approach** (with JSON Schema):
```python
from langchain_ollama import ChatOllama

llm = ChatOllama(
    model="gpt-oss:120b",
    base_url="https://ollama.com",
    client_kwargs={
        "headers": {"Authorization": f"Bearer {ollama_api_key}"}
    },
    # NEW: Structured outputs with JSON schema
    format="json",  # Enable structured mode
    # Or use the dedicated parameter if available:
    # structured_outputs=True,
)
```

**Benefits**:
- More reliable parsing (reduces errors)
- Better type safety
- Fewer invalid responses

**Recommendation**: ⭐ **HIGH PRIORITY** - Update to use structured outputs

---

### 3. 🎯 Recommended Upgrade: Streaming for Better UX

**What it is**: Real-time streaming responses instead of waiting for completion

**Current**:
```python
response = await chain.ainvoke({...})
```

**New Approach**:
```python
# For development/testing
async for chunk in chain.astream({...}):
    print(chunk.content, end="")
```

**Benefits**:
- Better user experience (see progress)
- Lower perceived latency
- Can cancel long-running operations

**Recommendation**: ⭐ **MEDIUM PRIORITY** - Good for user-facing features

---

### 4. ✅ OpenAI API Compatibility - Drop-In Replacement

**What it is**: Ollama now mirrors OpenAI's API exactly

**Current**: Using `ChatOllama` from `langchain-ollama`

**Alternative Approach**:
```python
from openai import OpenAI

# Drop-in replacement
client = OpenAI(
    base_url="https://ollama.com/v1",
    api_key=ollama_api_key,
)

# Use OpenAI SDK instead of LangChain
```

**Benefits**:
- Unified API with OpenAI
- Can switch between OpenAI and Ollama easily
- More standardized

**Recommendation**: Consider for future if you want to standardize on OpenAI API

---

### 5. 🔒 Secure Minions Protocol (NEW)

**What it is**: Local models collaborate with cloud models while maintaining end-to-end encryption

**Use Case**: 
- Keep sensitive data local
- Use cloud for heavy lifting
- Encrypted collaboration

**Your Situation**: You're already using cloud (Turbo), so not applicable unless you want to add local models.

**Recommendation**: Skip (not needed for your cloud-first approach)

---

### 6. 🎯 Enhanced Multimodal Support

**What it is**: Better support for images and documents

**Current**: Text-only analysis

**Potential**: Could analyze receipt images directly!

**New Feature**:
```python
llm = ChatOllama(
    model="gpt-oss:120b",
    multimodal=True,  # If supported
)
```

**Your Use Case**: You have receipt images in S3. Could you analyze them directly?

**Recommendation**: ⭐ **EXPLORE** - Could eliminate OCR dependency

---

### 7. 🎯 Vision Models Support

**What it is**: Process images directly (no OCR needed)

**Your Current Flow**:
```
Receipt Image → OCR (LayoutLM) → Text → LangGraph
```

**Potential New Flow**:
```
Receipt Image → Ollama Vision Model → Structured Data
```

**Benefits**:
- Fewer steps
- Potentially better accuracy
- One API call instead of OCR + analysis

**Models Available**:
- Google's Gemma 3:27B (vision)
- Mistral's Mistral-Small:24B (vision)

**Recommendation**: ⭐ **HIGH VALUE** - Could significantly simplify architecture

---

## Recommended Upgrade Path

### Immediate (High Impact)

#### 1. Add Structured Outputs

**File**: `receipt_label/langchain/nodes/phase1.py`

```python
# Update existing code
llm = ChatOllama(
    model="gpt-oss:120b",
    base_url="https://ollama.com",
    client_kwargs={
        "headers": {"Authorization": f"Bearer {ollama_api_key}"}
    },
    format="json",  # NEW: Enable structured outputs
    temperature=0.3,  # Lower temperature for more consistent outputs
)
```

**Why**: More reliable, fewer parsing errors

---

#### 2. Consider Streaming for Development

**File**: `receipt_label/langchain/currency_validation.py`

```python
# Add async streaming for debugging
async def analyze_with_streaming(client, image_id, receipt_id, ollama_api_key):
    """Analyze with streaming to see progress."""
    result = await unified_graph.astream(initial_state, config={...})
    
    async for state in result:
        print(f"Node: {state.keys()}")  # Show current node
        if "currency_labels" in state:
            print(f"Found {len(state['currency_labels'])} labels")
```

**Why**: Better debugging, see what's happening in real-time

---

### Medium-Term (High Value)

#### 3. Explore Vision Models

**New Research**:
```python
# Test if vision models can replace OCR
vision_llm = ChatOllama(
    model="llama3.2-vision",  # or other vision model
    base_url="https://ollama.com",
    client_kwargs={
        "headers": {"Authorization": f"Bearer {ollama_api_key}"}
    },
)

# Direct image analysis
response = await vision_llm.ainvoke(
    {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract structured receipt data"},
                    {"type": "image_url", "image_url": {"url": s3_url}},
                ]
            }
        ]
    }
)
```

**Benefits**:
- Eliminate OCR step
- Single API call
- Potentially better accuracy

**Recommendation**: Create a proof-of-concept to test

---

### Future Considerations

#### 4. OpenAI API Compatibility

If you want to standardize on OpenAI API:

```python
# Switch to OpenAI SDK
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url="https://ollama.com/v1",
    api_key=ollama_api_key,
)

response = await client.chat.completions.create(
    model="gpt-oss:120b",
    messages=[...],
    response_format={"type": "json_object"},  # Structured outputs
)
```

**Why**: Consistent API across providers

---

## Specific Code Improvements

### Current Phase 1 (currency_validation.py line 23-29)

```python
# BEFORE
llm = ChatOllama(
    model="gpt-oss:120b",
    base_url="https://ollama.com",
    client_kwargs={
        "headers": {"Authorization": f"Bearer {ollama_api_key}"}
    },
)
```

```python
# AFTER (Recommended improvements)
llm = ChatOllama(
    model="gpt-oss:120b",
    base_url="https://ollama.com",
    client_kwargs={
        "headers": {"Authorization": f"Bearer {ollama_api_key}"}
    },
    format="json",  # ⭐ NEW: Structured outputs
    temperature=0.3,  # ⭐ Lower temperature for consistency
    timeout=120,  # ⭐ Add timeout for reliability
    # streaming=True,  # ⭐ Optional: Enable streaming
)
```

---

## Performance Optimizations

### 1. Model Size Selection

**Current**:
- Phase 1: `gpt-oss:120b` (large, slow but accurate)
- Phase 2: `gpt-oss:20b` (smaller, faster)

**Consider**:
- Try `gpt-oss:20b` for Phase 1 too (faster, cheaper, may be accurate enough)
- Or use `llama3.2:90b` if available
- Balance between cost and accuracy

### 2. Batch Processing

**Current**: Sequential processing per receipt

**Potential**: Batch multiple receipts together:

```python
# Multiple receipts in one call
messages = [
    f"Receipt {i}: {receipt_text}"
    for i, receipt_text in enumerate(receipt_texts)
]
```

**Benefits**: Fewer API calls, lower cost

---

## Cost Analysis

### Current Setup
- Ollama Turbo: $20/month flat
- 120B model: Heavy but within budget
- 20B model: Lighter for line items

### Alternative Models
- **llama3.2:90b**: Similar quality, may be faster
- **llama3.2:70b**: Faster, lower cost on Turbo
- **mistral-small:24b**: Vision-capable, very fast

---

## Testing Strategy

### Test Structured Outputs
```python
# Create test script
async def test_structured_outputs():
    llm = ChatOllama(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        format="json",  # Enable structured outputs
    )
    
    response = await llm.ainvoke("Extract currency amounts")
    
    # Should always return valid JSON
    assert isinstance(response.content, str)
    json.loads(response.content)  # Should not raise
```

### Test Vision Models
```python
async def test_vision_analysis():
    vision_llm = ChatOllama(model="llama3.2-vision")
    
    response = await vision_llm.ainvoke({
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Analyze this receipt"},
                {"type": "image_url", "image_url": receipt_s3_url}
            ]
        }]
    })
```

---

## Summary

### Keep Doing ✅
- Ollama Turbo cloud service
- API key authentication
- Two-phase approach (currency → line items)

### Upgrade Now 🎯
1. **Add structured outputs** (`format="json"`)
2. **Lower temperature** (more consistent outputs)
3. **Add timeouts** (better error handling)

### Explore 🚀
1. **Vision models** (eliminate OCR dependency)
2. **Streaming** (better UX/debugging)
3. **Different model sizes** (cost/performance balance)

### Skip (For Now) ❌
- Secure Minions (cloud-only approach is fine)
- OpenAI API compatibility (current approach works)
- Desktop app (not relevant for server-side)

---

## Next Steps

1. **Test structured outputs** - Update phase1.py and phase2.py
2. **Run comparison** - Current vs structured outputs
3. **Explore vision** - POC with direct image analysis
4. **Consider streaming** - Add for development mode


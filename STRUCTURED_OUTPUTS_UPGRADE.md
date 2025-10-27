# Structured Outputs Upgrade Plan

## Current Implementation Analysis

### What We Have Now
```python
# Phase 1 node
llm = ChatOllama(format="json", ...)
output_parser = PydanticOutputParser(pydantic_object=Phase1Response)
prompt = PromptTemplate(...)
chain = prompt | llm | output_parser  # 3-step chain
```

**Issues**:
1. Still uses `PydanticOutputParser` (extra parsing step)
2. Requires prompt + LLM + parser chain
3. More code, more complexity

### What We Could Have
```python
# Modern approach
llm = ChatOllama(...)
llm_structured = llm.with_structured_output(Phase1Response)
response = await llm_structured.ainvoke(messages)  # Direct Pydantic object!
```

**Benefits**:
1. Returns Pydantic object directly (no parser needed)
2. Simpler code
3. Better error handling
4. Type-safe responses

---

## Research Findings

### Option 1: Use `with_structured_output()` (Recommended)

**ChatOllama Support**: ✅ Likely supported (LangChain standard)

**Implementation**:
```python
from langchain_ollama import ChatOllama

llm = ChatOllama(
    model="gpt-oss:120b",
    base_url="https://ollama.com",
    client_kwargs={
        "headers": {"Authorization": f"Bearer {ollama_api_key}"}
    },
    temperature=0.3,
)

# Bind structured output
llm_structured = llm.with_structured_output(Phase1Response)

# Use directly
messages = [{"role": "user", "content": prompt}]
response = await llm_structured.ainvoke(messages)

# response is already a Phase1Response Pydantic object!
# No parsing needed
```

**Pros**:
- ✅ Returns Pydantic object directly
- ✅ Eliminates PydanticOutputParser
- ✅ Simpler code
- ✅ Better error messages
- ✅ Type-safe

**Cons**:
- Need to verify ChatOllama support
- Need to restructure prompts (messages format)

---

### Option 2: Keep Current + Remove `format="json"` (Current Approach)

**What we just did**: Added `format="json"` but still use PydanticOutputParser

**This approach**:
```python
llm = ChatOllama(format="json", ...)  # Force JSON mode
output_parser = PydanticOutputParser(...)  # Still needed
chain = prompt | llm | output_parser
```

**Analysis**:
- `format="json"` ensures JSON response from LLM
- Still need `PydanticOutputParser` to convert JSON → Pydantic
- Redundant but works

**Can we remove PydanticOutputParser?**
- ⚠️ NO - still need parsing step
- The `format="json"` just makes the LLM return JSON string
- PydanticOutputParser converts that string to Pydantic object
- Still 3 steps: prompt → LLM → parser

---

## Recommended Approach: Test `with_structured_output()`

### Step 1: Test If ChatOllama Supports It

```python
# Test script
from langchain_ollama import ChatOllama
from receipt_label.langchain.models import Phase1Response

llm = ChatOllama(
    model="gpt-oss:120b",
    base_url="https://ollama.com",
    client_kwargs={
        "headers": {"Authorization": f"Bearer {api_key}"}
    },
    temperature=0.3,
)

# Try the modern approach
try:
    llm_structured = llm.with_structured_output(Phase1Response)
    print("✅ with_structured_output is supported!")
except AttributeError:
    print("❌ with_structured_output not supported, need PydanticOutputParser")
```

---

## Code Changes Needed (If Supported)

### Phase 1 Node - Before
```python
# Current (3 steps)
llm = ChatOllama(...)
output_parser = PydanticOutputParser(pydantic_object=Phase1Response)
prompt = PromptTemplate(...)

chain = prompt | llm | output_parser
response = await chain.ainvoke({...})

# response.content is Pydantic object
currency_labels = response.currency_labels
```

### Phase 1 Node - After (If using with_structured_output)
```python
# New (2 steps, no parser)
llm = ChatOllama(...)
llm_structured = llm.with_structured_output(Phase1Response)

# Build messages directly
messages = [
    {
        "role": "system",
        "content": "You are analyzing a receipt..."
    },
    {
        "role": "user",
        "content": f"RECEIPT TEXT:\n{state.formatted_text}\n\nFind currency amounts..."
    }
]

# Direct Pydantic object return
response = await llm_structured.ainvoke(messages)

# response is already Phase1Response!
currency_labels = response.currency_labels
```

---

## What Gets Removed?

### In Phase 1:
1. ❌ Remove: `PydanticOutputParser` (line 52)
2. ❌ Remove: `PromptTemplate` (lines 53-59)
3. ❌ Remove: Chain composition (line 61)
4. ✅ Keep: `ChatOllama` initialization
5. ✅ Keep: LLM invocation

**Before**: 4 dependencies (ChatOllama + PydanticOutputParser + PromptTemplate + chain)
**After**: 2 dependencies (ChatOllama + with_structured_output binding)

### In Phase 2:
Same simplifications

---

## Fallback: If `with_structured_output` Not Supported

Keep current approach but optimize:

```python
# Current approach (what we have now)
llm = ChatOllama(
    format="json",  # ✅ Keep this
    temperature=0.3,
    ...
)

# Still need PydanticOutputParser
output_parser = PydanticOutputParser(...)
chain = prompt | llm | output_parser

# But we can't remove the parser step
```

**Rationale**: If ChatOllama doesn't support `with_structured_output()`, we MUST keep the parser.

---

## Testing Plan

### Step 1: Check Support
```python
# Test if ChatOllama supports with_structured_output
python -c "from langchain_ollama import ChatOllama; print(hasattr(ChatOllama, 'with_structured_output'))"
```

### Step 2: If Supported, Update Code
- Remove PydanticOutputParser from phase1.py
- Remove PydanticOutputParser from phase2.py
- Update to use messages format instead of PromptTemplate

### Step 3: If Not Supported
- Keep current `format="json"` approach
- Keep PydanticOutputParser
- Document why

---

## Expected Benefits (If Supported)

### Removed Code (per file):
```python
# Remove these imports
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate

# Remove these lines
output_parser = PydanticOutputParser(...)  # ~5 lines
prompt = PromptTemplate(...)  # ~10 lines  
chain = prompt | llm | output_parser  # 1 line

# Simplify to:
llm_structured = llm.with_structured_output(Phase1Response)
response = await llm_structured.ainvoke(messages)
```

**Impact**:
- ~16 lines removed per file
- ~32 lines removed total (phase1 + phase2)
- Simpler code paths
- Fewer dependencies

---

## Action Items

1. **Test support**: Check if ChatOllama supports `with_structured_output()`
2. **If yes**: Refactor phase1.py and phase2.py to use it
3. **If no**: Keep current approach with `format="json"`
4. **Document**: Why we're using this approach

---

## Next Steps

1. Run the test to check ChatOllama support
2. If supported, implement `with_structured_output`
3. If not, keep current approach
4. Update documentation


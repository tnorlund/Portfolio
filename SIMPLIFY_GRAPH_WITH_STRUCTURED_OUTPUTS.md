# Simplifying Graph with Structured Outputs

## ✅ Test Results

**ChatOllama supports `with_structured_output()`**: YES

The test shows it works! We can significantly simplify the code.

---

## What We Can Remove

### Current Approach (Per File)

**phase1.py** - Currently:
- ❌ `PydanticOutputParser` (5-10 lines)
- ❌ `PromptTemplate` (10-15 lines)
- ❌ Chain composition `prompt | llm | output_parser` (5 lines)
- ❌ Parsing logic (10 lines)

**Total**: ~30-40 lines of code per node

### New Approach (Per File)

**phase1.py** - Simplified:
- ✅ Just `llm.with_structured_output()`
- ✅ Direct Pydantic object return
- ✅ No parsing needed

**Total**: ~10-15 lines of code per node

---

## Code Comparison

### Phase 1 - Before (Current)

```python
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate

llm = ChatOllama(...)
output_parser = PydanticOutputParser(pydantic_object=Phase1Response)
prompt = PromptTemplate(
    template="...",
    input_variables=["..."],
    partial_variables={...}
)

chain = prompt | llm | output_parser
response = await chain.ainvoke({...})

# Response is AIMessage with Pydantic object in content
currency_labels = response.currency_labels
```

### Phase 1 - After (Simplified)

```python
# Just these imports needed
from receipt_label.langchain.models import Phase1Response

llm = ChatOllama(...)
llm_structured = llm.with_structured_output(Phase1Response)

# Build messages directly
messages = [
    {"role": "user", "content": f"...receipt_text..."}
]

# Direct Pydantic object
response = await llm_structured.ainvoke(messages)

# response IS the Pydantic object
currency_labels = response.currency_labels
```

**Lines Saved**: ~30 lines per node = ~60 lines total (phase1 + phase2)

---

## Specific Removals

### From phase1.py (Remove):
1. Line 3: `from langchain_core.output_parsers import PydanticOutputParser`
2. Line 4: `from langchain_core.prompts import PromptTemplate`
3. Lines 52-61: `output_parser = ...`, `prompt = ...`, `chain = ...`
4. Lines 87-88: Manual parsing of `response.currency_labels`

### From phase2.py (Remove):
Same removals as phase1

---

## How to Refactor

### Step 1: Update Phase 1

```python
# OLD
async def phase1_currency_analysis(state, ollama_api_key):
    llm = ChatOllama(..., format="json", ...)
    output_parser = PydanticOutputParser(Phase1Response)
    prompt = PromptTemplate(...)
    chain = prompt | llm | output_parser
    response = await chain.ainvoke({...})
    currency_labels = [CurrencyLabel(...) for item in response.currency_labels]
    return {"currency_labels": currency_labels}

# NEW
async def phase1_currency_analysis(state, ollama_api_key):
    llm = ChatOllama(...)
    llm_structured = llm.with_structured_output(Phase1Response)
    
    messages = [{
        "role": "user",
        "content": f"RECEIPT TEXT:\n{state.formatted_text}\n\nFind currency amounts..."
    }]
    
    response = await llm_structured.ainvoke(messages)
    
    # response IS Phase1Response - no parsing needed
    return {"currency_labels": response.currency_labels}
```

### Step 2: Update Phase 2

Same pattern for line item analysis.

---

## Benefits

### 1. Simpler Code
- Fewer imports
- Fewer abstractions
- Easier to understand

### 2. Better Type Safety
- Direct Pydantic objects
- Type checking works
- Better IDE support

### 3. Fewer Steps in Graph
- No parsing step
- Faster execution
- Fewer error points

### 4. Less Dependencies
- Remove PromptTemplate dependency
- Remove PydanticOutputParser dependency
- Only need ChatOllama + models

---

## Migration Steps

1. ✅ Test confirmed `with_structured_output()` works
2. ⏳ Update phase1.py to use new approach
3. ⏳ Update phase2.py to use new approach  
4. ⏳ Remove unused imports
5. ⏳ Test with actual receipt
6. ⏳ Deploy

---

## Implementation Plan

### Do This Now:

**Option A**: Quick upgrade (just add structured outputs)
- ✅ Done: Added `format="json"` to ChatOllama
- Keep `PydanticOutputParser` for now
- Works but not optimal

**Option B**: Full simplification (use with_structured_output)
- ⏳ Replace PydanticOutputParser with with_structured_output
- Remove PromptTemplate
- Simplify code significantly

---

## Recommendation

Go with **Option B** - Full simplification.

Reason:
- ✅ We confirmed it works
- ✅ Significantly simpler code
- ✅ Better type safety
- ✅ Fewer dependencies
- ✅ Same functionality

Estimated effort: 1-2 hours
Lines saved: ~60 lines of code
Benefits: Simpler, cleaner, more maintainable


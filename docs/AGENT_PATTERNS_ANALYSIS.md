# Agent Patterns Analysis: What Works and What Doesn't

## Overview

Analysis of existing agent implementations (Places Agent, Harmonizer Agent, Place ID Finder) to identify successful patterns and anti-patterns for the Label Validation Agent.

## Successful Patterns

### 1. Context Injection via State Holder

**Pattern**: Use a `state_holder` dict to inject runtime context into tools.

**Example** (from `agentic.py`):
```python
state = {"context": None, "decision": None}

@tool
def get_my_lines() -> list[dict]:
    ctx: ReceiptContext = state["context"]
    if ctx is None:
        return [{"error": "No receipt context set"}]
    # Use ctx.image_id, ctx.receipt_id, etc.
```

**Why it works**:
- Tools are stateless functions that can be bound to LLM
- Context is injected at runtime, not at tool creation time
- Easy to test (can mock the state_holder)
- Works well with LangGraph's ToolNode

**Apply to Label Validation Agent**: ✅ Use `WordContext` in state_holder

### 2. Clear Tool Categories

**Pattern**: Organize tools into logical categories with clear purposes.

**Example** (from `place_id_finder_workflow.py`):
- **Context Tools**: `get_my_metadata`, `get_my_lines`, `get_my_words`
- **Similarity Search Tools**: `find_similar_to_my_line`, `find_similar_to_my_word`
- **Text Search Tools**: `search_lines`, `search_words`
- **Aggregation Tools**: `get_merchant_consensus`, `get_place_id_info`
- **Google Places Tools**: `verify_with_google_places`
- **Decision Tool**: `submit_place_id`

**Why it works**:
- Agent can reason about which category to use
- System prompt can guide agent to use tools in priority order
- Clear separation of concerns

**Apply to Label Validation Agent**: ✅ Organize tools into:
- Context Tools (word, receipt, merchant)
- Similarity Search Tools (ChromaDB)
- Label History Tools (audit trail)
- Decision Tool (submit_decision)

### 3. Rich Context in Tool Returns

**Pattern**: Tools return rich, structured data with all relevant context.

**Example** (from `find_similar_to_my_word`):
```python
output.append({
    "image_id": meta.get("image_id"),
    "receipt_id": meta.get("receipt_id"),
    "text": doc,
    "label": meta.get("label"),
    "similarity": round(similarity, 4),
})
```

**Why it works**:
- Agent has all information needed to make decisions
- Reduces need for follow-up tool calls
- Similarity scores help agent prioritize evidence

**Apply to Label Validation Agent**: ✅ Return:
- Similar words with labels, validation_status, similarity, context
- Word context with line, surrounding lines, surrounding words
- Label history with consolidation chain, audit trail

### 4. Automatic Exclusion of Current Receipt

**Pattern**: Similarity search tools automatically exclude the current receipt.

**Example** (from `find_similar_to_my_word`):
```python
# Skip if same receipt
if (meta.get("image_id") == ctx.image_id and
    int(meta.get("receipt_id", -1)) == ctx.receipt_id):
    continue
```

**Why it works**:
- Prevents agent from comparing word to itself
- Agent doesn't need to reason about excluding self
- Cleaner results

**Apply to Label Validation Agent**: ✅ Exclude current word from similarity search

### 5. Structured Decision Tool with Confidence

**Pattern**: Decision tool requires structured input with confidence score.

**Example** (from `submit_place_id`):
```python
class SubmitPlaceIdInput(BaseModel):
    place_id: Optional[str]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    search_methods_used: list[str]
```

**Why it works**:
- Forces agent to be explicit about confidence
- Reasoning field helps with debugging
- Can track which methods agent used

**Apply to Label Validation Agent**: ✅ Use structured decision with:
- decision: VALID/INVALID/NEEDS_REVIEW
- confidence: 0.0-1.0
- reasoning: explanation
- evidence: list of key findings

### 6. Clear System Prompt with Strategy

**Pattern**: System prompt includes clear strategy and priority order.

**Example** (from `PLACE_ID_FINDER_PROMPT`):
```
## Strategy

1. **Start** by examining the receipt:
   - Get metadata to see what's already known
   - Get lines to see all text on the receipt
   - Get words to see labeled fields

2. **PRIMARY: Search Google Places API** (REQUIRED):
   - **ALWAYS** use `verify_with_google_places`
   - Try phone number first (most reliable)
   - Then try address if available
   - Finally try merchant name text search

3. **SECONDARY: Verify with similar receipts** (optional verification only):
   - Use similarity search to find other receipts
   - Use get_merchant_consensus ONLY to verify/confirm
```

**Why it works**:
- Agent knows what to do first
- Clear priority order (PRIMARY vs SECONDARY)
- Reduces agent confusion

**Apply to Label Validation Agent**: ✅ Include:
- Priority order: word context (PRIMARY) > merchant metadata > similar words (SECONDARY)
- Clear decision criteria
- When to be confident vs conservative

### 7. Error Handling in Tools

**Pattern**: Tools return error dicts instead of raising exceptions.

**Example**:
```python
if ctx is None:
    return [{"error": "No receipt context set"}]

try:
    # ... tool logic ...
except Exception as e:
    logger.error(f"Error: {e}")
    return [{"error": str(e)}]
```

**Why it works**:
- Agent can handle errors gracefully
- Doesn't crash the workflow
- Agent can reason about errors and try alternatives

**Apply to Label Validation Agent**: ✅ All tools return error dicts on failure

## Anti-Patterns (What Doesn't Work)

### 1. Too Many Tools

**Anti-Pattern**: Having 20+ tools makes it hard for agent to choose.

**Example**: If we had separate tools for:
- `get_line_text`
- `get_surrounding_lines`
- `get_surrounding_words`
- `get_receipt_metadata`
- `get_merchant_name`
- etc.

**Why it doesn't work**:
- Agent gets confused about which tool to use
- Too many tool calls needed to gather context
- Slower execution

**Solution**: Combine related tools:
- ✅ `get_word_context` returns line, surrounding lines, surrounding words, receipt metadata all at once

### 2. Tools That Require Multiple Calls

**Anti-Pattern**: Agent needs to call tool multiple times to get complete picture.

**Example**: If `get_similar_words` only returned 5 results, agent would need to call it multiple times.

**Why it doesn't work**:
- Wastes tokens and time
- Agent might not realize it needs more data
- Inconsistent results

**Solution**: Tools return complete, useful results:
- ✅ `search_similar_words` returns 20-30 results with all context
- ✅ `get_word_context` returns all context in one call

### 3. Vague Tool Descriptions

**Anti-Pattern**: Tool descriptions don't explain when/why to use the tool.

**Example**:
```python
@tool
def search_words(query: str) -> list[dict]:
    """Search for words."""
```

**Why it doesn't work**:
- Agent doesn't know when to use it
- Agent might use wrong tool
- Wastes tool calls

**Solution**: Clear, detailed descriptions:
```python
@tool
def search_similar_words(n_results: int = 20) -> list[dict]:
    """
    Search ChromaDB for semantically similar words with their labels.

    Returns similar words with:
    - text, label, validation_status
    - similarity score (0.0-1.0)
    - context (line, surrounding lines)
    - merchant_name

    Use this to see if other similar words have this label type.
    This is SECONDARY evidence - word context is PRIMARY.
    """
```

### 4. No Guidance on Tool Priority

**Anti-Pattern**: System prompt doesn't tell agent which tools to use first.

**Why it doesn't work**:
- Agent might start with wrong tool
- Wastes time and tokens
- Less accurate results

**Solution**: Clear priority in system prompt:
```
## Tool Priority

1. **PRIMARY**: `get_word_context` - Get full context for the word (line, surrounding lines, etc.)
2. **SECONDARY**: `get_merchant_metadata` - Get Google Places data (mostly accurate)
3. **SUPPORTING**: `search_similar_words` - Find similar words with labels (supporting evidence only)
4. **HISTORY**: `get_all_labels_for_word` - See label history (helps understand context)
```

### 5. Binary Decisions Without Confidence

**Anti-Pattern**: Decision tool only accepts VALID/INVALID, no confidence.

**Why it doesn't work**:
- Can't distinguish high-confidence vs low-confidence decisions
- All decisions treated equally
- Can't optimize for accuracy

**Solution**: Include confidence score:
```python
class SubmitDecisionInput(BaseModel):
    decision: Literal["VALID", "INVALID", "NEEDS_REVIEW"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
```

### 6. Fast Failure Before Gathering Context

**Anti-Pattern**: Check edge cases first, fail immediately if found.

**Why it doesn't work**:
- Might reject valid labels that happen to match edge case pattern
- Doesn't use full context to make decision
- Less accurate

**Solution**: Gather all context first, then make decision:
- ✅ Remove fast failure
- ✅ Agent gathers all context
- ✅ Agent reasons about edge cases in context of full evidence
- ✅ Edge cases are supporting evidence, not definitive rejection

## Recommendations for Label Validation Agent

### Tool Design

1. **Combine Related Context**:
   - ✅ `get_word_context` returns line, surrounding lines, surrounding words, receipt metadata all at once
   - ✅ `get_all_labels_for_word` returns complete audit trail with consolidation chain

2. **Rich Similarity Search**:
   - ✅ `search_similar_words` returns 20-30 results with labels, validation_status, similarity, context
   - ✅ Automatically excludes current word
   - ✅ Includes both valid_labels and invalid_labels from metadata

3. **Clear Tool Descriptions**:
   - ✅ Each tool description explains when/why to use it
   - ✅ Indicates priority (PRIMARY, SECONDARY, SUPPORTING)

### System Prompt Design

1. **Clear Priority Order**:
   ```
   ## Decision Criteria (in priority order)

   1. **Word Context (PRIMARY)**: Where does word appear? What surrounds it?
   2. **Merchant Metadata (SECONDARY)**: Does it match merchant name/address?
   3. **Similar Words (SUPPORTING)**: Do similar words have this label?
   4. **Label History (CONTEXT)**: Has this word been labeled before?
   ```

2. **Confidence Guidelines**:
   ```
   ## Confidence Scoring

   - **High (0.8-1.0)**: Word context clearly supports/contradicts label
   - **Medium (0.5-0.8)**: Some supporting evidence, minor conflicts
   - **Low (0.0-0.5)**: Ambiguous, conflicting signals → NEEDS_REVIEW
   ```

3. **Decision Guidelines**:
   ```
   ## Decision Making

   - **VALID**: High confidence (>80%) - word clearly matches label definition
   - **INVALID**: High confidence (>80%) - word clearly does NOT match
   - **NEEDS_REVIEW**: Low confidence (<80%) - ambiguous, needs human review
   ```

### Workflow Design

1. **No Fast Failure**:
   - ❌ Don't check edge cases first and fail immediately
   - ✅ Gather all context first
   - ✅ Agent reasons about edge cases in context of full evidence

2. **Structured Decision**:
   - ✅ Decision tool requires confidence, reasoning, evidence
   - ✅ Can distinguish high-confidence vs low-confidence decisions

3. **Error Handling**:
   - ✅ All tools return error dicts, don't raise exceptions
   - ✅ Agent can handle errors and try alternatives

## Summary

**What Works**:
- Context injection via state_holder
- Clear tool categories
- Rich context in tool returns
- Automatic exclusion of current item
- Structured decision with confidence
- Clear system prompt with strategy
- Error handling in tools

**What Doesn't Work**:
- Too many tools
- Tools requiring multiple calls
- Vague tool descriptions
- No guidance on tool priority
- Binary decisions without confidence
- Fast failure before gathering context

**For Label Validation Agent**:
- Focus on accuracy, not speed
- Gather all context first
- Use edge cases as supporting evidence, not definitive rejection
- Return confidence scores
- Clear priority order in system prompt


# Agent Design: Initial Prompt vs Tools

## Decision Criteria

### Include in Initial Prompt When:

1. **Always Needed** ✅
   - Required for every validation/decision
   - Agent cannot make decision without it
   - Example: Word context, merchant metadata

2. **Fast to Fetch** ✅
   - < 100ms (simple DynamoDB queries)
   - No expensive operations (API calls, complex queries)
   - Example: Line text, surrounding words, receipt metadata

3. **Small/Concise** ✅
   - Won't bloat prompt significantly (< 2K tokens)
   - Can be formatted concisely
   - Example: Line text, merchant name, address

4. **Deterministic** ✅
   - Same data for same input (no variation)
   - No parameters needed
   - Example: Word context is always the same for a given word

5. **Primary Context** ✅
   - Essential for decision-making
   - Agent needs it to start reasoning
   - Example: Word context is PRIMARY decision factor

6. **Reduces Tool Calls** ✅
   - Saves LLM round-trips
   - Improves performance
   - Example: Providing word context upfront saves 1 tool call

### Keep as Tool When:

1. **Optional/Conditional** ⚠️
   - Only needed sometimes
   - Agent decides if needed
   - Example: Label history, receipt-wide labels

2. **Expensive to Fetch** ⚠️
   - > 500ms (API calls, complex queries, embeddings)
   - Resource-intensive operations
   - Example: Similarity search (ChromaDB + embedding)

3. **Large Data** ⚠️
   - Would bloat prompt significantly (> 2K tokens)
   - Many results or verbose output
   - Example: 20+ similar words with full context

4. **Variable Based on Agent's Needs** ⚠️
   - Agent controls when/how to use it
   - May need filtering or parameters
   - Example: Similarity search (agent decides n_results)

5. **Supporting Evidence** ⚠️
   - Not primary decision factor
   - Used for additional context
   - Example: Similar words are SUPPORTING evidence

## Current Implementation

### Initial Prompt (Always Provided)

#### `get_word_context` → Initial Prompt ✅
- **Always needed**: PRIMARY context for every validation
- **Fast**: DynamoDB queries (< 50ms)
- **Small**: Line text, surrounding words/lines (~500 tokens)
- **Deterministic**: Same for same word
- **Primary**: Essential for decision-making
- **Benefit**: Saves 1 tool call per validation

#### `get_merchant_metadata` → Initial Prompt ✅
- **Always needed**: SECONDARY context for every validation
- **Fast**: DynamoDB query (< 50ms)
- **Small**: Merchant name, address, phone (~200 tokens)
- **Deterministic**: Same for same receipt
- **Secondary**: Important but not primary
- **Benefit**: Saves 1 tool call per validation

### Tools (Called When Needed)

#### `search_similar_words` → Tool 🔧
- **Optional**: SUPPORTING evidence, not always needed
- **Expensive**: ChromaDB query + embedding (~500ms-2s)
- **Large**: 20+ similar words with full context (~3K-5K tokens)
- **Variable**: Agent decides if needed, controls n_results
- **Supporting**: Not primary decision factor
- **Reason**: Would bloat prompt, expensive, optional

#### `get_all_labels_for_word` → Tool 🔧
- **Optional**: Only if agent wants label history
- **Fast**: DynamoDB query (< 50ms)
- **Small**: Audit trail (~300 tokens)
- **Variable**: Agent decides if audit trail is relevant
- **Context**: Not primary decision factor
- **Reason**: Not always needed, agent decides

#### `get_labels_on_receipt` → Tool 🔧
- **Optional**: Only if agent wants receipt-wide context
- **Fast**: DynamoDB query (< 50ms)
- **Small**: Label counts (~200 tokens)
- **Variable**: Agent decides if needed
- **Context**: Not primary decision factor
- **Reason**: Not always needed, agent decides

## Decision Matrix

| Tool Name              | Always Needed | Fast | Small | Primary | Expensive | → Decision |
|------------------------|---------------|------|-------|---------|-----------|------------|
| `get_word_context`     | ✅ Yes        | ✅   | ✅    | ✅      | ❌        | → **INITIAL PROMPT** |
| `get_merchant_metadata`| ✅ Yes        | ✅   | ✅    | ⚠️      | ❌        | → **INITIAL PROMPT** |
| `search_similar_words` | ⚠️  No        | ❌   | ❌    | ⚠️      | ✅        | → **TOOL** |
| `get_all_labels_for_word` | ⚠️  No     | ✅   | ✅    | ⚠️      | ❌        | → **TOOL** |
| `get_labels_on_receipt` | ⚠️  No      | ✅   | ✅    | ⚠️      | ❌        | → **TOOL** |

## When to Move from Tool to Initial Prompt

Consider moving a tool to initial prompt if:

1. **High Usage Rate** (> 80% of validations call it)
   - Example: If `get_all_labels_for_word` is called in 90% of validations
   - Action: Include label history in initial prompt

2. **Performance Critical** (tool call is bottleneck)
   - Example: If tool call adds > 1s to validation time
   - Action: Pre-fetch and include in prompt

3. **Always Needed** (agent calls it 100% of the time)
   - Example: If agent always calls `get_word_context`
   - Action: Include in initial prompt (already done ✅)

4. **Small Data** (tool returns small, concise data)
   - Example: If tool returns < 500 tokens
   - Action: Consider including if also fast and always needed

## When to Move from Initial Prompt to Tool

Consider moving from initial prompt to tool if:

1. **Prompt Too Large** (> 10K tokens)
   - Example: If including all context makes prompt too large
   - Action: Move less critical context to tools

2. **Rarely Used** (< 20% of validations need it)
   - Example: If merchant metadata is only needed for MERCHANT_NAME labels
   - Action: Make it a tool, call conditionally

3. **Expensive to Fetch** (> 500ms)
   - Example: If fetching context becomes slow
   - Action: Make it a tool, fetch on-demand

4. **Variable/Parameterized** (needs parameters or filtering)
   - Example: If context needs filtering based on agent's needs
   - Action: Make it a tool with parameters

## Benefits of Current Approach

✅ **Performance**: Saves 2 tool calls per validation (~1-2s saved)
✅ **Efficiency**: Agent can start reasoning immediately
✅ **Flexibility**: Agent can still call tools for additional evidence
✅ **Cost**: Fewer LLM round-trips = lower cost
✅ **Speed**: Faster validation times

## Trade-offs

⚠️ **Prompt Size**: Initial prompt is larger (~1K-2K tokens)
⚠️ **Memory**: More data in prompt (but still manageable)
⚠️ **Flexibility**: Less flexibility if agent wants different context

## Best Practices

1. **Start with Tools**: Begin with tools, measure usage
2. **Measure Usage**: Track which tools are called most often
3. **Optimize Gradually**: Move high-usage tools to initial prompt
4. **Monitor Prompt Size**: Keep initial prompt < 5K tokens
5. **Balance**: Don't bloat prompt, but don't waste tool calls

## Example: Evolution

### Phase 1 (Initial): All Tools
- Agent calls `get_word_context` → 100% usage
- Agent calls `get_merchant_metadata` → 100% usage
- **Result**: 2 tool calls per validation

### Phase 2 (Optimized): Initial Prompt
- `get_word_context` → Initial prompt ✅
- `get_merchant_metadata` → Initial prompt ✅
- **Result**: 0 tool calls for context, agent starts reasoning immediately

### Phase 3 (Future): Further Optimization
- If `get_all_labels_for_word` usage > 80% → Move to initial prompt
- If `search_similar_words` becomes faster → Still keep as tool (large data)

## Summary

**Include in Initial Prompt**: Always needed, fast, small, primary context
**Keep as Tool**: Optional, expensive, large, variable, supporting evidence

The key is to balance **performance** (fewer tool calls) with **flexibility** (agent can get additional context when needed).





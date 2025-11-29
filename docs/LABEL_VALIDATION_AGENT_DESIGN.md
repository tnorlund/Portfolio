# Label Validation Agent Design

## Overview

Create a dedicated LangGraph agent specifically for validating label suggestions. This agent will have access to all necessary context (ChromaDB, DynamoDB, Google Places metadata) and use intelligent reasoning to make validation decisions.

## Problem Statement

Current validation (`_validate_suggestion_with_similarity`) has issues:
1. **Technical failures** cause validation to fail (ChromaDB missing, no embedding, etc.)
2. **LLM prompt is too conservative** - biased toward rejection
3. **Missing original word context** - only shows similar words' context
4. **No structured reasoning** - single LLM call, no multi-step reasoning

## Solution: Label Validation Agent

A LangGraph agent that:
- Has access to multiple tools (ChromaDB search, DynamoDB queries, Google Places metadata)
- Can reason through multiple steps
- Uses all available context intelligently
- Makes confident VALID/INVALID decisions (not just NEEDS_REVIEW)

## Agent Architecture

### Agent Tools

1. **`search_similar_words`**
   - Query ChromaDB for semantically similar words
   - Returns: similar words with their labels, validation status, context
   - Parameters: word_text, label_type, merchant_name (optional), n_results

2. **`get_word_context`**
   - Fetch full context for the word being validated
   - Returns: line text, surrounding lines, surrounding words, receipt metadata
   - Parameters: image_id, receipt_id, line_id, word_id

3. **`get_merchant_metadata`**
   - Fetch Google Places metadata for the receipt
   - Returns: merchant_name, address, phone, etc.
   - Parameters: image_id, receipt_id

4. **`get_all_labels_for_word`**
   - Get all labels that exist for this specific word
   - Returns: audit trail of all labels, their validation status, consolidation chain
   - Parameters: image_id, receipt_id, line_id, word_id

5. **`get_labels_on_receipt`**
   - Get all labels on the same receipt for context
   - Returns: label counts by type, labels on same line
   - Parameters: image_id, receipt_id

### Agent State

```python
@dataclass
class ValidationAgentState:
    # Input
    word_text: str
    suggested_label_type: str
    merchant_name: Optional[str]
    original_reasoning: str  # From the suggestion LLM

    # Context (populated by tools)
    # Note: In ReAct pattern, context is stored in tool results, not state
    # State only tracks conversation messages and final decision

    # Agent reasoning
    reasoning_steps: List[str] = field(default_factory=list)
    confidence: float = 0.0

    # Output
    decision: Optional[str] = None  # "VALID", "INVALID", "NEEDS_REVIEW"
    final_reasoning: Optional[str] = None
```

### Agent Graph

```
┌─────────────────┐
│  START          │
│  (Initial State) │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│  Agent Node             │
│  (LLM decides actions)  │
└────────┬────────────────┘
         │
         ├─→ Tool calls needed? → Tools Node
         │
         ├─→ Decision ready? → END
         │
         ▼
┌─────────────────────────┐
│  Tools Node             │
│  (Execute tools)        │
│  - get_word_context     │
│  - get_merchant_metadata│
│  - search_similar_words │
│  - get_all_labels_for_word│
│  - get_labels_on_receipt│
│  - submit_decision      │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  Back to Agent           │
│  (Continue reasoning)   │
└─────────────────────────┘
```

**Note**: This is a ReAct agent - the LLM autonomously decides which tools to call and in what order. The agent will gather all context it needs before making a decision.

## Agent Prompt Design

### System Prompt

```python
system_prompt = """You are a label validation agent for receipt processing.

Your task is to validate whether a suggested label type is correct for a word on a receipt.

You have access to:
1. **Word Context**: The word's line, surrounding lines, position on receipt
2. **Merchant Metadata**: Google Places data (merchant name, address, etc.) - mostly accurate
3. **Similar Words**: Semantically similar words from ChromaDB with their labels
4. **Edge Cases**: Known invalid patterns
5. **Label History**: All labels that exist for this word (audit trail)
6. **Receipt Context**: Other labels on the same receipt

## Decision Criteria (in priority order)

1. **Word Context (PRIMARY)**:
   - Where does the word appear? (header, products, totals, etc.)
   - What text surrounds it?
   - Does the context match the label type definition?

2. **Merchant Metadata (SECONDARY)**:
   - Does the word match merchant name components? (for MERCHANT_NAME)
   - Is the word part of the merchant's address? (for ADDRESS_LINE)
   - Use Google Places data as supporting evidence, not primary

3. **Similar Words (SUPPORTING)**:
   - Do similar words have this label as VALID? (positive signal)
   - Do similar words have this label as INVALID? (negative signal, but not definitive)
   - Use as supporting evidence, not primary

5. **Label History**:
   - Has this word been labeled before? What happened?
   - Consolidation chain shows cross-label confusion patterns
   - Use to understand context, not as primary decision factor

6. **Receipt Context**:
   - What other labels exist on this receipt?
   - Are there conflicting labels?
   - Use to understand overall receipt structure

## Decision Making

- **VALID**: High confidence (>80%) - word clearly matches label type definition in context
- **INVALID**: High confidence (>80%) - word clearly does NOT match label type definition
- **NEEDS_REVIEW**: Low confidence (<80%) - ambiguous, needs human review

Be confident when:
- Word context clearly supports/contradicts the label
- Merchant metadata strongly supports the label (for MERCHANT_NAME)
- Multiple similar words consistently have/don't have the label

Be conservative (NEEDS_REVIEW) when:
- Context is ambiguous
- Conflicting signals (some similar words have label, others don't)
- Word is in an unusual context
- Merchant metadata contradicts word context

## Output Format

Provide structured reasoning:
1. What context did you review?
2. What patterns did you identify?
3. What's your confidence level?
4. What's your decision (VALID/INVALID/NEEDS_REVIEW)?
5. Why?
"""
```

### Tool Descriptions

Each tool should have clear descriptions for the LLM:

```python
tools = [
    Tool(
        name="search_similar_words",
        description="Search ChromaDB for semantically similar words. Returns similar words with their labels, validation status, and context. Use this to see if other similar words have this label type.",
        func=search_similar_words,
    ),
    Tool(
        name="get_word_context",
        description="Get full context for the word being validated. Returns the line the word is on, surrounding lines, and surrounding words. This is PRIMARY context for decision-making.",
        func=get_word_context,
    ),
    Tool(
        name="get_merchant_metadata",
        description="Get Google Places metadata for the receipt. Returns merchant name, address, phone, etc. This is mostly accurate and useful for validating MERCHANT_NAME and ADDRESS_LINE labels.",
        func=get_merchant_metadata,
    ),
    Tool(
        name="check_edge_cases",
        description="Check if the word/label combination matches a known invalid pattern. Returns immediately if found - this is a fast rejection mechanism.",
        func=check_edge_cases,
    ),
    Tool(
        name="get_all_labels_for_word",
        description="Get all labels that exist for this specific word. Shows the audit trail and consolidation chain. Use this to understand if there's cross-label confusion.",
        func=get_all_labels_for_word,
    ),
    Tool(
        name="get_labels_on_receipt",
        description="Get all labels on the same receipt. Returns label counts by type and labels on the same line. Use this to understand receipt structure and context.",
        func=get_labels_on_receipt,
    ),
]
```

## Agent Workflow

### Step 1: Gather Context (Parallel)

```python
async def gather_context_step(state: ValidationAgentState) -> ValidationAgentState:
    """Gather all context in parallel."""
    # Parallel tool calls
    word_context, merchant_metadata, similar_words, all_labels, receipt_labels = await asyncio.gather(
        get_word_context(state.image_id, state.receipt_id, state.line_id, state.word_id),
        get_merchant_metadata(state.image_id, state.receipt_id),
        search_similar_words(state.word_text, state.suggested_label_type, state.merchant_name),
        get_all_labels_for_word(state.image_id, state.receipt_id, state.line_id, state.word_id),
        get_labels_on_receipt(state.image_id, state.receipt_id),
    )

    state.word_context = word_context
    state.merchant_metadata = merchant_metadata
    state.similar_words = similar_words
    state.all_labels_for_word = all_labels
    state.labels_on_receipt = receipt_labels

    return state
```

### Step 2: Analyze with LLM (Reasoning)

```python
async def analyze_context_step(state: ValidationAgentState) -> ValidationAgentState:
    """LLM analyzes all context and makes decision."""

    prompt = build_validation_prompt(state)

    # Use structured output for decision
    decision = await llm_structured.ainvoke(prompt)

    state.decision = decision.decision  # VALID, INVALID, or NEEDS_REVIEW
    state.confidence = decision.confidence
    state.final_reasoning = decision.reasoning
    state.reasoning_steps = decision.reasoning_steps

    return state
```

### Step 3: Final Decision

```python
async def finalize_decision_step(state: ValidationAgentState) -> ValidationAgentState:
    """Finalize and return decision."""
    # Decision already set in analyze_context_step
    return state
```

## Integration with Harmonizer

### Replace Current Validation

```python
# In label_harmonizer_v2.py
async def _validate_suggestion_with_agent(
    self,
    word: LabelRecord,
    suggested_label_type: str,
    merchant_name: str,
    llm_reason: str,
) -> Tuple[bool, Optional[str], float]:
    """
    Validate suggestion using Label Validation Agent.

    Returns:
        (is_valid, reasoning, confidence)
    """
    from receipt_agent.tools.label_validation_agent import LabelValidationAgent

    agent = LabelValidationAgent(
        dynamo=self.dynamo,
        chroma=self.chroma,
        llm=self.llm,
    )

    result = await agent.validate(
        word_text=word.word_text,
        suggested_label_type=suggested_label_type,
        merchant_name=merchant_name,
        original_reasoning=llm_reason,
        image_id=word.image_id,
        receipt_id=word.receipt_id,
        line_id=word.line_id,
        word_id=word.word_id,
    )

    if result.decision == "VALID":
        return True, result.final_reasoning, result.confidence
    elif result.decision == "INVALID":
        return False, result.final_reasoning, result.confidence
    else:  # NEEDS_REVIEW
        return False, result.final_reasoning, result.confidence  # Still return False, but with low confidence
```

### Update apply_fixes_from_results

```python
# In apply_fixes_from_results
if is_outlier:
    if has_validated_suggestion:
        # High confidence validation → create new label
        labels_to_update.append(r)
    elif validation_confidence < 50.0:
        # Low confidence → mark as INVALID (clearly wrong, just not sure what it should be)
        labels_to_mark_invalid.append(r)
    else:
        # Medium confidence → NEEDS_REVIEW
        labels_for_review.append(r)
```

## Agent Implementation Structure

Following the existing pattern in `receipt_agent/receipt_agent/graph/agentic_workflow.py`:

```
receipt_agent/receipt_agent/graph/
├── label_validation_workflow.py       # Main workflow builder (like agentic_workflow.py)
└── receipt_agent/receipt_agent/tools/
    └── label_validation_tools.py      # Tool implementations (like agentic.py)
```

### File: `label_validation_workflow.py`

```python
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field
from typing import Annotated
from langgraph.graph.message import add_messages

class LabelValidationState(BaseModel):
    """State for label validation agent."""
    # Input
    word_text: str
    suggested_label_type: str
    merchant_name: Optional[str]
    original_reasoning: str
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int

    # Messages for agent conversation
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    # Terminal state
    decision: Optional[dict] = Field(default=None, description="Final decision when complete")

def create_label_validation_graph(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    settings: Optional[Settings] = None,
) -> tuple[Any, dict]:
    """Create the label validation workflow graph."""
    # Create tools with injected dependencies
    tools, state_holder = create_label_validation_tools(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
    )

    # Create LLM with tools bound
    llm = ChatOllama(...).bind_tools(tools)

    # Agent node
    def agent_node(state: LabelValidationState) -> dict:
        response = llm.invoke(state.messages)
        return {"messages": [response]}

    # Tool node
    tool_node = ToolNode(tools)

    # Routing
    def should_continue(state: LabelValidationState) -> str:
        if state_holder.get("decision") is not None:
            return "end"
        if state.messages and isinstance(state.messages[-1], AIMessage):
            if state.messages[-1].tool_calls:
                return "tools"
        return "end"

    # Build graph
    workflow = StateGraph(LabelValidationState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    workflow.add_edge("tools", "agent")

    return workflow.compile(), state_holder
```

### File: `label_validation_tools.py`

Following the pattern from `receipt_agent/receipt_agent/tools/agentic.py`:

```python
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from dataclasses import dataclass

@dataclass
class WordContext:
    """Context for the word being validated. Injected at runtime."""
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    word_text: str
    suggested_label_type: str
    merchant_name: Optional[str]
    original_reasoning: str

def create_label_validation_tools(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
) -> tuple[list[Any], dict]:
    """Create tools for label validation agent."""
    state = {"context": None, "decision": None}

    @tool
    def get_word_context() -> dict:
        """Get full context for the word: line, surrounding lines, surrounding words."""
        ctx: WordContext = state["context"]
        # Fetch from dynamo_client
        ...

    @tool
    def get_merchant_metadata() -> dict:
        """Get Google Places metadata for the receipt."""
        ctx: WordContext = state["context"]
        # Fetch from dynamo_client.get_receipt_metadata()
        ...

    @tool
    def search_similar_words(n_results: int = 20) -> list[dict]:
        """Search ChromaDB for semantically similar words with their labels."""
        ctx: WordContext = state["context"]
        # Query chroma_client
        ...

    @tool
    def get_all_labels_for_word() -> list[dict]:
        """Get all labels for this word (audit trail, consolidation chain)."""
        ctx: WordContext = state["context"]
        # Fetch from dynamo_client
        ...

    @tool
    def get_labels_on_receipt() -> dict:
        """Get all labels on the same receipt for context."""
        ctx: WordContext = state["context"]
        # Fetch from dynamo_client
        ...

    @tool
    def submit_decision(
        decision: Literal["VALID", "INVALID", "NEEDS_REVIEW"],
        confidence: float,
        reasoning: str,
    ) -> dict:
        """Submit final validation decision."""
        state["decision"] = {
            "decision": decision,
            "confidence": confidence,
            "reasoning": reasoning,
        }
        return {"status": "decision_submitted"}

    tools = [
        get_word_context,
        get_merchant_metadata,
        search_similar_words,
        get_all_labels_for_word,
        get_labels_on_receipt,
        submit_decision,
    ]

    return tools, state
```

## Benefits of Agent Approach

1. **Structured Reasoning**: Multi-step process, not single LLM call
2. **Full Context**: Agent can gather all context it needs
3. **Intelligent Decisions**: Can reason through conflicting signals
4. **Confidence Scores**: Returns confidence, not just binary decision
5. **Extensible**: Easy to add new tools or reasoning steps
6. **Traceable**: LangSmith traces show full reasoning process

## Comparison with Current Validation

| Aspect | Current Validation | Agent Approach |
|--------|-------------------|----------------|
| Context Gathering | Single ChromaDB query | Agent gathers all context it needs |
| Reasoning | Single LLM call | Multi-step reasoning |
| Edge Cases | Checked first, then fails | Used as supporting evidence, not definitive |
| Word Context | Missing | Included as primary context |
| Merchant Metadata | Not used | Used as supporting evidence |
| Confidence | Binary (valid/invalid) | Confidence score (0-100) |
| Decision Quality | Conservative, biased toward rejection | Balanced, context-aware, accuracy-focused |

## Implementation Plan

### Phase 1: Core Agent Structure
1. Create agent state class
2. Create tool implementations
3. Build basic graph structure

### Phase 2: Tool Integration
1. Integrate ChromaDB search
2. Integrate DynamoDB queries
3. Integrate Google Places metadata

### Phase 3: LLM Reasoning
1. Design system prompt
2. Implement structured output
3. Add confidence scoring

### Phase 4: Integration
1. Replace `_validate_suggestion_with_similarity` with agent
2. Update `apply_fixes_from_results` to use confidence scores
3. Test and monitor

### Phase 5: Optimization
1. Add caching for repeated queries
2. Optimize tool call patterns
3. Fine-tune prompts based on results

## Success Metrics

- **Validation Success Rate**: % of suggestions that get validated (should increase)
- **NEEDS_REVIEW Rate**: Should decrease (more confident decisions)
- **False Positive Rate**: Should stay low (valid labels marked as INVALID)
- **False Negative Rate**: Should decrease (invalid labels marked as VALID)
- **Confidence Distribution**: Should see more high-confidence decisions

## Testing Strategy

1. **Unit Tests**: Test each tool independently
2. **Integration Tests**: Test agent with real data
3. **A/B Testing**: Compare agent vs current validation
4. **LangSmith Analysis**: Review traces to improve prompts

## Summary

### Key Design Decisions

1. **Follow Existing Pattern**: Use the same structure as `agentic_workflow.py` and `agentic.py` for consistency
2. **ReAct Agent**: LLM autonomously decides which tools to call, in what order
3. **State Holder Pattern**: Use `state_holder` dict to inject runtime context (WordContext)
4. **Tool-Based Architecture**: Each context source is a separate tool the agent can call
5. **Structured Decision**: Agent submits decision via `submit_decision` tool with confidence score

### Advantages Over Current Validation

| Aspect | Current Validation | Agent Approach |
|--------|-------------------|----------------|
| **Context Gathering** | Single ChromaDB query, missing word context | Agent can gather all context it needs |
| **Reasoning** | Single LLM call, fixed prompt | Multi-step reasoning, agent decides what to check |
| **Edge Cases** | Checked first, then fails | Checked first, early exit if found |
| **Word Context** | Missing | Included as primary context via tool |
| **Merchant Metadata** | Not used | Available via tool |
| **Label History** | Not used | Available via tool for audit trail |
| **Confidence** | Binary (valid/invalid) | Confidence score (0-100) |
| **Technical Failures** | Cause validation to fail | Agent can work around (e.g., no ChromaDB = use other tools) |
| **Decision Quality** | Conservative, biased toward rejection | Balanced, context-aware, agent can reason through conflicts |

### Next Steps

1. **Review Design**: Confirm this approach aligns with requirements
2. **Implement Tools**: Create `label_validation_tools.py` with all 7 tools
3. **Implement Workflow**: Create `label_validation_workflow.py` with graph builder
4. **Create System Prompt**: Design comprehensive prompt that guides agent decision-making
5. **Integration**: Replace `_validate_suggestion_with_similarity` with agent call
6. **Testing**: Test with real data, compare results
7. **Monitoring**: Add metrics for agent decisions, tool usage, confidence distribution


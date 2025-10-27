# Next Steps: Implementing LangGraph Best Practices

## Current Status: 75% Compliant ✅⚠️

We have the basics right, but we're missing the graph-level resilience patterns.

---

## What We Need to Add

### 1. Graph-Level Error Handler Node

**Purpose**: Handle errors that propagate up from retried nodes

```python
async def graph_error_handler(state: CurrencyAnalysisState):
    """Handle errors at graph level when node retries fail."""
    return {
        **state,
        "currency_labels": [],  # Empty on error
        "line_item_labels": [],
        "confidence_score": 0.0,
        "error": state.get("error", "Unknown error"),
        "partial_results": True,
    }
```

### 2. Conditional Edge for Error Routing

**Purpose**: Route to error handler when failures occur

```python
def should_handle_error(state: CurrencyAnalysisState) -> str:
    """Determine if we should handle error or continue."""
    error_count = state.get("error_count", 0)
    
    if error_count > 0 or state.get("currency_labels") is None:
        return "error_handler"
    
    return "continue"

# Add to graph
workflow.add_conditional_edges(
    "phase1_currency",
    should_handle_error,
    {
        "continue": "combine_results",
        "error_handler": "graph_error_handler"
    }
)
```

### 3. Graceful Degradation (Optional)

**Purpose**: Try simpler model if expensive model fails

```python
async def phase1_with_graceful_degradation(state, ollama_api_key):
    """Try expensive model first, fallback to simpler model."""
    try:
        # Try with expensive model (120b)
        return await phase1_currency_analysis(state, ollama_api_key)
    except Exception as e:
        logger.warning(f"Expensive model failed: {e}, trying simpler model")
        
        # Fallback to simpler model (20b)
        return await phase1_with_simple_model(state, ollama_api_key)
```

---

## Implementation Plan

### Step 1: Add Error State Tracking

Update `CurrencyAnalysisState` to include:
```python
class CurrencyAnalysisState(BaseModel):
    # ... existing fields ...
    
    error_count: int = 0
    last_error: Optional[str] = None
    partial_results: bool = False
```

### Step 2: Modify Nodes to Track Errors

```python
async def phase1_with_error_tracking(state, ollama_api_key):
    """Phase 1 with error tracking."""
    try:
        return await phase1_currency_analysis(state, ollama_api_key)
    except Exception as e:
        return {
            "currency_labels": [],
            "error_count": state.get("error_count", 0) + 1,
            "last_error": str(e),
            "partial_results": True,
        }
```

### Step 3: Add Error Handler to Graph

```python
workflow.add_node("error_handler", graph_error_handler)

# Route from phase1 to either error handler or combine
workflow.add_conditional_edges(
    "phase1_currency",
    should_handle_error,
    {
        "error_handler": "error_handler",
        "continue": "combine_results"
    }
)

# From error handler, still go to combine
workflow.add_edge("error_handler", "combine_results")
```

---

## Benefits

### Current (Without Graph-Level Handling)
- ❌ Errors cause complete failure
- ❌ No graceful degradation
- ❌ User sees errors

### With Graph-Level Handling
- ✅ Errors handled gracefully
- ✅ Partial results returned
- ✅ Better user experience
- ✅ System keeps running

---

## Priority

**High**: Graph-level error handling should be added before production deployment.  
**Medium**: Graceful degradation is nice-to-have but not critical.  
**Low**: Enhanced monitoring can be added incrementally.

---

## Estimated Time

- Graph-level error handler: 2 hours
- Conditional edges: 1 hour
- Testing: 1 hour
- **Total: 4 hours**

Would you like me to implement the graph-level error handling now?


# LangSmith Trace Nesting Debug Summary

## What the Documentation Says

According to LangSmith documentation (https://docs.langchain.com/langsmith/nest-traces), for async functions with `@traceable`:

> "Create an async trace (or decorate async functions with @traceable). On Python 3.11+ the SDK automatically propagates the parent run across awaits, so every nested @traceable call appears under the same trace. On older Python versions you must manually pass the parent run—either add a `run_tree: ls.RunTree` argument to the child functions and call them with `langsmith_extra={"parent": run_tree}` **or wrap the child body with `with ls.tracing_context(parent=run_tree):`**."

### Two Approaches from Docs:

**Approach 1: Use `langsmith_extra` when calling**
```python
@traceable
async def child_function(inputs: str, run_tree: ls.RunTree):
    # Function body
    pass

# When calling:
await child_function(inputs, langsmith_extra={"parent": parent_run_tree})
```

**Approach 2: Wrap function body with `tracing_context`**
```python
@traceable
async def child_function(inputs: str, run_tree: ls.RunTree):
    with ls.tracing_context(parent=run_tree):
        # Entire function body here
        pass
```

## What We've Tried

### Attempt 1: Passing Parent Config (LangChain Runnables Approach)
- **What we did**: Stored parent `RunnableConfig` and passed it to child LangChain calls
- **Why it failed**: This approach is for LangChain runnables calling other LangChain runnables, not for `@traceable` decorated functions
- **Result**: Traces still appeared as separate root traces

### Attempt 2: Using `tracing_context` Only Around LangChain Calls
- **What we did**:
  - Decorated both functions with `@traceable`
  - Wrapped only the LangChain `ainvoke` calls with `tracing_context(parent=run_tree)`
- **Why it failed**: The `@traceable` decorator creates its own trace for the function, and wrapping only the LangChain call doesn't link the function trace itself to the parent
- **Result**: Traces still appeared as separate root traces

### Attempt 3: Wrapping Entire Function Body with `tracing_context` (Current)
- **What we did**:
  - Decorated both functions with `@traceable`
  - In `_llm_determine_outlier`: Store `run_tree` (from `@traceable`) after LangChain call
  - In `_suggest_label_type_for_outlier`: Receive `run_tree` as parameter and wrap entire function body with `tracing_context(parent=run_tree)`
  - Split into wrapper function and implementation function
- **Current code structure**:
  ```python
  @traceable
  async def _llm_determine_outlier(..., run_tree: Optional[Any] = None):
      # ... setup code ...
      if run_tree and ls:
          with ls.tracing_context(parent=run_tree):
              structured_response = await llm_structured.ainvoke(...)
      # Store run_tree for later
      if run_tree:
          group.outlier_run_trees[word_key] = run_tree

  @traceable
  async def _suggest_label_type_for_outlier(..., run_tree: Optional[Any] = None):
      if run_tree and ls:
          with ls.tracing_context(parent=run_tree):
              return await self._suggest_label_type_for_outlier_body(...)
      else:
          return await self._suggest_label_type_for_outlier_body(...)
  ```
- **Why it might still fail**:
  - The `@traceable` decorator creates a trace when the function is called
  - If we wrap the body with `tracing_context`, it might create a nested context but the `@traceable` trace itself might still be created at the top level
  - The timing of when `run_tree` is available vs when `@traceable` creates the trace might be the issue

## The Core Problem

The issue is that `@traceable` creates a trace **when the function is called**, but we're trying to link it to a parent **after** the parent function has completed. The sequence is:

1. `_llm_determine_outlier` is called → `@traceable` creates trace A
2. Inside `_llm_determine_outlier`, LangChain call happens (wrapped in `tracing_context`)
3. `_llm_determine_outlier` stores its `run_tree` (trace A's run tree)
4. `_llm_determine_outlier` completes
5. Later, `_suggest_label_type_for_outlier` is called → `@traceable` creates trace B (separate, no parent)
6. Inside `_suggest_label_type_for_outlier`, we wrap the body with `tracing_context(parent=trace_A)`
7. But trace B was already created by `@traceable` before we enter the `tracing_context` block

## What the Docs Actually Show

Looking at the documentation examples more carefully, the pattern seems to be:

```python
@traceable
async def parent_function(inputs: str):
    run_tree = get_current_run_tree()  # Get THIS function's run tree
    await child_function(inputs, run_tree=run_tree)  # Pass it to child

@traceable
async def child_function(inputs: str, run_tree: ls.RunTree):
    with ls.tracing_context(parent=run_tree):
        # All child logic here
        pass
```

**Key difference**: The parent gets its own `run_tree` using `get_current_run_tree()` and passes it to the child, which then wraps its entire body.

## Potential Issues with Our Implementation

1. **Timing Issue**: We're storing `run_tree` from `_llm_determine_outlier` AFTER the LangChain call, but the `@traceable` decorator might have already created the trace. We need to get the run_tree at the START of the function.

2. **Context Issue**: When we call `_suggest_label_type_for_outlier` from `apply_fixes`, we're not in the context of `_llm_determine_outlier` anymore. The async context has changed.

3. **Decorator Behavior**: The `@traceable` decorator might be creating the trace before we can set the parent, making it impossible to link them after the fact.

## What We Should Try Next

1. **Get run_tree at the START of `_llm_determine_outlier`**:
   ```python
   @traceable
   async def _llm_determine_outlier(..., run_tree: Optional[Any] = None):
       # Get run_tree at the START, before any async operations
       if not run_tree and ls:
           from langsmith.run_helpers import get_current_run_tree
           run_tree = get_current_run_tree()

       # Store it immediately
       if run_tree:
           word_key = f"{word.image_id}:{word.receipt_id}:{word.line_id}:{word.word_id}"
           group.outlier_run_trees[word_key] = run_tree

       # Then do the LangChain call wrapped in tracing_context
       if run_tree and ls:
           with ls.tracing_context(parent=run_tree):
               structured_response = await llm_structured.ainvoke(...)
   ```

2. **Use `langsmith_extra` approach instead**:
   ```python
   # When calling:
   await self._suggest_label_type_for_outlier(
       record, group,
       langsmith_extra={"parent": parent_run_tree}
   )
   ```

3. **Check if we need to pass run_tree differently**: The `@traceable` decorator might need the parent to be set BEFORE the function is called, not inside it.

## References

- Main docs: https://docs.langchain.com/langsmith/nest-traces
- Custom instrumentation: https://docs.langchain.com/langsmith/annotate-code
- LangSmith Cookbook: https://github.com/langchain-ai/langsmith-cookbook/blob/main/tracing-examples/nesting-tools/nest_runs_within_tools.ipynb


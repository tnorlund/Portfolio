# LangSmith Trace Nesting for Label Harmonizer

## Overview

This document describes the implementation of proper trace nesting in LangSmith for the label harmonizer, specifically ensuring that `suggest_label_type_for_outlier` traces appear as child traces under their parent `_llm_determine_outlier` traces in the LangSmith UI.

## Problem Statement

When the label harmonizer detects an outlier (a word that doesn't belong in a label group), it:
1. Calls `_llm_determine_outlier()` to confirm the word is an outlier
2. Calls `_suggest_label_type_for_outlier()` to suggest the correct label type

The issue was that the suggestion traces were appearing as separate top-level runs in LangSmith instead of being nested under the outlier detection runs, making it difficult to understand the relationship between outlier detection and label type suggestions.

## Solution Approach

According to LangSmith documentation, there are two main approaches for trace nesting:

1. **For LangChain Runnables**: Pass the parent `RunnableConfig` to child calls. LangChain automatically uses the run tree from the parent config to link child traces.

2. **For Direct LangSmith Usage** (with `@traceable` decorator): Use `langsmith.tracing_context(parent=run_tree)` to manually link traces.

Since we're using LangChain runnables (`ChatOllama` with `with_structured_output`), we use the first approach: **passing the parent config to child calls**.

## Implementation Details

### 1. Data Structure Changes

**`MerchantLabelGroup` class** was updated to store parent configs:

```python
@dataclass
class MerchantLabelGroup:
    # ... existing fields ...
    outlier_configs: dict[str, Any] = field(default_factory=dict)  # word_id -> parent config for child calls
```

This dictionary maps word identifiers to their parent configs, allowing us to retrieve the parent config when making the child call.

### 2. Capturing Parent Config During Outlier Detection

In `_llm_determine_outlier()`, after the LLM call completes, we store the config used for that call:

```python
llm_structured = llm_with_schema.with_structured_output(OutlierDecision)
structured_response: OutlierDecision = await llm_structured.ainvoke(messages, config=config)

# Store the config for this outlier detection call
# According to LangSmith docs, for LangChain runnables, we should pass the parent config
# to child calls to ensure proper trace nesting
word_key = f"{word.image_id}:{word.receipt_id}:{word.line_id}:{word.word_id}"
group.outlier_configs[word_key] = config
```

The `config` object contains the run tree information that LangChain uses to link traces. By storing it, we can pass it to the child call later.

### 3. Using Parent Config in Child Call

In `_suggest_label_type_for_outlier()`, we retrieve the stored parent config and merge it with child-specific metadata:

```python
async def _suggest_label_type_for_outlier(
    self,
    word: LabelRecord,
    group: MerchantLabelGroup,
    parent_config: Optional[Any] = None,  # Changed from parent_run_tree
) -> Optional[str]:
    # ... setup code ...

    if parent_config:
        # Copy parent config and update with child-specific metadata
        # Pydantic models have model_dump() method for this
        parent_dict = parent_config.model_dump() if hasattr(parent_config, 'model_dump') else dict(parent_config)
        # Merge child metadata/tags with parent
        child_metadata = {**(parent_dict.get("metadata") or {}), **config_dict.get("metadata", {})}
        child_tags = list(set((parent_dict.get("tags") or []) + config_dict.get("tags", [])))
        child_config = RunnableConfig(
            run_name=config_dict.get("run_name", "suggest_label_type_for_outlier"),
            metadata=child_metadata,
            tags=child_tags,
            # Preserve all other parent config fields (callbacks, etc.)
            **{k: v for k, v in parent_dict.items() if k not in ["run_name", "metadata", "tags"]}
        )
    else:
        child_config = RunnableConfig(**config_dict)

    structured_response: LabelTypeSuggestion = await llm_structured.ainvoke(messages, config=child_config)
```

The key points:
- We merge the parent config's metadata and tags with child-specific ones
- We preserve all other parent config fields (especially callbacks and run tree information)
- LangChain automatically uses the parent run tree from the config to link the traces

### 4. Retrieving Parent Config in `apply_fixes()`

When applying fixes and detecting outliers, we retrieve the stored parent config:

```python
is_outlier = record in group.outliers
if is_outlier:
    # Get the stored config from the outlier detection call
    # According to LangSmith docs, for LangChain runnables, we should pass the parent config
    # to child calls to ensure proper trace nesting
    word_key = f"{record.image_id}:{record.receipt_id}:{record.line_id}:{record.word_id}"
    parent_config = group.outlier_configs.get(word_key)
    if not parent_config:
        logger.debug(
            "No parent config found for outlier '%s' - child trace may not be linked",
            record.word_text,
        )
    suggested_label_type = await self._suggest_label_type_for_outlier(record, group, parent_config=parent_config)
```

## Why This Approach?

### LangChain Runnables vs Direct LangSmith Usage

The LangSmith documentation distinguishes between two scenarios:

1. **LangChain Runnables** (our case):
   - We're using `ChatOllama` with `with_structured_output()`, which are LangChain runnables
   - The recommended approach is to pass the parent `RunnableConfig` to child calls
   - LangChain automatically extracts the run tree from the config and links traces

2. **Direct LangSmith Usage** (with `@traceable`):
   - For functions decorated with `@traceable`, you use `langsmith.tracing_context(parent=run_tree)`
   - This is a different API for manual instrumentation

### Why Not `tracing_context`?

We initially tried using `langsmith.tracing_context(parent=run_tree)`, but this is designed for direct LangSmith usage, not LangChain runnables. When using LangChain runnables, the proper approach is to pass the parent config, which LangChain uses internally to maintain trace hierarchy.

### Why Store Config Instead of Run Tree?

- The `RunnableConfig` object contains all the information LangChain needs to link traces
- It's the standard way LangChain propagates trace context
- We can't reliably get the run tree after an async call completes due to context variable limitations in Python < 3.11
- Storing the config is simpler and follows LangChain's design patterns

## Verification

To verify that trace nesting is working correctly:

1. **Run the harmonizer** with a project name:
   ```bash
   ./scripts/trigger_harmonizer_dry_run.sh --project label-harmonizer-trace-test
   ```

2. **Check LangSmith UI**:
   - Navigate to the project in LangSmith
   - Find an outlier detection trace (`_llm_determine_outlier`)
   - Verify that the `suggest_label_type_for_outlier` trace appears as a child node under it

3. **Query traces programmatically**:
   ```python
   from langsmith import Client

   client = Client()
   run = client.read_run(run_id="<outlier-detection-run-id>")
   # Check if child runs exist
   child_runs = client.list_runs(filter=f'parent_run_id == "<outlier-detection-run-id>"')
   ```

## Troubleshooting

### Traces Still Appear as Separate Root Traces

If traces are still appearing as separate root traces:

1. **Check Python version**: Python 3.11+ has better async context propagation. If using Python < 3.11, manual context propagation (which we're doing) is required.

2. **Verify config is being stored**: Add logging to confirm `group.outlier_configs` contains entries after outlier detection.

3. **Check config merging**: Verify that the parent config fields are being preserved in the child config, especially callbacks.

4. **Review LangSmith project settings**: Ensure the project is correctly configured and traces are being sent to the right project.

### Alternative Approach: Using `@traceable` Decorator

If the config-based approach doesn't work, we could switch to using LangSmith's `@traceable` decorator:

```python
from langsmith import traceable

@traceable
async def _suggest_label_type_for_outlier(self, word, group, run_tree):
    with langsmith.tracing_context(parent=run_tree):
        # Make LLM call
        pass
```

However, this would require:
- Capturing the run tree during the parent call (using callbacks)
- Switching from LangChain runnables to direct LangSmith instrumentation
- More complex implementation

## References

- [LangSmith Nest Traces Documentation](https://docs.langchain.com/langsmith/nest-traces)
- [LangChain RunnableConfig Documentation](https://python.langchain.com/docs/modules/model_io/runnables/)
- [LangSmith Custom Instrumentation](https://docs.langchain.com/langsmith/annotate-code)

## Related Files

- `/receipt_agent/receipt_agent/tools/label_harmonizer.py`: Main implementation
- `/scripts/trigger_harmonizer_dry_run.sh`: Script to trigger harmonizer with custom project name
- `/scripts/analyze_langsmith_edge_cases.py`: Script to analyze LangSmith traces


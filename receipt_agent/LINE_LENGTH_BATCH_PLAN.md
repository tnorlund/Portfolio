# Line Length Cleanup Plan

We still have ~1,045 `C0301` long-line warnings. To finish manually, tackle them in four batches while keeping related modules together.

## Batch 1 — Financial Validation
- `receipt_agent/subagents/financial_validation/llm_driven_graph.py`
- `receipt_agent/subagents/financial_validation/enhanced_graph.py`
- `receipt_agent/subagents/financial_validation/graph.py`

## Batch 2 — Place Finder & Text Consistency
- `receipt_agent/subagents/place_finder/graph.py`
- `receipt_agent/subagents/place_finder/tools/receipt_place_finder.py`
- `receipt_agent/subagents/place_finder/__init__.py`
- `receipt_agent/subagents/place_finder/state.py`
- `receipt_agent/subagents/cove_text_consistency/graph.py`

## Batch 3 — Harmonizer
- `receipt_agent/agents/harmonizer/graph.py`
- `receipt_agent/agents/harmonizer/tools/harmonizer_v3.py`

## Batch 4 — Label Suggestion & Remaining Singles
- `receipt_agent/agents/label_suggestion/graph.py`
- `receipt_agent/agents/label_suggestion/tools/label_suggestion_tools.py`
- `receipt_agent/agents/label_suggestion/tools/__init__.py`
- Remaining stragglers: `clients/factory.py`, `tools/chroma.py`, `tracing/callbacks.py`, `state/models.py`, `tools/on_the_fly_embedding_tools.py`, and any other one-offs that still flag C0301.

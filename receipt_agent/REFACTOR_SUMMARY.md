# Receipt Agent Refactor Summary

The receipt agent refactor is complete. Active code now lives in modular
`agents/*` and `subagents/*` packages, with the historical bulk metadata
harmonizer removed from the runtime and import surface.

## Active Structure

```text
receipt_agent/
├── agents/
│   ├── agentic/
│   ├── label_evaluator/
│   ├── place_id_finder/
│   ├── question_answering/
│   ├── receipt_grouping/
│   └── validation/
└── subagents/
    ├── financial_validation/
    ├── place_finder/
    └── table_columns/
```

## Cleanup Completed

- Deprecated `graph/*_workflow.py` shims were removed in an earlier cleanup.
- Legacy label harmonizer and label suggestion agents were retired in favor of
  the current label evaluator and LayoutLM/ChromaDB flows.
- The bulk metadata harmonizer Step Function, harmonizer agent package, CoVe
  text-consistency subagent, and LangSmith harmonizer schemas are retired.

## Current Validation Model

The active system validates receipts by combining deterministic checks and
targeted correction tools:

- `place_id_finder` fills missing Google Place IDs.
- fix-place workflows correct existing `ReceiptPlace` records with receipt text,
  Google Places, and ChromaDB context.
- `label_evaluator` validates label quality, within-receipt consistency, and
  financial math.
- MCP tools and scheduled maintenance agents apply explicit corrections outside
  the hot path.

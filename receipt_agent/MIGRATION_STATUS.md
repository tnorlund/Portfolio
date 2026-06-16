# Receipt Agent Status

This package now keeps active workflows under `receipt_agent.agents` and
`receipt_agent.subagents`. Historical refactor plans and the bulk metadata
harmonizer have been removed from the active surface.

## Active Agents

- `agentic/` - Agentic validation workflow for cases that need tool-driven
  exploration.
- `validation/` - Deterministic validation workflow that prefers known ChromaDB
  matches before falling back to agentic work.
- `label_evaluator/` - Label quality checks, financial math validation, and
  related receipt health signals.
- `place_id_finder/` - Finds missing Google Place IDs for receipt place data.
- `question_answering/` - Receipt QA graph.
- `receipt_grouping/` - Combines and splits receipt images into receipt groups.

## Active Subagents

- `financial_validation/` - Financial consistency checks used by the label
  evaluator.
- `place_finder/` - Place data fill-in and Google Places verification used by
  fix-place workflows.
- `table_columns/` - Placeholder table column helper.

## Retired Components

- `agents/harmonizer/` and `subagents/cove_text_consistency/` were removed with
  the bulk metadata harmonizer. Existing place corrections now run through the
  fix-place workflow and receipt MCP tools.
- Legacy `graph/*_workflow.py` shims remain retired. Code should import from the
  active `agents/*` and `subagents/*` packages.
- Legacy label harmonizer and label suggestion agents remain retired; label
  quality work is handled by the current label evaluator and supporting
  pipelines.

## Current Direction

Receipt cleanup is moving toward a single receipt health flow:

1. Validate merchant/place data against Google Places and stored receipt text.
2. Validate label quality, within-receipt consistency, and financial math.
3. Write one cache that the frontend can render as a unified health view.
4. Use MCP tools and scheduled maintenance agents for corrective edits.

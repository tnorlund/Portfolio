# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2025-12-30

### Removed
- **BREAKING**: Removed deprecated label agents (replaced by unified labeling pipeline):
  - `agents/label_harmonizer/` - functionality absorbed by `label_evaluator`
  - `agents/label_suggestion/` - replaced by LayoutLM + ChromaDB pipeline
  - `agents/label_validation/` - replaced by unified validation in new pipeline
  - `infra/label_harmonizer_step_functions/`
  - `infra/label_suggestion_step_functions/`
  - `infra/label_validation_agent_step_functions/`

### Changed
- Consolidated label processing to use:
  - LayoutLM for initial predictions
  - ChromaDB for validation/refinement
  - Google Places for metadata validation
  - `label_evaluator` for financial math validation

### Migration Notes
- The old multi-stage label pipeline (suggest → validate → harmonize) is replaced by a unified pipeline during upload.
  See `docs/UNIFIED_LABELING_PIPELINE.md` for detailed architecture and integration guidance.
- Use `label_evaluator` for financial validation during the pipeline and post-hoc label quality checks

## [0.2.0] - 2025-12-11

### Removed
- **BREAKING**: Removed all deprecated `receipt_agent.graph.*_workflow` shim modules
  - `graph/agentic_workflow.py`
  - `graph/harmonizer_workflow.py`
  - `graph/label_harmonizer_workflow.py`
  - `graph/label_validation_workflow.py`
  - `graph/label_suggestion_workflow.py`
  - `graph/place_id_finder_workflow.py`
  - `graph/receipt_grouping_workflow.py`
  - `graph/receipt_metadata_finder_workflow.py`
  - `graph/cove_text_consistency_workflow.py`
  - `graph/financial_validation_workflow.py`
  - `graph/workflow.py`

### Changed
- Updated `graph/__init__.py` to reflect cleanup - only `nodes.py` remains in `graph/` directory
- All code now uses new `receipt_agent.agents.*` import paths

### Migration Notes
- If you were using deprecated `receipt_agent.graph.*_workflow` imports, update to:
  - `receipt_agent.agents.harmonizer` (was `graph.harmonizer_workflow`)
  - `receipt_agent.agents.label_evaluator` (was `graph.label_harmonizer_workflow`) - **Note: label_harmonizer removed in v0.3.0**
  - `receipt_agent.agents.label_validation` (was `graph.label_validation_workflow`)
  - `receipt_agent.agents.label_suggestion` (was `graph.label_suggestion_workflow`)
  - `receipt_agent.agents.place_id_finder` (was `graph.place_id_finder_workflow`)
  - `receipt_agent.agents.receipt_grouping` (was `graph.receipt_grouping_workflow`)
  - `receipt_agent.agents.agentic` (was `graph.agentic_workflow`)
  - `receipt_agent.agents.validation` (was `graph.workflow`)
  - `receipt_agent.subagents.place_finder` (was `graph.receipt_metadata_finder_workflow`)
  - `receipt_agent.subagents.cove_text_consistency` (was `graph.cove_text_consistency_workflow`)
  - `receipt_agent.subagents.financial_validation` (was `graph.financial_validation_workflow`)

## [0.1.0] - Initial Release

- Initial release with agentic validation workflows
- LangGraph-based orchestration
- ChromaDB integration for similarity search
- Google Places API verification
- LangSmith tracing support

# Metadata Agents Branch Strategy

## Current Situation

We're on a new branch `metadata-agents-only` created from `clean-langchain-agents`.

## Goal

Create a clean branch that contains **only** metadata agent work, separated from:
- Label agents (label_validation, label_suggestion)
- Receipt grouping agents
- Other validation agents

## Strategy Options

### Option 1: Cherry-pick Metadata-Related Commits

Identify commits that are metadata-agent specific and cherry-pick them:

**Metadata-agent related commits** (from recent history):
- `6be92b48b` - Extract shared utilities (used by metadata agents)
- `de11dffbc` - Extract shared utilities for agent workflows
- `6e60957ad` - Improve retry logic for sub-agents (CoVe and metadata finder)
- `6ae954a8a` - Add address sanitization (used by harmonizer)
- `3fa5d5a3e` - Improve metadata finder validation
- `3e7d57c05` - Pass chromadb_bucket to metadata finder
- `fcc95b58f` - Add efficient place_id-based receipt loading to harmonizer
- `22f9f99c2` - Add ChromaDB lazy loading (used by metadata agents)
- `48a1f3eac` - Add retry logic to metadata finder and CoVe

**Not metadata-agent related**:
- `2ae9b6314` - LangSmith traces script (dev tool)
- `b5f8144ec` - Canonical fields deprecation (documentation)
- `41b74b50a` - CloudWatch alarms (infrastructure)

### Option 2: Reset to Main and Manually Add Files

1. Reset branch to main
2. Manually add only metadata agent files:
   - `graph/harmonizer_workflow.py`
   - `graph/receipt_metadata_finder_workflow.py`
   - `graph/cove_text_consistency_workflow.py`
   - `graph/place_id_finder_workflow.py`
   - `tools/harmonizer_v3.py`
   - `tools/receipt_metadata_finder.py`
   - `tools/place_id_finder.py`
   - `tools/agentic.py` (shared)
   - `utils/agent_common.py` (shared)
   - `utils/receipt_fetching.py` (shared)
   - `utils/address_validation.py` (shared)
   - Documentation files

### Option 3: Interactive Rebase (Recommended)

Use interactive rebase to keep only metadata-agent commits:

```bash
# Find the base commit (before agent work started)
git log --oneline --all | grep -i "agent\|metadata\|harmonizer" | tail -20

# Interactive rebase from that point
git rebase -i <base-commit>
# Mark commits to keep/drop/edit
```

## Recommended Approach

**Use Option 2** (Reset to main and manually add files) because:
1. Cleanest separation
2. No commit history confusion
3. Can create clean, focused commits
4. Easy to review what's included

## Implementation Steps

```bash
# 1. Reset to main
git reset --hard origin/main

# 2. Create a script to copy metadata agent files
# (or manually add them)

# 3. Commit in logical groups:
#    - Shared utilities first
#    - Metadata agent workflows
#    - Metadata agent tools
#    - Documentation
```

## Files to Include

### Core Metadata Agents
- `receipt_agent/receipt_agent/graph/harmonizer_workflow.py`
- `receipt_agent/receipt_agent/graph/receipt_metadata_finder_workflow.py`
- `receipt_agent/receipt_agent/graph/cove_text_consistency_workflow.py`
- `receipt_agent/receipt_agent/graph/place_id_finder_workflow.py`

### Metadata Agent Tools
- `receipt_agent/receipt_agent/tools/harmonizer_v3.py`
- `receipt_agent/receipt_agent/tools/receipt_metadata_finder.py`
- `receipt_agent/receipt_agent/tools/place_id_finder.py`

### Shared Tools (used by metadata agents)
- `receipt_agent/receipt_agent/tools/agentic.py`

### Shared Utilities
- `receipt_agent/receipt_agent/utils/agent_common.py`
- `receipt_agent/receipt_agent/utils/receipt_fetching.py`
- `receipt_agent/receipt_agent/utils/address_validation.py`

### Documentation
- `docs/METADATA_AGENTS_DIRECTORY.md`
- `docs/METADATA_AGENTS_REVIEW.md`
- `docs/METADATA_AGENTS_EVOLUTION.md`
- `docs/AGENT_REFACTORING_PLAN.md`
- `docs/REFACTORING_SUMMARY.md`
- `docs/METADATA_AGENTS_SPLIT_PROPOSAL.md`

### Infrastructure (if needed)
- `infra/metadata_harmonizer_step_functions/` (Lambda handler that uses harmonizer)

## Files to Exclude

- `graph/label_validation_workflow.py`
- `graph/label_suggestion_workflow.py`
- `graph/receipt_grouping_workflow.py`
- `graph/agentic_workflow.py`
- `graph/workflow.py` (legacy)
- `tools/label_validation_tools.py`
- `tools/label_suggestion_tools.py`
- `tools/receipt_grouper.py`

## Next Steps

1. Decide on approach (Option 2 recommended)
2. Reset branch to main
3. Copy/add metadata agent files
4. Create clean commits
5. Push branch for review

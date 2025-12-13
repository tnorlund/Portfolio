#!/bin/bash
# Script to stage only metadata agent files for commit
# Usage: ./scripts/stage_metadata_agents.sh

set -e

echo "Staging metadata agent files..."

# Metadata agent workflows
git add receipt_agent/receipt_agent/graph/harmonizer_workflow.py
git add receipt_agent/receipt_agent/graph/receipt_metadata_finder_workflow.py
git add receipt_agent/receipt_agent/graph/cove_text_consistency_workflow.py
git add receipt_agent/receipt_agent/graph/place_id_finder_workflow.py

# Metadata agent tools
git add receipt_agent/receipt_agent/tools/harmonizer_v3.py
git add receipt_agent/receipt_agent/tools/receipt_metadata_finder.py
git add receipt_agent/receipt_agent/tools/place_id_finder.py

# Shared tools (used by metadata agents)
git add receipt_agent/receipt_agent/tools/agentic.py

# Shared utilities (used by metadata agents)
git add receipt_agent/receipt_agent/utils/agent_common.py
git add receipt_agent/receipt_agent/utils/receipt_fetching.py
git add receipt_agent/receipt_agent/utils/address_validation.py

# Documentation
git add docs/METADATA_AGENTS_DIRECTORY.md
git add docs/METADATA_AGENTS_REVIEW.md
git add docs/METADATA_AGENTS_EVOLUTION.md
git add docs/AGENT_REFACTORING_PLAN.md
git add docs/REFACTORING_SUMMARY.md

echo ""
echo "✅ Staged metadata agent files"
echo ""
echo "Review with: git status"
echo "Commit with: git commit -m 'feat: Metadata agents - Harmonizer, Metadata Finder, and CoVe'"

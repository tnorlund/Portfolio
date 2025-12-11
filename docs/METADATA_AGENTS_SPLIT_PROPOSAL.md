# Proposal: Splitting Metadata Agents from Other Agents

## Current Situation

The `receipt_agent` package contains multiple types of agents:
- **Metadata Agents**: Harmonizer, Metadata Finder, CoVe, Place ID Finder
- **Label Agents**: Label Validation, Label Suggestion
- **Other Agents**: Receipt Grouping, Agentic Validation, Legacy Validation

All agents are currently in the same directories (`graph/` and `tools/`), making it hard to commit metadata agent work separately.

## Option 1: Directory Reorganization (Recommended for Long-term)

Create subdirectories to separate concerns:

```
receipt_agent/receipt_agent/
├── graph/
│   ├── metadata/                    # Metadata agents
│   │   ├── __init__.py
│   │   ├── harmonizer_workflow.py
│   │   ├── receipt_metadata_finder_workflow.py
│   │   ├── cove_text_consistency_workflow.py
│   │   └── place_id_finder_workflow.py
│   ├── labeling/                    # Label-related agents
│   │   ├── __init__.py
│   │   ├── label_validation_workflow.py
│   │   └── label_suggestion_workflow.py
│   ├── validation/                  # Validation agents
│   │   ├── __init__.py
│   │   ├── agentic_workflow.py
│   │   └── workflow.py
│   └── grouping/                   # Grouping agents
│       ├── __init__.py
│       └── receipt_grouping_workflow.py
├── tools/
│   ├── metadata/                   # Metadata agent tools
│   │   ├── __init__.py
│   │   ├── agentic.py              # Shared tools (used by metadata agents)
│   │   ├── harmonizer_v3.py
│   │   ├── receipt_metadata_finder.py
│   │   └── place_id_finder.py
│   ├── labeling/                   # Label agent tools
│   │   ├── __init__.py
│   │   ├── label_validation_tools.py
│   │   └── label_suggestion_tools.py
│   └── grouping/                  # Grouping agent tools
│       ├── __init__.py
│       └── receipt_grouper.py
└── utils/                          # Shared utilities (used by all)
    ├── agent_common.py
    ├── receipt_fetching.py
    └── address_validation.py
```

**Pros:**
- Clear separation of concerns
- Easy to see which files belong to which domain
- Can commit metadata agents independently
- Better organization for future development

**Cons:**
- Requires updating all imports across the codebase
- More disruptive change
- Need to update documentation

**Migration Steps:**
1. Create new subdirectories
2. Move files to appropriate subdirectories
3. Update all imports (can use find/replace)
4. Update `__init__.py` files to maintain backward compatibility
5. Test all imports still work

---

## Option 2: Commit Organization (Recommended for Short-term)

Keep current directory structure but organize commits logically:

### Commit 1: Metadata Agents
```
Files to include:
- receipt_agent/receipt_agent/graph/harmonizer_workflow.py
- receipt_agent/receipt_agent/graph/receipt_metadata_finder_workflow.py
- receipt_agent/receipt_agent/graph/cove_text_consistency_workflow.py
- receipt_agent/receipt_agent/graph/place_id_finder_workflow.py
- receipt_agent/receipt_agent/tools/harmonizer_v3.py
- receipt_agent/receipt_agent/tools/receipt_metadata_finder.py
- receipt_agent/receipt_agent/tools/place_id_finder.py
- receipt_agent/receipt_agent/tools/agentic.py (shared tools)
- receipt_agent/receipt_agent/utils/agent_common.py (shared)
- receipt_agent/receipt_agent/utils/receipt_fetching.py (shared)
- receipt_agent/receipt_agent/utils/address_validation.py (shared)
- docs/METADATA_AGENTS_*.md (all metadata agent docs)
```

### Commit 2: Other Agents (if needed later)
```
Files to include:
- receipt_agent/receipt_agent/graph/label_validation_workflow.py
- receipt_agent/receipt_agent/graph/label_suggestion_workflow.py
- receipt_agent/receipt_agent/graph/receipt_grouping_workflow.py
- receipt_agent/receipt_agent/graph/agentic_workflow.py
- receipt_agent/receipt_agent/graph/workflow.py
- receipt_agent/receipt_agent/tools/label_validation_tools.py
- receipt_agent/receipt_agent/tools/label_suggestion_tools.py
- receipt_agent/receipt_agent/tools/receipt_grouper.py
```

**Pros:**
- No code changes required
- Can commit immediately
- Minimal disruption
- Easy to revert if needed

**Cons:**
- Files still mixed in same directories
- Harder to see separation in file tree
- Future commits may mix concerns

---

## Option 3: Hybrid Approach (Recommended)

1. **Short-term**: Use Option 2 (commit organization) to get metadata agents to main quickly
2. **Long-term**: Plan Option 1 (directory reorganization) as a separate refactoring task

This allows you to:
- Commit metadata agent work now without disruption
- Plan the directory reorganization as a separate, well-tested change
- Keep the codebase stable while improving organization

---

## Recommendation

**For immediate commit**: Use **Option 2** (commit organization)

**For long-term**: Plan **Option 1** (directory reorganization) as a separate PR

This gives you:
1. ✅ Quick path to commit metadata agents
2. ✅ No risk of breaking existing code
3. ✅ Clear plan for future organization
4. ✅ Can test directory reorganization separately

---

## Implementation: Option 2 (Immediate)

To commit metadata agents separately:

```bash
# Stage only metadata agent files
git add receipt_agent/receipt_agent/graph/harmonizer_workflow.py
git add receipt_agent/receipt_agent/graph/receipt_metadata_finder_workflow.py
git add receipt_agent/receipt_agent/graph/cove_text_consistency_workflow.py
git add receipt_agent/receipt_agent/graph/place_id_finder_workflow.py
git add receipt_agent/receipt_agent/tools/harmonizer_v3.py
git add receipt_agent/receipt_agent/tools/receipt_metadata_finder.py
git add receipt_agent/receipt_agent/tools/place_id_finder.py
git add receipt_agent/receipt_agent/tools/agentic.py
git add receipt_agent/receipt_agent/utils/agent_common.py
git add receipt_agent/receipt_agent/utils/receipt_fetching.py
git add receipt_agent/receipt_agent/utils/address_validation.py
git add docs/METADATA_AGENTS_DIRECTORY.md
git add docs/METADATA_AGENTS_REVIEW.md
git add docs/METADATA_AGENTS_EVOLUTION.md
git add docs/AGENT_REFACTORING_PLAN.md
git add docs/REFACTORING_SUMMARY.md

# Commit
git commit -m "feat: Metadata agents - Harmonizer, Metadata Finder, and CoVe

- Refactored shared utilities (agent_common, receipt_fetching, address_validation)
- Fixed ChromaDB collection loading (separate directories for lines/words)
- Improved error handling and retry logic
- Added comprehensive documentation

Metadata Agents:
- Harmonizer Agent: Ensures consistency across place_id groups
- Receipt Metadata Finder: Finds missing metadata for receipts
- CoVe Agent: Verifies text consistency
- Place ID Finder: Legacy agent for finding place_ids

See docs/METADATA_AGENTS_DIRECTORY.md for complete overview."
```

---

## Future: Option 1 (Directory Reorganization)

When ready, create a separate PR for directory reorganization:

1. Create subdirectories
2. Move files with git mv (preserves history)
3. Update imports
4. Add backward compatibility shims in old locations
5. Test thoroughly
6. Update documentation

This can be done incrementally without blocking metadata agent work.

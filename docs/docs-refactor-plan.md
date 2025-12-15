# Documentation audit plan

## Scope

- Built `docs-refactor` from `main` to isolate the exploration.
- Listed every top-level directory (`architecture`, `layoutlm`, `operations`, etc.) and captured their last commit dates to understand which areas are actively maintained versus historical.
- Focused on the 21 files whose last updates occurred before September 2025 (AI usage guides, early CI docs, the Phase-1 doc cleanup artifacts) and traced them back to their PRs so we could explain why each existed.
- Created `docs/archive/legacy-ai-usage/` to hold the obsolete AI testing/usage docs and removed the dummy `types/api/interfaces/BoundingBox.md` placeholder.

## Archived documents

| File | Last edit | Notes |
| --- | --- | --- |
| `docs/ai-usage-context-migration.md` | 2025-06-26 (PR #132) | Migrated into `docs/archive/legacy-ai-usage/`; kept for the historical context of the old `receipt_label` tracker migration. |
| `docs/ai-testing-workflow.md` | 2025-07-03 | Worktree/runbook stuff; now lives in the legacy archive folder to keep the main index clean. |
| `docs/pytest-optimization-guide.md` | 2025-07-03 | Superceded by newer test guides; archived beside the other AI usage artifacts. |
| `docs/testing-strategy-ai-usage.md` | 2025-07-03 | Branch/workstream diagram that no longer reflects the repo; archived for historians. |
| `docs/design-decisions/performance-testing-strategy.md` | 2025-07-01 (PR #150) | The original `SKIP_PERFORMANCE_TESTS` ADR is now in the archive folder with a note that it was Phase-1 mitigation. |
| `docs/types/api/interfaces/BoundingBox.md` | 2025-08-01 (PR #291) | Removed—only contained the word `temp` and duplicated real API docs elsewhere. |

## Issue analysis archive

Moved the following incident- and execution-specific write-ups from the root into `docs/archive/issue-analyses/` so they remain searchable without crowding the top-level namespace:

- `CHUNK_*` investigations about chunk failures and fix attempts.
- `EXECUTION_*` trace logs covering specific batch runs (including missing embeddings, failure analyses, ECR comparisons, etc.).
- `FINAL_MERGE_*`, `FORCE_REBUILD_EXPLANATION.md`, and `GRAND_TOTAL_VALIDATION_PATTERNS.md`, which chronicle ad-hoc investigations or proofs-of-concept that are no longer part of the regular workflow.

## Next steps

1. Keep the operational & deployment documentation (`docs/development`, `docs/operations`, etc.) under review so their referenced scripts still exist; update any broken references as part of future clean-up passes.
2. Replace `docs/README.md` with the refreshed index (already done) whenever releasing doc updates so new contributors see the current structure first.
3. Use this file as the source of truth whenever another doc is archived; mention the version/PR that led to the change (git blame references are helpful when explaining the why).
4. Revisit the archive later to see if any of the artifacts deserve condensation into a single timeline note instead of 200+ orphaned Markdown files.

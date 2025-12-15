# Documentation audit plan

## Scope

- Built `docs-refactor` from `main` to isolate the exploration.
- Listed every top-level directory (`architecture`, `layoutlm`, `operations`, etc.) and captured their last commit dates to understand which areas are actively maintained versus historical.
- Focused on the 21 files whose last updates occurred before September 2025 (AI usage guides, early CI docs, the Phase-1 doc cleanup artifacts) and traced them back to their PRs so we could explain why each existed.
- Documented why the handful of remaining “live” docs still matter, then moved every other `.md` under `docs/archive/` so the living tree contains only what’s still referenced by the code.

## Live documentation

These are the files that remain at the top level because the code still references them (via infra config, automation tooling, or README links).

- `AGENT_REFACTORING_PLAN.md`
- `CHROMADB_CLIENT_CLOSING_WORKAROUND.md`
- `CHROMADB_EMBEDDING_WRITE_PATHS.md`
- `DELTA_VALIDATION_AND_RETRY_IMPLEMENTATION.md`
- `METADATA_AGENTS_DIRECTORY.md`
- `METADATA_AGENTS_EVOLUTION.md`
- `METADATA_AGENTS_REVIEW.md`
- `PENDING_LABELS_BEST_PRACTICES.md`
- `README.md`
- `REFACTORING_SUMMARY.md`
- `architecture/CANONICAL_FIELDS_DEPRECATION.md`
- `architecture/COMPLETE_FLOW_DOCUMENTATION.md`
- `architecture/LAMBDA_NETWORKING_ARCHITECTURE.md`
- `architecture/overview.md`
- `chromadb-efs-architecture.md`
- `development/TESTING_STRATEGY.md`
- `development/ci-cd.md`
- `development/setup.md`
- `development/testing.md`
- `operations/deployment.md`

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

1. Keep the operational & deployment documentation under review so their referenced scripts still exist; update the living docs as the code evolves.
2. Treat `docs/archive/unreferenced/` as the resting place for obsolete plans/reports and keep it indexed so future reviewers understand what was moved and why.
3. Maintain `docs/archive/unreferenced/docs-refactor-plan.md` as the source of truth for the refactor history; mention PR references when additional docs get archived in future passes.
4. Periodically reassess whether any of the archived content should be deleted entirely or condensed into a single “Historical archive” summary.

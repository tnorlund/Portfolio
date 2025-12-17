# Documentation Index

Active documentation now covers only the handful of Markdown files that are referenced by the current codebase. Every other doc has been purged or moved into the purposeful archive subfolders so the living docs are concise yet the historical context still exists where needed.

## 🔍 Active sections

- **Architecture & ADRs**: `docs/architecture/overview.md`, `docs/architecture/LAMBDA_NETWORKING_ARCHITECTURE.md`, `docs/architecture/CANONICAL_FIELDS_DEPRECATION.md`, and the networking/performance callouts capture the up-to-date service map referenced by the infrastructure code.
- **Package Architecture**: `RECEIPT_LABEL_MIGRATION.md` explains the evolution from monolithic `receipt_label` to specialized packages (`receipt_chroma`, `receipt_places`, `receipt_agent`, etc.) and the architectural decisions behind the split.
- **Metadata & agents**: `AGENT_REFACTORING_PLAN.md`, `METADATA_AGENTS_DIRECTORY.md`, `METADATA_AGENTS_EVOLUTION.md`, `METADATA_AGENTS_REVIEW.md`, `REFACTORING_SUMMARY.md`, and `PENDING_LABELS_BEST_PRACTICES.md` document the ongoing metadata & agent workflows the step functions and Lambdas still rely on.
- **Chroma/delta guidance**: `CHROMADB_EMBEDDING_WRITE_PATHS.md`, `DELTA_VALIDATION_AND_RETRY_IMPLEMENTATION.md`, and `chromadb-efs-architecture.md` describe the Chroma/EFS behaviors referenced in multiple handlers.
- **Development onboarding**: `development/setup.md`, `development/testing.md`, `development/ci-cd.md`, and `development/TESTING_STRATEGY.md` contain the scripts and workflow instructions the repo tooling still consults.
- **Operations runbook**: `operations/deployment.md` remains the deployment playbook referenced in `infra/` automation.

## 🔖 Archive

- `docs/archive/legacy-ai-usage/` preserves the retired AI usage workstreams (migrated from the old `receipt_label` tracker).
- `docs/archive/issue-analyses/` keeps execution and chunk failure investigations that still serve as incident history.
- Other `docs/archive/*` folders (e.g., `planning`, `analysis`, `status`) capture the remaining planning/status stories for those subsystems.

This refactor deleted the “unreferenced” catch-all folder—you can find the audit history in this branch’s commits if you need the removed docs.

## 🚀 Quick links

- [System overview](architecture/overview.md)
- [Package architecture & receipt_label migration](RECEIPT_LABEL_MIGRATION.md)
- [Deployment runbook](operations/deployment.md)
- [Development setup](development/setup.md)
- [Testing plan](development/testing.md)
- [CI/CD notes](development/ci-cd.md)
- [Legacy archive index](archive/)

## 📖 Supporting resources

- [GitHub repository](https://github.com/tnorlund/Portfolio)
- [Live portfolio](https://tylernorlund.com)
- [AWS console](https://console.aws.amazon.com)

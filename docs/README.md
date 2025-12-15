# Documentation Index

Active documentation now centers on the handful of files that are referenced by the current codebase. Everything else has been moved into `docs/archive/unreferenced/` so the living doc tree stays minimal while still preserving history.

## 🔍 Active sections

- **Architecture & ADRs**: `docs/architecture/overview.md`, `docs/architecture/LAMBDA_NETWORKING_ARCHITECTURE.md`, `docs/architecture/CANONICAL_FIELDS_DEPRECATION.md`, and related networking/performance notes capture the system’s current topology.
- **Metadata/agents**: `AGENT_REFACTORING_PLAN.md`, `METADATA_AGENTS_DIRECTORY.md`, `METADATA_AGENTS_EVOLUTION.md`, `METADATA_AGENTS_REVIEW.md`, `REFACTORING_SUMMARY.md`, and `PENDING_LABELS_BEST_PRACTICES.md` explain the ongoing agent and metadata workflows that the Step Functions and Lambdas still rely on.
- **Chroma/delta guidance**: `CHROMADB_EMBEDDING_WRITE_PATHS.md`, `DELTA_VALIDATION_AND_RETRY_IMPLEMENTATION.md`, and `chromadb-efs-architecture.md` document the Chroma & EFS behaviors referenced by Lambda handlers.
- **Development onboarding**: `development/setup.md`, `development/testing.md`, `development/ci-cd.md`, and `development/TESTING_STRATEGY.md` contain the scripts and workflow instructions the repo machines and CD pipelines consult.
- **Operations runbook**: `operations/deployment.md` remains the authoritative deployment playbook.
- **Index**: This README is the entry point that ties all of the above together.

## 🔖 Archive

- **Primary archive**: Anything not in the list above now lives under `docs/archive/unreferenced/`. The structure mirrors the original layout when helpful, and the files remain available for historical reference but are no longer part of the active tree.
- **Special archive folders**:
  - `docs/archive/legacy-ai-usage/` retains the retired AI usage workstreams.
  - `docs/archive/issue-analyses/` keeps execution and chunk failure investigations.
  - Other `docs/archive/*` folders (e.g., `planning`, `analysis`, `status`) capture past planning docs, root-level reports, and status summaries.

- **Audit reference**: The documentation audit plan now lives at `docs/archive/unreferenced/docs-refactor-plan.md`.

## 🚀 Quick links

- **System overview**: [architecture/overview.md](architecture/overview.md)
- **Deployment runbook**: [operations/deployment.md](operations/deployment.md)
- **Development setup**: [development/setup.md](development/setup.md)
- **Testing playbook**: [development/testing.md](development/testing.md)
- **CI/CD notes**: [development/ci-cd.md](development/ci-cd.md)
- **Legacy archive index**: `docs/archive/unreferenced/`

## 📖 Supporting resources

- [GitHub repository](https://github.com/tnorlund/Portfolio)
- [Live portfolio](https://tylernorlund.com)
- [AWS console](https://console.aws.amazon.com)

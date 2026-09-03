# Agent Instructions

Monorepo for a receipt-processing system (Vision OCR → LayoutLM → DynamoDB →
LangGraph agents) and the Next.js portfolio site that visualises it. This file is
the single source of truth for every coding agent (Cursor, Claude Code, Codex,
Grok). `CLAUDE.md` only imports it; edit this file, never `CLAUDE.md`.

## Layout

- `receipt_dynamo/` DynamoDB entities and the only code that talks to DynamoDB.
- `receipt_dynamo_stream/` lightweight DynamoDB stream parsing.
- `receipt_upload/` receipt upload, OCR post-processing, line-item decode.
- `receipt_agent/` LangGraph agents (QA, validation) over receipt data.
- `receipt_chroma/`, `receipt_embeddings/` vector storage and search.
- `receipt_places/` Google Places client with DynamoDB cache.
- `receipt_layoutlm/` LayoutLM training, inference, CoreML export (heavy deps).
- `receipt_langsmith/`, `receipt_logo/` trace analytics and logo MCP tools (heavy deps).
- `receipt_ocr_swift/` Swift Mac worker: Apple Vision OCR + CoreML LayoutLM.
- `portfolio/` Next.js 16 / React 19 frontend. `infra/` Pulumi AWS stack.
- Package-specific rules live in nested `AGENTS.md` files inside
  `portfolio/`, `infra/`, `receipt_dynamo/`, and `receipt_ocr_swift/`.

## Environment

- Python 3.13 venv at `.venv/` (created by `.cursor/install.sh`) with the same
  editable package set as CI's `repository-tests` job: `receipt_dynamo`,
  `receipt_dynamo_stream`, `receipt_chroma`, `receipt_places`, `receipt_agent`,
  `receipt_upload`. Activate with `source .venv/bin/activate`.
- NOT installed (PySpark, torch, CoreML): `receipt_langsmith`, `receipt_layoutlm`,
  `receipt_logo`. Run `pip install -e "<package>[test]"` before working on those.
- Node 22 with `portfolio/node_modules` installed via `npm ci`. Run every npm
  command from `portfolio/`, never from the repo root.
- No AWS credentials by default. Unit tests use `moto` and pass offline; skip
  anything marked integration/e2e or that reaches real AWS, Pulumi, or Chroma Cloud.

## Checks

- Python format: `make format` (Black + isort, line length 79). CI runs
  `black --check --line-length=79 <package>` and
  `isort --check-only --profile=black --line-length=79 <package>` per package.
- Python tests: `pytest <package>/tests` from the repo root with the venv active.
  `receipt_dynamo` uses markers `unit`, `integration`, `end_to_end`.
- Frontend: `cd portfolio && npm run lint && npm run type-check && npm test`.
  CI runs `npm run test:ci`.
- CI (`.github/workflows/main.yml`) pins Python 3.13 and Node 22. Match those.
- Format only the files you touch; do not reformat unrelated packages.

## Conventions

- Type-annotate everything. Use boto3 stubs without runtime cost:
  `if TYPE_CHECKING: from mypy_boto3_dynamodb import DynamoDBClient`, then
  `client: DynamoDBClient = boto3.client("dynamodb")`. Stubs live in `[dev]` extras.
- Layering: `receipt_dynamo` owns all DynamoDB access, retries, and batching, and
  never imports sibling packages. Other packages call `DynamoClient` methods
  instead of `boto3` DynamoDB APIs directly.
- Entities live in `receipt_dynamo/receipt_dynamo/entities/`, accessors in
  `receipt_dynamo/receipt_dynamo/data/`. Match the style of neighbouring files.
- Imports at the top of the module; no inline imports.
- Commit messages: `feat:`, `fix:`, `chore:`, `docs:` prefix with a short imperative
  subject (see `git log`). One logical change per commit.
- Timestamps that cross Swift ↔ Python must serialise with `+00:00`, never `Z`
  (`datetime.fromisoformat` rejects `Z`).

## Hard rules

- Never commit directly to `main`; work on feature branches. Never force-push.
- Production is a hard no-go: never select, preview, refresh, update, destroy, or
  import `tnorlund/portfolio/prod`, and never trigger a production deployment.
- Pulumi against `tnorlund/portfolio/dev` only when the user explicitly asks for a
  dev deployment or live dev test. Pin every command to that fully qualified stack,
  verify AWS account `681647709217`, preview before applying, refuse unrelated
  deletes or replacements, and never interrupt a running update (the stack is shared).
- Never write to the prod table `ReceiptsTable-d7ff76a`. Dev evals read
  `ReceiptsTable-dc5be22` only.
- Don't commit screenshots, logs, `dev.*` scratch scripts, or debug instrumentation.
- Hooks in `.cursor/hooks.json` and `.claude/settings.json` enforce the Pulumi
  and git rules above; do not work around them.

## Skills (load on demand from `.agents/skills/`)

- `layoutlm-training` starting and monitoring SageMaker LayoutLM jobs, hyperparameters, label merge presets.
- `coreml-export` queueing and running CoreML exports, the isolated export-worker venv, quantization.
- `mac-ocr-worker` building and running the Swift Vision OCR + LayoutLM worker, model cache.
- `qa-agent-eval` the deploy → step function → viz cache → parallel grading → `SCORECARD.md` loop.
- `pr-screenshots` Playwright before/after screenshots for `portfolio/` PRs and how to host them.
- `sprouts-line-item-stack` stacked line-item decode PRs, Sprouts A/B evals, other-merchant uplift.
  Do not chase 187/187; bottle-return refunds stay no-baseline.
- `receipt-dynamo-integration-tests` exception mapping, fixtures, and patterns for `receipt_dynamo` tests.
- `dependabot-maintainer` reviewing, verifying, and merging Dependabot PRs.
- `portfolio-remote-control` launching the three Claude remote-control sessions.
- `codex-diff-review` milestone-sized work gated by a `codex exec` review of the diff.

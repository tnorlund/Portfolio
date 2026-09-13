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
- `receipt_embeddings/` native DynamoDB vector storage and search (`SearchVectors`).
- `receipt_places/` Google Places client with DynamoDB cache.
- `receipt_nutrition/` source-backed product facts and dimensional purchase costing.
- `receipt_layoutlm/` LayoutLM training, inference, CoreML export (heavy deps).
- `receipt_logo/` logo MCP tools (heavy dependencies).
- `receipt_ocr_swift/` Swift Mac worker: Apple Vision OCR + CoreML LayoutLM.
- `portfolio/` Next.js 16 / React 19 frontend. `infra/` Pulumi AWS stack.
- Package-specific rules live in nested `AGENTS.md` files inside `portfolio/`,
  `infra/`, `receipt_dynamo/`, `receipt_upload/`, `receipt_agent/`,
  `receipt_embeddings/`, `receipt_ocr_swift/`, `synthesis_loop/`, and
  `tools/glyph-studio/`.

## Environment

- Python 3.13 venv at `.venv/` (created by `.cursor/install.sh`) with the same
  editable package set as CI's `repository-tests` job: `receipt_dynamo`,
  `receipt_embeddings`, `receipt_dynamo_stream`, `receipt_places`, `receipt_agent`,
  `receipt_upload`, `receipt_nutrition`. Activate with `source .venv/bin/activate`.
- NOT installed (torch, CoreML): `receipt_layoutlm`, `receipt_logo`. Run `pip install -e "<package>[test]"` before working on those.
- Node 22 with `portfolio/node_modules` installed via `npm ci`. Run every npm
  command from `portfolio/`, never from the repo root.
- Do not assume credentials or AWS access. Unit tests and `receipt_dynamo`'s
  moto integration tests run offline; skip tests that reach live AWS/Pulumi
  unless that environment and operation are authorized.

## Checks

- Python format: `make format` (Black + isort, line length 79). CI runs
  `black --check --line-length=79 <package>` and
  `isort --check-only --profile=black --line-length=79 <package>` per package.
- Python tests: `pytest <package>/tests` from the repo root with the venv active.
  `receipt_dynamo` uses markers `unit`, `integration`, `end_to_end`.
- Frontend: `cd portfolio && npm run lint && npm run type-check && npm test`.
  CI runs `npm run test:ci`.
- Baseline CI pins Python 3.13 and Node 22. Container Lambda packages also
  have required Python 3.14 tests and native Linux ARM64 image import checks.
  LayoutLM containers and ZIP Lambdas remain on Python 3.13.
- Format only the files you touch; do not reformat unrelated packages. CI lints
  `receipt_agent` on changed `.py` files only; every other package is linted whole.
- The main CI matrix runs for PRs targeting any branch, including children in
  a PR stack. Run package checks locally while implementing and require each
  PR's own CI to pass before marking it ready.
- Browser tests select an isolated loopback port via `PLAYWRIGHT_PORT` in CI.
  Investigate port conflicts using that run's logs; never kill host-wide listeners.

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
- Timestamps that cross Swift ↔ Python use the project's `+00:00` convention.
  Python 3.13 accepts `Z`; do not describe that convention as a parser limitation.

## Hard rules

- Never commit directly to `main`; work on feature branches. Never force-push.
- Agents may merge when the user explicitly authorizes it. In a PR stack, parents are
  merged with a merge commit and only the leaf is squashed (squashing a parent
  orphans every child).
- `scripts/*dev_to_prod*`, `scripts/promote_*`, `scripts/activate_merchant_truth.py`,
  and any `--live` flag are owner-only; never run them.
- Direct production commands are prohibited: never select, preview, refresh,
  update, destroy, or import `tnorlund/portfolio/prod`. The normal main CI
  deployment is part of a PR merge explicitly authorized by the user. Scheduled
  maintenance stays report-only unless the owner separately enables
  `DEPENDABOT_AUTOMERGE=true`; a manual maintenance merge dispatch likewise
  explicitly authorizes those normal releases. Never enable either on the
  user's behalf without authorization.
- Pulumi against `tnorlund/portfolio/dev` only when the user explicitly asks for a
  dev deployment or live dev test. Pin every command to that fully qualified stack,
  verify AWS account `681647709217`, preview before applying, refuse unrelated
  deletes or replacements, and never interrupt a running update (the stack is shared).
- Never write to the prod table `ReceiptsTable-d7ff76a`. Dev evals read
  `ReceiptsTable-dc5be22` only.
- Don't commit screenshots, logs, `dev.*` scratch scripts, or debug instrumentation.
- Hooks in `.cursor/hooks.json`, `.claude/settings.json`, and `.codex/hooks.json`
  catch common risky direct CLI forms. They are not a shell interpreter or a
  security boundary and do not inspect arbitrary code, aliases, or script bodies.
  Codex normalizes shell calls to `Bash`/`tool_input.command` for these hooks;
  project hooks require the host's hook support and trust review. See
  [the hook contract](docs/agent-hooks.md). Do not work around a configured denial.

## Code Review Rules

- Flag DynamoDB calls (`boto3.client("dynamodb")`, `Table(...)`, raw
  `put_item`/`query`) outside `receipt_dynamo/`. Safe path: add a `DynamoClient`
  method in `receipt_dynamo/receipt_dynamo/data/`.
- Flag any reference to the prod stack or prod table in scripts, configs, or
  tests. Safe path: `tnorlund/portfolio/dev` and `ReceiptsTable-dc5be22`.
- Flag Swift date formats ending in `XXXXX` or hard-coded `Z` suffixes.
- Flag committed screenshots, logs, `dev.*` scripts, or leftover debug prints.
- Leave formatting and lint findings to CI (Black, isort, ESLint run there).

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

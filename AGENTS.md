# Agent Instructions

Full project documentation lives in [CLAUDE.md](CLAUDE.md) — architecture, training/export
pipelines, the QA-agent evaluation workflow, and PR screenshot conventions. Read it first;
this file only covers environment mechanics.

## Environment

- Python 3.13 venv at `.venv/` (created by `.cursor/install.sh`) with the same editable
  package set as CI's `repository-tests` job: `receipt_dynamo`, `receipt_dynamo_stream`,
  `receipt_chroma`, `receipt_places`, `receipt_agent`, `receipt_upload`. Activate with
  `source .venv/bin/activate`. NOT installed (heavy extras — PySpark, torch, CoreML):
  `receipt_langsmith`, `receipt_layoutlm`, `receipt_logo`; run
  `pip install -e "<package>[test]"` yourself before working on those.
- Node 22 with `portfolio/node_modules` installed via `npm ci`. The Next.js app lives in
  `portfolio/` (`npm run dev`, `npm test`, `npm run build`).
- No AWS credentials are present by default. Unit tests use `moto` and pass offline; skip
  anything marked integration/e2e or that reaches real AWS, Pulumi, or Chroma Cloud.

## Checks

- Format: `make format` (Black + isort). Lint: `make lint`.
- Python tests: `pytest <package>/tests` from the repo root with the venv active.
- Frontend: `cd portfolio && npm test`.
- CI (`.github/workflows/main.yml`) pins Python 3.13 and Node 22 — match those, not newer.

## Conventions

- Never commit directly to `main`; work on feature branches.
- Do not run `pulumi` commands; deploys are CI-driven (and a local `pulumi up` can lock the
  account-wide stack).
- Don't commit screenshots, logs, or `dev.*` scratch scripts you didn't author.

## Line-item decode stack

Sprouts match is stacked geometry PRs (constraint → pairing → fragment join →
qty-echo / ITEMS tail → zone-gap), then remaining-error EDA plus other-merchant
uplift. Follow `.cursor/skills/sprouts-line-item-stack/SKILL.md`. Do not chase
187/187; bottle-return refunds stay no-baseline.

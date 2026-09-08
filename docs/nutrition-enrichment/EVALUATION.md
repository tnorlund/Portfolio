# Nutrition evaluation ledger

Updated 2026-09-08. Branch codex/nutrition-enrichment.
Contract: [SPEC.md](SPEC.md). Tasks: [AGENT_PLAN.md](AGENT_PLAN.md).

## Current checkpoint

P revised contract passed full-diff re-review with no remaining HIGH/MEDIUM
findings after two corrections. Implementation gates remain separate below.
A–G not implemented/evaluated yet. Historical research counts are not current
evaluation results. No live model, dev deployment, or public release claimed.

| Gate | Status | Evidence / next action |
|---|---|---|
| P contract review | PASS | two MEDIUM findings fixed; full-diff second pass found none |
| A schema/≥30 dimensional cases | NOT RUN | isolated Python 3.13 tests |
| A honest harness/fixture validation | NOT RUN | synthetic vs source-backed modes |
| B persistence/generation/conditions | NOT RUN | moto tests |
| C 20–30 product pilot evidence | NOT RUN | independent label/package verification |
| C ≥150-line grouped held-out fixture | NOT RUN | source/split/count validation |
| C real imported catalog recall | NOT RUN | fixed-denominator retrieval |
| C real model precision/cost | NOT RUN | configured capped live run; acceptance off |
| D private MCP correction loop | NOT RUN | repeated purchases/all alias statuses |
| E events/concurrency/deletion/repair | NOT RUN | adversarial integration tests |
| E ARM64 artifact | NOT RUN | real package import/size/runtime |
| F dev backfill/end-to-end | NOT RUN | implementation packet, then explicit authorization |
| G public projection/cache | NOT RUN | forbidden data absent from serialized results |
| G browser/owner review | NOT RUN | checks/screenshots/visual review |
| PR/merge/release | NOT RUN | draft PR after review; no merge authorized |

## Evidence format

Record commit/base, fixture/catalog hashes, commands, pass/fail counts,
metric numerators/denominators, evidence tier and unresolved limits. Keep
raw logs/bulk catalogs outside git. Record HIGH/MEDIUM review findings and
their resolution before committing.

### P review, first pass

- MEDIUM: cleanup could delete verified staged rows before publication.
  Resolved by making cleanup share the publication lease and requiring read
  hash/count validation and retry when the manifest changes.
- MEDIUM: parent-only deletion lacked a terminal cache invalidation signal.
  Resolved by routing parent REMOVE, filtering absent parents in aggregates,
  invalidating caches even after manifest deletion, and full repair rebuilds.
- Environment: installed Codex 0.147.0 rejected the configured model. Review
  ran successfully using an isolated temporary Codex 0.153.4 install; the
  global CLI and model settings were not changed.
- Second pass: no remaining actionable HIGH/MEDIUM findings across the full
  planning diff. `git diff --check` and Python version-consistency check pass.

## Review corrections incorporated

Units/evidence required; identity/quantity/completeness separated; revisioned
corrections update past purchases; atomic generation publication and lease
fencing; REMOVE and bounded repair; live evaluation distinct from fake;
product-family holdout; source permissions enforced before public
aggregation; TJ bulk not required; main #1391, FDC April 2026, corrected token
cost, fully qualified dev stack examples.

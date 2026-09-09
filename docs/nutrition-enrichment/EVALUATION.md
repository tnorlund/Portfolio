# Nutrition evaluation ledger

Updated 2026-09-08. Branch codex/nutrition-enrichment.
Contract: [SPEC.md](SPEC.md). Tasks: [AGENT_PLAN.md](AGENT_PLAN.md).

## Current checkpoint

P revised contract passed full-diff re-review with no remaining HIGH/MEDIUM
findings after two corrections. Implementation gates remain separate below.
A passed full-diff review and is committed as d185bce01. B1 catalog storage
passes local tests and three clean full-diff reviews.
B2 has a tested local prototype, not yet integrated into DynamoClient or committed.
C–G remain pending.
Historical research counts are not current
evaluation results. No live model, dev deployment, or public release claimed.

| Gate | Status | Evidence / next action |
|---|---|---|
| P contract review | PASS | two MEDIUM findings fixed; full-diff second pass found none |
| A schema/≥30 dimensional cases | PASS | 92 Python 3.13 tests; 35 independent arithmetic cases |
| A honest harness/fixture validation | PASS | synthetic labelled explicitly; offline/live return NOT RUN and exit 2 |
| B1 immutable catalog/conditional aliases | PASS (local) | 52 entity/moto tests + 1 domain roundtrip; review clean |
| B2 generation/publication | IN PROGRESS | 15 prototype entity/moto tests; integration/review pending |
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

### A reviewed checkpoint

- `.venv/bin/python -m pytest receipt_nutrition/tests -q`: 92 passed.
- `.venv/bin/python scripts/nutrition_harness/evaluate.py --mode contract`:
  35/35 cases passed. These are synthetic dimensional arithmetic examples,
  not real package labels or model-quality ground truth.
- Fixture SHA-256:
  `fe60a6bd58626320bc08756b7e1b67d0142e9fc7cf67c76429c3d985447b4864`.
- Missing units, incompatible dimensions, unverified source facts, negative
  adjustments, absent nutrients, supplied/derived servings, Decimal rounding,
  and decoder disagreement have explicit checks.
- First diff review found and fixed fractional-token suffix matching and
  precision loss from exact fluid-ounce conversion. Regression cases include
  spaced fractions and grouped digits. The second review found a
  repeating-decimal cent tie and grouped rate prefix; both are fixed. Cost
  ratios now remain rational until final cent rounding. A third pass narrowed
  the grouped-rate guard to preserve separate printed totals. The fourth
  pass added mixed-fraction rate rejection; numeric rate tokens now must
  terminate completely. A fifth pass added spaced-decimal prefix rejection;
  apostrophe groupings and Unicode negative signs also abstain. The sixth
  review exposed Unicode separators and discarded price denominators. The
  adapter now full-matches a complete annotation line, eliminating numeric
  suffix searching entirely. Mixed name/quantity lines remain unknown. Seventh-pass corrections reject
  ambiguous integer-rate/total sequences and check separate printed totals.
  Eighth full-diff pass: no remaining HIGH/MEDIUM findings.
- Wheel builds successfully; this does not prove the later ARM64 Lambda runtime.
- New package is wired into the Python 3.13 CI matrix and local/repository
  installation lists. Provider/persistence/stream/UI are subsequent stages.

## Review corrections incorporated

Units/evidence required; identity/quantity/completeness separated; revisioned
corrections update past purchases; atomic generation publication and lease
fencing; REMOVE and bounded repair; live evaluation distinct from fake;
product-family holdout; source permissions enforced before public
aggregation; TJ bulk not required; main #1391, FDC April 2026, corrected token
cost, fully qualified dev stack examples.

### B1 local storage checkpoint

See [CATALOG_STORAGE.md](CATALOG_STORAGE.md) for invariants and the revision/TTL
refinement. Both full-diff review passes found no HIGH/MEDIUM correctness
findings; the third clean pass includes package-required unused-in-production markers
and this ledger update. Targeted mypy passes for four implementation modules.
Explicit local checks run these tests; ordinary CI skips the temporary marker
until the E consumer is wired. No live AWS writes or dev runtime claims.

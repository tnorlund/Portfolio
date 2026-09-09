> Owner steering, 2026-09-08: the next deliverable is private lunch plus
> compatibility/freshness repairs and a same-case persistence comparison.
> Broad catalog, stream integration, backfill and public UI are deferred.
> Earlier v1 choices/thresholds are proposed defaults, not owner decisions.
> The initial B2 PASS below described local prototype tests only; the devil's
> advocate review reproduced missing cases and supersedes its readiness.

# Nutrition evaluation ledger

Updated 2026-09-08. Branch codex/nutrition-enrichment.
Contract: [SPEC.md](SPEC.md). Tasks: [AGENT_PLAN.md](AGENT_PLAN.md).

## Current checkpoint

P revised contract passed full-diff re-review with no remaining HIGH/MEDIUM
findings after two corrections. Implementation gates remain separate below.
A passed full-diff review and is committed as d185bce01. B1 catalog storage
passes local tests and three clean full-diff reviews.
B1 is committed as 1ecf16526. B2 is replaced by the evaluated single-document
design. B2/L requires a clean independent full-diff review before commit;
neither milestone proves deployed behavior.
Broader C–G work is deferred by the owner.
Historical research counts are not current
evaluation results. No live model, dev deployment, or public release claimed.

| Gate | Status | Evidence / next action |
|---|---|---|
| P contract review | PASS | two MEDIUM findings fixed; full-diff second pass found none |
| A schema/≥30 dimensional cases | PASS | 92 Python 3.13 tests; 35 independent arithmetic cases |
| A honest harness/fixture validation | PASS | synthetic labelled explicitly; offline/live return NOT RUN and exit 2 |
| B1 immutable catalog/conditional aliases | PASS (local) | 52 entity/moto tests + 1 domain roundtrip; review clean |
| B2 prototype | SUPERSEDED | initial tests missed reproduced compatibility/freshness failures |
| B2 single document + compatibility | PASS (local) | 57 targeted tests and all 23 resegmentation tests; full-diff review is the commit gate |
| L private lunch calculation | PASS (local; incomplete label coverage) | runnable private report, 116 package tests; current egg nutrition remains unknown |
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

### B2 generation prototype checkpoint (superseded)

See [RECEIPT_PUBLICATION.md](RECEIPT_PUBLICATION.md). Fifteen new entity/moto
tests and 34 existing B1 integration cases pass with the final DynamoClient
composition. Targeted mypy passes. Independent review is pending. No stream,
cache, AWS runtime, model or public UI result is inferred from these tests.

## Owner-directed review repair checkpoint

Reproduced 2 failing freshness tests against the generation prototype and
2 failing resegmentation plan cases before fixes. Replaced the uncommitted
prototype with the smaller atomic document design. Comparison and scope
limits: [RECEIPT_PUBLICATION.md](RECEIPT_PUBLICATION.md). No stream code added.

Commands after repair:

```sh
.venv/bin/python -m pytest tests/test_resegment_receipt_lambda.py -q
.venv/bin/python -m pytest receipt_dynamo/tests/integration/test__receipt_nutrition.py receipt_dynamo/tests/integration/test__nutrition_catalog.py receipt_dynamo/tests/unit/test_receipt_nutrition_entities.py -q
```

Results: 23 passed (existing Pillow deprecation warnings); 57 passed.
TransactionConflict uses injected ClientError reasons because moto cannot
reproduce AWS transaction contention. It retries three transient attempts to
success and surfaces exhaustion after four attempts. This is offline wire
behavior, not AWS runtime proof.

Earlier bulk research downloaded FDC Foundation (3.8 MB) and Branded (449 MB)
April 2026 archives to /tmp. They were not imported into a catalog, committed,
or used for a quality claim. Broad import work is now deferred.

Independent review found a MEDIUM retryability gap for AWS transaction
cancellation reasons ThrottlingError and ProvisionedThroughputExceeded. The
shared bounded helper now handles both alongside TransactionConflict, with
parameterized success/exhaustion tests.

The second review found RequestLimitExceeded was still non-retryable. A
complete top-level contention/throttle test table now covers this and
TransactionInProgressException as well as the three cancellation reasons.
Retries remain bounded at four; exhaustion stays retryable for callers.
Existing server errors retain the shared decorator's retryable mapping.

## Private lunch implementation checkpoint

The local `receipt_nutrition.portions` CLI now computes independent purchase
and eaten quantities, exact household portion ratios and money, and complete
versus partial nutrient totals. It supports explicit `--quantity` and
`--portion` overrides. No catalog, model, MCP deployment or stream required.

Validation: 116 package tests pass, including 23 new portion cases covering
teaspoon/tablespoon, per-egg counts, quantity-only corrections, unknown and
conflicting purchase quantities, missing/unverified facts, unit incompatibility,
invalid amounts, once-only money rounding and actual CLI overrides.
The original synthetic contract remains 35 cases; this is not a real-model
evaluation. The owner's input JSON and runnable Markdown/JSON output are
private, in a local directory outside git.

Source checks: the official TJ product page supplied the complete Tater
Bites label; the official Kerrygold page supplied its per-tablespoon values;
Costco identified the salted SKU and pack/serving count. Conflicting Costco
nutrient values were not substituted for manufacturer facts. The egg
carton's current nutrition label remains unverified; the private result
therefore leaves its nutrients and dependent complete meal totals unknown.
An async question requests the current egg panel. Carton count and butter
amount are explicit scenario assumptions, not newly confirmed owner facts.

Current source gap is material: no claim that all three lunch labels or the
complete calorie total have been verified. Broader work remains deferred.

Deferred acquisition research was read as requested. Card M now proposes one
versioned Target adapter with source-method provenance and identity/variant/
unit/preparation validation. No merchant acquisition implementation or probe
was started. The research's local script results remain local evidence;
Lambda is unverified and RedSky undocumented. Registry, browser workers,
weekly canaries and rediscovery are deferred pending demonstrated need.

The combined review found three MEDIUM portion issues. Added rejection of
inconsistent package/serving bases (with a bounded explicit rounding allowance
for nominal labels), direct physical-quantity costing/nutrients without an
unrelated serving label, and visible generic-estimate status in Markdown.
All three have regression cases. The reviewer reproduced the private saved
outputs and manufacturer butter table; its independent TJ browser recheck
was denied by browser approval review, so TJ verification remains the earlier
parent-session direct page observation, not a repeated independent check.

The next review found two MEDIUM issues: exact converted purchase quantities
were narrowed to a 12-decimal portion type, and Markdown hid the effective
quantity after a CLI override. Three regression cases failed before repair.
Portions now retain the existing physical quantity precision; both purchased
and eaten converted amounts round-trip through the real CLI. Reports print
the effective quantity/unit and distinguish original notes from overrides.
All 116 package tests pass after repair. The complete milestone diff must
receive a clean independent review before it is committed and pushed.

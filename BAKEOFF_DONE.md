# Round A rubric self-report

Implementation commit: `001380380`

All implementation and verification ran offline. No `CHROMA_CLOUD_*`
credentials were present, so no live capture was attempted. The committed
fixture identifies itself as `canonical: false` and `backend:
offline_bootstrap`. The live capture command is guarded to the `receipt_dev`
database and `ReceiptsTable-dc5be22`, uses read APIs only, and is reserved for
the winner's single post-selection canonical capture.

## 1. Fixture coverage

Addressed by `tests/fixtures/similarity/golden.json` and schema enforcement in
`scripts/similarity_harness/common.py`. The committed fixture contains 81
receipts and 243 queries: 81 merchant-resolution queries, 81 word-neighbor
queries, and 81 section-verifier queries. Every receipt has all three families.
Word queries contain exactly 30 ranked IDs and distances. Merchant records
contain neighbors, matched or not-found decisions, and phone/address/text tier
outcomes. Section records contain predicted sections and agree, disagree, or
abstain votes.

Verify with:

```bash
.venv/bin/pytest receipt_embeddings/tests/test_harness.py \
  -q -k committed_fixture_covers
```

## 2. Metrics match the specification

Addressed by `scripts/similarity_harness/evaluate.py`. Its deterministic JSON
scorecard reports recall@k overall and by query family, merchant agreement,
merchant tier-decision agreement, expected and actual tier distributions with
percentage-point deltas, section-vote agreement, p50/p95 latency, read request
units per query, and estimated USD per query. The scorecard also evaluates the
0.90 recall, 98 percent merchant agreement, 5 percentage-point tier, and 100 ms
DynamoDB p95 gates.

Verify with:

```bash
.venv/bin/python scripts/similarity_harness/evaluate.py \
  --backend fake --out fake-scorecard.json
```

## 3. Determinism

Addressed by canonical JSON serialization, sorted receipt/corpus/query output,
fixed vector and distance rounding, a self-checking content SHA-256, and the
absence of timestamps from fixtures and scorecards. Semantic recapture
comparison ignores latency and permits only the documented `1e-6` distance and
`1e-7` vector tolerances. Offline bootstrap captures were byte-identical in the
final gate run, and repeated fake evaluations were identical.

Verify with:

```bash
.venv/bin/python scripts/similarity_harness/capture_golden.py \
  --offline-bootstrap --out first.json
.venv/bin/python scripts/similarity_harness/capture_golden.py \
  --offline-bootstrap --compare-to first.json --out second.json
cmp first.json second.json
.venv/bin/pytest receipt_embeddings/tests/test_harness.py \
  -q -k 'deterministic or pure_given_fixture'
```

## 4. Chroma self-parity sanity

Addressed by the offline `CapturedChromaReplay` backend. It replays the
captured Chroma answer through the same `VectorSearchClient` path used by other
backends. The final run produced neighbor recall@10 of 1.0 for every family,
100 percent merchant agreement, 100 percent tier-decision agreement, and 100
percent section-vote agreement. This is intentionally a fixture self-parity
check and does not contact Chroma Cloud.

Verify with:

```bash
.venv/bin/python scripts/similarity_harness/evaluate.py \
  --backend chroma --out chroma-scorecard.json
.venv/bin/pytest receipt_embeddings/tests/test_harness.py \
  -q -k chroma_self_parity
```

## 5. Interface minimalism

Addressed by `receipt_embeddings/vector_client.py`. The runtime-checkable
`VectorSearchClient` protocol exposes only `search()` and `get_vector()`.
`FakeVectorIndex` implements exact NumPy cosine nearest-neighbor search,
equality filters, a stable key tie-break, defensive copies, and the 100-result
limit. Fake, Chroma replay, and future DynamoDB clients are selected without
changing evaluation consumer logic. Optional latency and request-unit
telemetry is discovered by the harness and is not part of the consumer
protocol.

Verify with:

```bash
.venv/bin/pytest receipt_embeddings/tests/test_fake_index.py -q
```

## 6. Runtime

Addressed by the offline runtime regression test and compact deterministic
fixture. In the final gate run, two 81-receipt captures completed in 0.12 and
0.14 seconds. Fake and Chroma-replay evaluations completed in 0.11 and 0.10
seconds. The test enforces capture below 15 minutes and evaluation below one
minute. Live timing remains to be recorded during the judge-authorized blessed
capture because credentials were unavailable and a live run was forbidden.

Verify with:

```bash
.venv/bin/pytest receipt_embeddings/tests/test_harness.py \
  -q -k runtime_limits
```

## Final offline gate

The exact CI-style command passed 35 tests. Focused package coverage was 100
percent, above the enforced 95 percent threshold. Black, isort, strict mypy,
Python-version consistency, wheel construction, fixture comparison, and both
offline evaluator backends also passed.

```bash
cd receipt_embeddings
../.venv/bin/python -m pytest tests -n auto --timeout=120 --tb=short \
  --maxfail=5 --reruns 1 --reruns-delay 2 \
  -m 'not end_to_end and not slow and not performance and not unused_in_production' \
  --cov --cov-report=xml
```

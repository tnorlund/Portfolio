# Round A self-report

Branch `bakeoff/A/grok`. Offline pytest is green (33 tests: package +
`tests/test_similarity_harness.py`). No DynamoDB vector indexes were created,
no AWS table writes, no live Chroma capture (`CHROMA_CLOUD_*` was unset).

Committed work: `receipt_embeddings/` (`VectorSearchClient` +
`FakeVectorIndex`), `scripts/similarity_harness/{capture_golden,evaluate}.py`,
fixtures at `tests/fixtures/similarity/{golden,corpus}.json`.

## 1. Fixture coverage

**Addressed.** `tests/fixtures/similarity/golden.json` holds **81 receipts**
(38 line-item golden + 43 May-26 placeholders). Every receipt has:

- merchant-resolution neighbors (top-20) + `tier` + `decision`
- word queries with top-30 neighbors (`key` + `distance`)
- section-verifier row queries (top-15) and
  AGREED/DISAGREED/ABSTAINED vote counts

Source is `synthetic_offline` (FakeVectorIndex). Live recapture:

```
python scripts/similarity_harness/capture_golden.py   # needs CHROMA_CLOUD_*
```

Without creds the script exits rather than inventing a live run; `--synthetic`
rebuilds the offline set.

**Verify**

```
python -m pytest tests/test_similarity_harness.py::test_committed_fixtures_cover_three_families -q
python -c "import json; g=json.load(open('tests/fixtures/similarity/golden.json')); print(g['meta']['n_receipts'], g['receipts'][0].keys())"
```

## 2. Metrics match SPEC §8

**Addressed.** `evaluate.py` scorecard `metrics` contains:

| Metric | Definition | Gate |
|---|---|---|
| `neighbor_recall_at_k` | \(\|R_{:k} \cap G_{:k}\| / \|G_{:k}\|\) per query, macro-averaged; families merchant@20 / words@30 / sections@15 plus `recall@10` | ≥ 0.9 at k=10 |
| `merchant_agreement_pct` | backend `decision` vs golden (Chroma) `decision` | ≥ 98% |
| `tier_distribution` + `tier_distribution_pp_gap` | backend vs golden tier counts, max absolute percentage-point gap | ≤ 5 pp |
| `section_vote_agreement_pct` | per-row AGREED/DISAGREED/ABSTAINED match | ≥ 95% |
| `latency_ms.p50` / `p95` | wall-clock of `search()` | p95 < 100 ms |
| `est_usd_per_query` | Fake/Chroma $0; Dynamo RRUs × $0.25 / million (us-east-1 on-demand) | recorded |

Thresholds live in `scripts/similarity_harness/common.py` and are asserted in
`receipt_embeddings/tests/test_metrics.py`.

**Verify**

```
python -m pytest receipt_embeddings/tests/test_metrics.py -q
python scripts/similarity_harness/evaluate.py --backend fake | python -c "import sys,json; print(json.load(sys.stdin)['metrics'].keys())"
```

## 3. Determinism

**Addressed.** FakeVectorIndex is exact cosine, sorted `(distance, key)`.
Synthetic capture seeds numpy (`seed=0`) and hashes merchant names with
SHA-256 (not salted `hash()`). Fixture `meta.tolerance.distance_atol` is
`1e-6`; neighbor sets are exact. Two `capture_synthetic(seed=0)` calls match
after stripping `captured_at`. `evaluate.py` is pure given fixtures except
wall-clock latency.

**Verify**

```
python -m pytest receipt_embeddings/tests/test_capture_golden.py::test_two_synthetic_captures_are_identical_modulo_timestamp receipt_embeddings/tests/test_evaluate.py::test_evaluate_is_pure_given_fixtures -q
```

## 4. Self-parity sanity

**Addressed.** `evaluate.py --backend chroma` uses live Chroma Cloud when
`CHROMA_CLOUD_*` is set; otherwise it **replays captured neighbors**
(`chroma_replay`). Against the committed fixtures both `fake` and
`chroma_replay` score **1.0** recall, **100%** merchant/tier/vote agreement.

Measured on this branch (81 receipts, 486 queries):

```
python scripts/similarity_harness/evaluate.py --backend chroma
# neighbor_recall_at_k.macro = 1.0
# merchant_agreement_pct = 100.0
# all_gates_pass = true
```

**Verify**

```
python -m pytest receipt_embeddings/tests/test_evaluate.py::test_chroma_replay_self_parity tests/test_similarity_harness.py::test_evaluate_cli_fake_and_chroma_self_parity -q
```

## 5. Interface minimalism

**Addressed.** `VectorSearchClient` is a `@runtime_checkable` Protocol whose
only members are `search(vector, index, top_k, filters)` and `get_vector(key)`.
`FakeVectorIndex`, `ChromaVectorClient`, `ReplayVectorClient`, and
`DynamoVectorClient` all implement those two methods. Consumers type against
the protocol; evaluate swaps backends by name.

`DynamoVectorClient` is read-only SearchVectors: no `update_table` /
`create_table` / `VectorIndexes`. Round A does not create indexes.

**Verify**

```
python -m pytest receipt_embeddings/tests/test_vector_client.py receipt_embeddings/tests/test_evaluate.py::test_dynamo_client_never_creates_indexes -q
python -c "from typing import get_protocol_members; from receipt_embeddings import VectorSearchClient; print(get_protocol_members(VectorSearchClient))"
```

## 6. Runtime

**Addressed.** Synthetic capture of 81 receipts completed in seconds as part
of `capture_golden.py --synthetic` (well under 15 min). Offline evaluate of
the full fixture set:

- `--backend fake`: ~0.20 s wall (486 queries)
- `--backend chroma` (replay): ~0.19 s wall

Both far under 1 min. Live Chroma capture is not timed here (no creds).

**Verify**

```
/usr/bin/time -p python scripts/similarity_harness/evaluate.py --backend fake >/dev/null
python -m pytest receipt_embeddings/tests tests/test_similarity_harness.py -q --timeout=120
```

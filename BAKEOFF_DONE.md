# Round A — rubric self-report (Cursor)

Branch: `bakeoff/A/cursor`  
Work: similarity evaluation harness (`VectorSearchClient` + `FakeVectorIndex` + capture/evaluate CLIs).  
Offline gate: `python -m pytest receipt_embeddings/tests` — **34 passed**.

Live Chroma capture was **not** run: `CHROMA_CLOUD_*` credentials were not in the environment. Committed fixtures are the deterministic exact-NN set from `capture_golden.py --synthetic`. The winner recaptures once against live Cloud as the canonical set (BAKEOFF.md).

---

## 1. Fixture coverage

**Addressed.** Committed fixtures at `tests/fixtures/similarity/` cover **81 receipts** (line-item goldens + 43-image May-26 catalog) and all three query families:

| Family | File | What is stored |
|---|---|---|
| Merchant resolution | `merchant_resolution.json` | neighbors + retrieval tier + merchant decision per receipt |
| Word top-30 | `word_neighbors.json` | neighbor keys + cosine distances for sampled words |
| Section-verifier votes | `section_verifier.json` | AGREED / DISAGREED / ABSTAINED per row |

**Verify:**

```bash
python -c "
from receipt_embeddings.fixtures import load_fixture_bundle
b = load_fixture_bundle()
print(len(b['golden_set']['receipts']))
print(len(b['merchant_resolution']['queries']))
print(len(b['word_neighbors']['queries']))
print(len(b['section_verifier']['queries']))
"
python -m pytest receipt_embeddings/tests/test_fixture_coverage.py receipt_embeddings/tests/test_capture.py -q
```

Expect `n_receipts >= 40` and all three JSON documents non-empty.

---

## 2. Metrics match SPEC §8

**Addressed.** `evaluate.py` writes a scorecard with the BAKEOFF / SPEC §8 quantities:

| Metric | Implementation |
|---|---|
| Neighbor **recall@k** | set overlap of retrieved vs golden keys (`metrics.recall_at_k`); scorecard keys include `recall@10`, merchant @1/5/10/20, words @10/30 |
| **Merchant agreement %** | casefold/whitespace-normalized name match vs fixture decision |
| **Tier distribution** | per-tier share + `max_abs_delta` (later-round ±5% gate) and per-receipt tier-decision agreement |
| **Latency percentiles** | p50 / p95 of `search()` wall time (ms) |
| **Est. $/query** | DynamoDB vector search $0.002/GB, 1 KB minimum (`cost.py`); fake/Chroma report `$0` with an explicit `cost_model` string |

**Verify:**

```bash
python scripts/similarity_harness/evaluate.py --backend fake --out /tmp/scorecard.json
python -c "
import json
d=json.load(open('/tmp/scorecard.json'))
assert 'neighbor_recall' in d and 'recall@10' in d['neighbor_recall']
assert 'merchant_agreement_pct' in d
assert 'tier_distribution' in d and 'max_abs_delta' in d['tier_distribution']
assert 'p50' in d['latency_ms'] and 'p95' in d['latency_ms']
assert 'est_usd_per_query' in d
print('ok', d['n_receipts'], d['neighbor_recall']['recall@10'])
"
python -m pytest receipt_embeddings/tests/test_metrics.py -q
```

---

## 3. Determinism

**Addressed.**

- Two `--synthetic` captures in one process are **bitwise identical** (`sort_keys` JSON; distances quantized to 1e-8; section-vote ties broken by label name, not `set` iteration).
- Live ANN tolerance is documented in `tests/fixtures/similarity/README.md`: neighbor-set Jaccard ≥ 0.95 at k=10; cosine distance atol **1e-5**.
- `evaluate.py` is a pure function of fixture JSON + backend answers (no AWS writes).

**Verify:**

```bash
python -m pytest receipt_embeddings/tests/test_capture.py::test_two_synthetic_captures_are_identical -q
# Live capture refused without creds:
python scripts/similarity_harness/capture_golden.py --out /tmp/nope ; echo exit:$?
```

Expect the second command to exit non-zero with `Refusing live capture`.

---

## 4. Self-parity sanity (`evaluate.py --backend chroma` ≈ 1.0)

**Addressed** at two layers:

1. **Offline (this environment):** `evaluate.py --backend fake` against the committed fixtures scores **recall@10 = 1.0**, **merchant agreement = 100%**, tier delta **0**. The Chroma adapter, wrapped around the same fake, also scores 1.0 (`test_chroma_adapter_self_parity_against_synthetic_fixtures`) — same `search()` / `get_vector()` path the live Cloud client uses.
2. **Live Cloud:** `evaluate.py --backend chroma` constructs a read-only `ChromaClient` from `CHROMA_CLOUD_*` and scores against fixtures. That run was skipped here (no creds). After the winner recaptures from live Chroma, `--backend chroma` on those fixtures is the ≈1.0 sanity check.

**Verify:**

```bash
python scripts/similarity_harness/evaluate.py --backend fake --out /tmp/scorecard.json
# recall@10 == 1.0, merchant_agreement_pct == 100.0
python -m pytest receipt_embeddings/tests/test_evaluate.py receipt_embeddings/tests/test_chroma_adapter.py -q
# With Cloud creds (not present in this bake-off workspace):
# python scripts/similarity_harness/evaluate.py --backend chroma --out /tmp/chroma-scorecard.json
```

`--backend dynamo` is wired and raises `NotImplementedError` (Round A must not create vector indexes).

---

## 5. Interface minimalism

**Addressed.** `VectorSearchClient` exposes only `search(vector, index, top_k, filters)` and `get_vector(key)`. Fake, Chroma adapter, and Dynamo stub all implement that pair. Index names are capability names (`line-embeddings` / `word-embeddings`).

**Verify:**

```bash
python -m pytest receipt_embeddings/tests/test_fixture_coverage.py::test_protocol_exposes_only_search_and_get_vector receipt_embeddings/tests/test_fake_index.py::test_fake_index_is_vector_search_client -q
```

`typing.get_protocol_members(VectorSearchClient) == {"search", "get_vector"}`.

---

## 6. Runtime

**Addressed.**

- Synthetic capture of 81 receipts: ~1–3 s (well under 15 min). Live Cloud capture is bounded by Chroma RTT × three families × ≥40 receipts; the CLI is read-only and does not write DynamoDB.
- Offline evaluate: **< 10 s** in this workspace (pytest also asserts `< 60 s`).

**Verify:**

```bash
time python scripts/similarity_harness/evaluate.py --backend fake --out /tmp/scorecard.json
python -m pytest receipt_embeddings/tests/test_evaluate.py::test_evaluate_offline_runtime_under_one_minute -q
```

# Round A completion report — Claude entrant

Branch `bakeoff/A/claude`. All work committed; `pytest` passes offline.

Verify everything below with:

```bash
python3 -m pytest receipt_embeddings/tests tests/test_similarity_harness.py -q
python3 -m black --line-length 79 --check receipt_embeddings scripts/similarity_harness tests/test_similarity_harness.py
python3 -m isort --profile black --line-length 79 --check-only receipt_embeddings scripts/similarity_harness tests/test_similarity_harness.py
```

Result on this machine: 12 passed (fake index) + 15 passed / 2 skipped
(harness; skips are the vendored-constant parity checks, see "Not verified
locally"), lint clean.

## 1. Fixture coverage (≥40 receipts; all three query families)

`scripts/similarity_harness/capture_golden.py` captures, per golden receipt:
(a) merchant-resolution Tier-2 query lines — chosen by the **real** resolver
helpers (`_extract_phone` / `_get_line_for_phone` / `_get_line_for_address` /
`_get_merchant_line`) — with top-20 line neighbors + distances, the decision
(tier / merchant / place_id), and each neighbor's DynamoDB place lookup
materialized into fixture metadata; (b) top-30 word neighbors with distances
for a deterministic 10-word sample; (c) section-verifier votes via the real
`verify_receipt_sections` behind a read-only `VerificationStore` wrapper
(would-be section updates are captured into fixtures, never written).

Golden set: the line-item golden receipts
(`receipt_upload/tests/fixtures/line_items_golden.json`; 38 entries, 5
`local_only` → 33 in Dynamo) topped up via `--extra-receipts` (e.g. the
May-26 known-merchant batch); a `--min-receipts 40` gate fails the capture
below 40. **Live capture not yet run** — no `CHROMA_CLOUD_*` creds in this
environment (see below); the full pipeline is exercised end-to-end against
synthetic fixtures in `tests/test_similarity_harness.py`.

Verify: read `capture_golden.py` (families a/b/c are `capture_merchant`,
`capture_words`, `capture_sections`); run it with dev creds to produce
`tests/fixtures/similarity/`.

## 2. Metrics match SPEC §8

`scripts/similarity_harness/evaluate.py` emits: neighbor recall@10 for
merchant lines and words (plus words@30), merchant agreement % (pure Tier-2
decision replay), tier distribution fixture-vs-backend with per-tier deltas,
section vote agreement %, p50/p95 `search()` latency, and
`est_cost_per_query_usd` (documented constants; dynamo filled from request
units when that backend lands). The decision math (`decision.py`) vendors the
resolver's thresholds/boosts/poison-guard verbatim and mirrors
`propagate_knn` for section votes.

Verify: `TestEvaluate::test_fake_backend_self_parity` asserts every metric
key; compare `decision.py` against `resolver.py:1312-1500` and
`section_propagation.py:24-70`.

## 3. Determinism

Fixtures: sorted-keys JSON, fixed indent, distances and vector components
rounded to 6 decimals, `mtime=0` gzip for the vectors sidecar, every list in
a documented deterministic order. Tolerance and the one legitimate drift
source (corpus writes between capture runs) are documented in
`tests/fixtures/similarity/README.md`; `manifest.json` `captured_at` is
deliberate provenance. `evaluate.py` is pure given fixtures: everything
except the `latency_ms` block is a deterministic function of fixture files +
backend answers.

Verify: `TestFixturesIO::test_write_is_byte_deterministic` (two writes,
byte-compared) and
`TestEvaluate::test_scorecard_is_deterministic_modulo_latency`.

## 4. Self-parity sanity (`--backend chroma` ≈ 1.0)

The chroma backend replays each fixture query by fetching the stored query
vector (`get_vector`) and re-searching — the identical path the fake backend
scores 1.0 on in `test_fake_backend_self_parity` (recall 1.0, merchant
agreement 100%, section votes 100%, tier deltas 0). The live ≈1.0 run needs
creds; `test_detects_perturbed_backend` proves the score drops when a
backend's answers actually differ, so parity ≈1.0 is a real signal, not a
tautology.

Verify offline: the two tests above. Verify live: capture, then
`python scripts/similarity_harness/evaluate.py --backend chroma --out sc.json`.

## 5. Interface minimalism

Consumers touch only `VectorSearchClient.search()` / `get_vector()`
(`receipt_embeddings/receipt_embeddings/vector_client.py`; one dataclass,
one protocol, two index-name constants; core package has zero deps and no
chromadb). `evaluate.py` swaps fake/chroma/dynamo backends with no consumer
changes; the dynamo stub raises `NotImplementedError` with the interface
ready for Round C/D. One judged deviation: `get_vector` takes an `index`
argument beyond the card's `get_vector(key)` — two indexes exist and a bare
key cannot disambiguate.

Verify: grep consumers of `receipt_embeddings` for anything beyond
`search`/`get_vector`/`ScoredItem`; `test_satisfies_protocol` checks the
fake against the runtime-checkable protocol.

## 6. Runtime (capture < 15 min; evaluate < 1 min offline)

Capture is ~4 Chroma queries + a handful of DynamoDB reads per receipt
(query vectors come from Chroma's stored embeddings — no OpenAI calls), well
under 15 minutes for ~40 receipts. Offline evaluate on the synthetic fixture
set completes in milliseconds; the full offline test suite runs in ~2s.

## Not verified locally

- Live capture against Chroma Cloud dev and the live `--backend chroma`
  self-parity score: no `CHROMA_CLOUD_*` credentials were present in this
  environment (per the round rules, coded + tested synthetically instead).
- Real fixture files: none committed; per BAKEOFF.md the winner recaptures
  the canonical set once after selection.
- `TestConstantParity` (vendored resolver/section-verifier constants): skips
  on this machine because chromadb fails to import under Python 3.14
  (pydantic-v1 `ConfigError`); runs in the repo's Python 3.13 venvs.
- Merchant "decision" fidelity vs a full `MerchantResolver._resolve_impl`
  execution: the harness computes decisions with the pure `decision.py`
  replay on both sides of the comparison (identical math); end-to-end
  resolver parity is Round E1's gate, not Round A's.

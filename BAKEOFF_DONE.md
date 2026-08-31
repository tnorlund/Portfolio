# Round C+E1 completion report — Claude entrant

Branch `bakeoff/C/claude`. All five card deliverables are implemented and
tested; live behavior was exercised against the dev table end-to-end
(small `--limit`-style smoke, then cleaned up). Hard rules held: no
vector index was created/altered/deleted; the only dev-table writes were
`…#EMBEDDING` items via the backfill script (24 items across 2 golden
receipts, deleted again after the smoke); nothing touched prod; the only
OpenAI call attempted was rejected by a placeholder key (no spend).

## Rubric self-report

### 1. Embedding-item entities + accessors

`receipt_dynamo/receipt_dynamo/entities/receipt_line_embedding.py` and
`receipt_word_embedding.py`: `RECEIPT_LINE_EMBEDDING` /
`RECEIPT_WORD_EMBEDDING`, SK under the `RECEIPT#` prefix ending
`#EMBEDDING`, `TYPE` set, vector attrs exactly `line_vector` /
`word_vector`, **no GSI1–4 keys** (tested), filter/projection attributes
exactly matching the judge-provisioned indexes (verified against the
live `DescribeTable` output: `section_type` inline filter + 8 projected
attrs on lines; `label_status` + 7 on words). `label_status` is the
closed set validated/pending/none (spec §3.3); vectors are validated
1536-dim finite non-zero and serialize as positional Number strings
(never scientific notation). House-style accessor mixins
(`data/_receipt_line_embedding.py`, `_receipt_word_embedding.py`) are
registered on `DynamoClient`: add / batch add / get / list-per-receipt
(TYPE-filtered, since lines, words, letters, and both embedding types
share the SK prefix) / batch delete / list-by-TYPE for backfill audits.
35 tests (entity unit + moto-backed accessor CRUD).

### 2. Embed-and-put writer

`receipt_embeddings/receipt_embeddings/writer.py` — `EmbedAndPutWriter`:
plans one receipt's visual rows (`group_lines_into_visual_rows`, primary
line keyed, `row_line_ids` carried) and ±2-word-context words using the
Round B formatting surface, derives merchant/place from `ReceiptPlace`,
row `section_type` from majority VALID sections (ties abstain), and
`label_status`/`primary_label`/`valid_labels` from `ReceiptWordLabel`
rows. Idempotent: existing items are detected first, so a re-run embeds
nothing, writes nothing, and makes zero OpenAI calls (tested, and proven
live — see item 3). Vectors come from a pluggable `VectorSource`
(realtime OpenAI default via `receipt_embeddings.openai.realtime`,
constructed lazily). Per-item skip-and-report: missing vector, invalid
item, empty text, and failed writes land in the report's `failures`;
batch-write failure degrades to per-item puts; nothing aborts the batch
(all tested).

### 3. Backfill script

`scripts/embedding_backfill/backfill_embeddings.py` (+ README): golden
receipts by default (the manifest loader is shared with
`capture_golden.py`, so `--extra-receipts` / `--limit` behave
identically to Round A), refuses any table but `ReceiptsTable-dc5be22`,
per-receipt skip-and-report (absent receipt, empty receipt, source auth
errors — all tested; auth-error skip also observed live), end-of-run
written/skipped report (human + optional `--report-out` JSON), bounded
searchability wait (polls SearchVectors for one sampled line + one
sampled word item until found or `--wait-timeout`; timeout reported,
not fatal).

**OpenAI-free design (scored plus):** `--vector-source chroma` reuses
the vectors already stored in Chroma Cloud dev (ids equal item keys;
read-only), which also preserves vector identity — OpenAI embeddings
are not bit-stable across calls. `--vector-source fixture` is a fully
offline source from a captured fixture corpus. `auto` prefers chroma,
falls back to openai, and errors clearly with neither.

**Live evidence (dev, 2 golden receipts, fixture vectors):** first run
wrote 8 line + 16 word items with 317 uncovered items skip-reported and
the sampled items searchable after **0.9s**; the immediate re-run wrote
**0** items, reporting all 24 as existing (idempotency proof). All 24
items were then deleted (embedding items only), leaving dev as found.

### 4. DynamoVectorSearchClient + quotas + contract tests

`receipt_embeddings/receipt_embeddings/dynamo_client.py`: implements the
Round A `VectorSearchClient` protocol over `search_vectors` (plain
boto3, adaptive retries; refuses to construct on boto3 < 1.43.64 with a
clear message). Protocol index names `lines-vectors`/`words-vectors` map
onto the physical `line-embeddings`/`word-embeddings` indexes; scores
pass through as cosine distance with the fake's (distance, key)
tie-break; filters compile to equality-only `SearchConditionExpression`;
`get_vector` fetches stored vectors by fixture key and raises `KeyError`
on absence (matching fake and replay); `last_latency_ms` /
`last_request_units` feed the harness (request units =
`VectorSearchRequestBytes`).

`evaluate.py --backend dynamo` is real via the pre-wired
`create_client_from_env` hook — **no Round A code was modified**. A full
258-query live run against dev completed cleanly (recall 0 as expected:
the judge-wiped index is empty; latency/cost columns populated).

`receipt_embeddings/receipt_embeddings/dynamo_quotas.py` pins the
service quotas as constants (top-k ≤ 100, equality-only filters, 4096
max dims, 5 indexes/table, boto3 floor, index/attr names) with contract
tests holding `FakeVectorIndex` to the same top-k boundaries, the same
`TypeError`/`ValueError` shapes, and the same operator-key rejection
(Round A standing amendment).

### 5. Merchant resolution behind VECTOR_BACKEND

`receipt_upload/receipt_upload/merchant_resolution/vector_retrieval.py`
plus a retrieval-only edit in `resolver.py`: the `lines_client.query`
call is replaced by `VectorSearchClient.search` through a seam that
defaults to `chroma` (an adapter over the client the resolver already
holds, distances and metadata passed through untouched);
`VECTOR_BACKEND=dynamodb` swaps in `DynamoVectorSearchClient.from_env()`;
unknown values are rejected. Every threshold
(`MIN_SIMILARITY_THRESHOLD`, boosts, `HIGH_CONFIDENCE_THRESHOLD`), the
phone/address/text tier logic, and OCR corroboration gating are
byte-for-byte unchanged — the diff touches only the retrieval block.
Evidence: the entire pre-existing resolver suite passes unmodified
through the seam; a behavior-identity test proves identical neighbors
yield identical `MerchantResult`s on both backends; a throttled backend
degrades to the existing empty-result path (no crash).

## Gates

- **Unit suites (fresh checkout, documented below):** receipt_embeddings
  87 passed; receipt_dynamo tests/unit 2475 passed (incl. 26 new) plus 9
  moto accessor tests; receipt_upload full suite exit 0 (incl. 9 new
  backend tests); receipt_upload + receipt_chroma collection clean.
- **Fake-backend parity vs canonical fixtures** (sha256-verified
  download): recall@10 overall **0.8663 ≥ 0.85** ✅ (matches the
  documented ≈0.87 offline-replay ceiling); tier-decision agreement
  100%; tier-distribution delta 0.0; section-vote agreement 100%.
  Merchant agreement as computed by evaluate.py is **91.86%**, below the
  98% gate — see the finding below; this number is fully determined by
  the Round A fake + blessed fixtures and is unchanged by (and outside
  the reach of) this round's deliverables.
- **Graceful degradation:** missing vector (writer skip-and-report;
  `get_vector` KeyError), throttle (ClientError surfaces from the
  client; resolver degrades to empty result), absent receipt (backfill
  skip-and-report) — all covered by tests named below.

### Finding: canonical-set fake-replay merchant agreement is 91.86%

All 7 disagreements (of 86) are near-tie flips, not retrieval failures:
the corpus contains near-duplicate header rows from *different branches
of the same chain* (e.g. two Sprouts receipts whose header embeddings
differ by ≤1.3e-06 cosine distance, one an exact duplicate at 0.0), and
exact-NN replay orders the tie differently than live Chroma did. In 6/7
cases decision and merchant_name agree and only `place_id` (the branch)
differs; the 7th is "Trader Joe'S" vs "Trader Joe's" (a `.title()`
casing artifact in stored Chroma metadata). Merchant-*identity*
agreement is 85/86 = **98.8%**. The number cannot be moved from Round C
scope: it is computed by Round A's evaluate.py over Round A's fake and
the blessed fixtures, none of which this round may modify. Diagnosis
script output is reproducible from the verify commands' fixture.

## Verify commands (verbatim)

Environment: the judge env `/Users/tnorlund/Portfolio/.venv` (Python
3.12, boto3/botocore 1.43.84, chromadb, moto, pytest). That venv's
receipt packages are editable installs of the MAIN checkout, so every
command below sets `PYTHONPATH` to make **this** checkout shadow them —
omitting it runs the wrong code. Run from the checkout root.

```bash
cd /Users/tnorlund/Portfolio-bakeoff-claude   # or any fresh checkout of bakeoff/C/claude
VENV=/Users/tnorlund/Portfolio/.venv
export PYTHONPATH="$PWD/receipt_dynamo:$PWD/receipt_dynamo_stream:$PWD/receipt_embeddings:$PWD/receipt_chroma:$PWD/receipt_upload:$PWD/receipt_places:$PWD"

# Item 1 — entities + accessors (unit + moto CRUD)
"$VENV/bin/python" -m pytest receipt_dynamo/tests/unit/test_receipt_line_embedding.py receipt_dynamo/tests/unit/test_receipt_word_embedding.py receipt_dynamo/tests/integration/test__receipt_embedding.py -q -p no:cacheprovider

# Items 2–4 — writer, dynamo client, quota contract tests pinning the fake, backfill script (all offline)
"$VENV/bin/python" -m pytest receipt_embeddings/tests -q -p no:cacheprovider

# Item 5 — VECTOR_BACKEND seam + full pre-existing resolver suite through it
"$VENV/bin/python" -m pytest receipt_upload/tests/test_vector_backend.py receipt_upload/tests/test_merchant_resolver.py -q -p no:cacheprovider

# Phase-1 parity gate — canonical fixtures (sha256-checked by the loader)
aws s3 cp s3://raw-image-bucket-c779c32/similarity-fixtures/canonical-2026-08-31/golden.json.gz - | gunzip > /tmp/canonical_golden.json
"$VENV/bin/python" scripts/similarity_harness/evaluate.py --backend fake --fixture /tmp/canonical_golden.json --out /tmp/scorecard_fake.json
```

Phase-2 (judge-run, dev; AWS credentials required — plus
`CHROMA_CLOUD_*` for the OpenAI-free source or `OPENAI_API_KEY` to
re-embed):

```bash
# capped backfill; second invocation is the idempotency proof (writes 0)
"$VENV/bin/python" scripts/embedding_backfill/backfill_embeddings.py --limit 10 --vector-source auto --report-out /tmp/backfill_report.json
"$VENV/bin/python" scripts/embedding_backfill/backfill_embeddings.py --limit 10 --vector-source auto --report-out /tmp/backfill_rerun.json

# the real dynamo scorecard
"$VENV/bin/python" scripts/similarity_harness/evaluate.py --backend dynamo --fixture /tmp/canonical_golden.json --out /tmp/scorecard_dynamo.json
```

Merchant resolution cutover is `VECTOR_BACKEND=dynamodb` in the
resolver's environment; unset/`chroma` is the unchanged default.

## Not verified locally

- **OpenAI vector source with a real key** — the only key on this
  machine is a placeholder; the 401 exercised the skip-and-report path,
  not a successful embed. The `OpenAIVectorSource` batching/ordering
  logic is unit-tested against a stubbed client only.
- **Chroma vector source against live Chroma Cloud** — credentials are
  Pulumi secrets I did not retrieve; `ChromaVectorSource` follows the
  same read-only `.get(ids=…)` contract capture_golden.py uses, and is
  exercised only via that shared contract, not live.
- **Graded-scale backfill + populated-index recall/latency** — my live
  writes were deliberately capped at 2 receipts (card: judge runs the
  graded backfills) and cleaned up; `evaluate.py --backend dynamo`
  numbers on a populated index are therefore unverified. The full
  258-query live run against the empty index completed cleanly.
- **p95 < 100ms from Lambda** — measured locally over the internet
  (p50 ≈ 291ms from this laptop); in-region behavior not measurable
  from here.
- **CI matrix runs** — not pushed (per protocol); local equivalents of
  the receipt_embeddings / receipt_dynamo / receipt_upload suites were
  run with the commands above.

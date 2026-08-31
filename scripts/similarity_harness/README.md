# Similarity evaluation harness

Round A freezes Chroma retrieval behavior before later rounds replace the
backend. The fixture contains one merchant, one sampled-word, and one section
query for every receipt. It also contains the query and neighbor vectors needed
by the exact offline fake. Receipt text, phone numbers, and addresses are not
stored.

## Offline verification

The committed fixture is an explicitly non-canonical bootstrap because this
worktree did not have `CHROMA_CLOUD_*` credentials. It exercises all schema,
coverage, scoring, and runtime paths without touching AWS or Chroma:

```bash
python scripts/similarity_harness/evaluate.py \
  --backend chroma \
  --out scorecard.json

python scripts/similarity_harness/evaluate.py \
  --backend fake \
  --out fake-scorecard.json
```

`--backend chroma` is a replay of the captured answers. It is deliberately
offline: scoring the reference against itself must produce recall and consumer
agreement of 1.0 before that fixture is allowed to grade another backend.
`--backend fake` performs exact cosine nearest-neighbor search over the fixture
corpus. `--backend dynamo` loads the implementation supplied by a later round,
optionally through `VECTOR_CLIENT_FACTORY=module:callable`, and measures wall
latency and request units.

Evaluation emits no current timestamp or runtime value. With a fixed fixture
and backend response it is byte deterministic. Fake evaluation records zero
latency and request units; Dynamo evaluation records wall latency and consumes
optional `last_request_units` or `get_last_search_metrics()` telemetry from the
backend without expanding the two-method consumer protocol.

## Blessed live capture

Run a live capture only when all three Chroma variables already exist in the
environment. The script permits only the dev Chroma database and dev DynamoDB
table, and its code path uses reads only:

```bash
python scripts/similarity_harness/capture_golden.py \
  --manifest path/to/may26-and-line-item-receipts.json \
  --canonical \
  --out tests/fixtures/similarity/golden.json
```

The repository does not contain the May-26 receipt manifest named by the task
card. Without `--manifest`, capture combines the 38 versioned line-item golden
receipts with 43 deterministic records from the versioned local row cache. The
judge should supply the authoritative manifest for the one post-selection
capture.

Capture omits the wall-clock capture time, sorts every receipt/item/query, and
rounds vectors and distances to eight decimal places. To compare two captures
made minutes apart, use:

```bash
python scripts/similarity_harness/capture_golden.py \
  --manifest path/to/manifest.json \
  --compare-to first.json \
  --out second.json
```

The comparison requires identical receipt, corpus, query, neighbor, metadata,
merchant-decision, tier, and section-vote identities. It ignores captured
latency and permits only absolute distance drift up to `1e-6` and vector drift
up to `1e-7`. Both tolerances are configurable. Any neighbor reordering or
decision change fails the command.

For an entirely offline byte-stability check:

```bash
python scripts/similarity_harness/capture_golden.py \
  --offline-bootstrap --out first.json
python scripts/similarity_harness/capture_golden.py \
  --offline-bootstrap --compare-to first.json --out second.json
cmp first.json second.json
```

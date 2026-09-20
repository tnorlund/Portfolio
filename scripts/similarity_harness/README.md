# Similarity evaluation harness

Round A froze the original vector store's retrieval behavior before later
rounds replaced the backend. The fixture contains one merchant, one
sampled-word, and one section query for every receipt. It also contains the
query and neighbor vectors needed by the exact offline fake. Receipt text,
phone numbers, and addresses are not stored.

The live capture script was retired with the original vector store; the
committed `tests/fixtures/similarity/golden.json` is the frozen reference.

## Offline verification

The committed fixture exercises all schema, coverage, scoring, and runtime
paths without touching AWS:

```bash
python scripts/similarity_harness/evaluate.py \
  --backend golden \
  --out scorecard.json

python scripts/similarity_harness/evaluate.py \
  --backend fake \
  --out fake-scorecard.json
```

`--backend golden` is a replay of the captured answers. It is deliberately
offline: scoring the reference against itself must produce recall and consumer
agreement of 1.0 before that fixture is allowed to grade another backend.
`--backend fake` performs exact cosine nearest-neighbor search over the fixture
corpus. `--backend dynamo` loads the DynamoDB implementation, optionally
through `VECTOR_CLIENT_FACTORY=module:callable`, and measures wall latency and
request units.

Evaluation emits no current timestamp or runtime value. With a fixed fixture
and backend response it is byte deterministic. Fake evaluation records zero
latency and request units; Dynamo evaluation records wall latency and consumes
optional `last_request_units` or `get_last_search_metrics()` telemetry from the
backend without expanding the two-method consumer protocol.

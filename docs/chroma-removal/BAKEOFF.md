# Bake-off: the vector engine (cards C + E1)

Identical instruction packet for all four competitors (Claude, Codex, Grok
Build, Cursor). Read [SPEC.md](SPEC.md) §3 and [AGENT_PLAN.md](AGENT_PLAN.md)
first. The foundation branch (harness + `receipt_embeddings` + dev vector
indexes) is your base — do not modify it, do not create or alter vector
indexes, do not touch prod.

## Deliverable (one branch, one draft PR against the foundation branch)

1. Embedding-item entities per SPEC §3.1 (`RECEIPT_LINE_EMBEDDING`,
   `RECEIPT_WORD_EMBEDDING`) with accessors in the established
   `receipt_dynamo` style.
2. Embed-and-put writer in `receipt_embeddings`: OpenAI realtime →
   BatchWriteItem, idempotent, poison-tolerant.
3. Backfill script: golden receipts → embedding items on dev; safe to re-run
   (second run writes nothing).
4. Merchant resolution ported behind `VECTOR_BACKEND=dynamodb|chroma`
   (default `chroma`): swap the retrieval in
   `receipt_upload/merchant_resolution/resolver.py:1346` to
   `VectorSearchClient.search`; thresholds and tier logic UNCHANGED (SPEC
   §3.5a).

## Rules

- Code against `receipt_embeddings.vector_client.VectorSearchClient` — never
  boto3 SearchVectors directly in consumer code.
- Unit tests use `FakeVectorIndex`. Integration tests use marker
  `vector_integration` (skipped without AWS creds).
- Branch `bakeoff/<tool>`; draft PR; do not merge anything.
- Dev table writes only via your backfill script on the golden receipt set;
  clean up is handled by the judge, not you.

## Grading (identical for all)

Phase 1 (all four, offline):
```
python scripts/similarity_harness/evaluate.py --backend fake --out scorecard.json
```
Gates: neighbor recall@10 ≥ 0.9 vs golden fixtures; merchant agreement ≥ 98%;
tier distribution within ±5% of fixtures; unit suite green.

Phase 2 (top two, sequential on dev): backfill → `--backend dynamo` →
latency/cost recorded → items wiped by the judge.

Scorecard fields compared: recall@10, merchant agreement %, tier deltas,
p50/p95 latency (phase 2), est. $/query (phase 2), diffstat, new deps,
idempotency proof (backfill re-run output).

Done means the gates pass — not that the code "looks complete."

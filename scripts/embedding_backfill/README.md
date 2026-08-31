# Embedding backfill

Writes `RECEIPT_LINE_EMBEDDING` / `RECEIPT_WORD_EMBEDDING` items for the
golden receipts into the **dev** table (`ReceiptsTable-dc5be22`) — any
other table is refused. Only `…#EMBEDDING` items are ever written; no
non-embedding item is created, modified, or deleted.

```
python scripts/embedding_backfill/backfill_embeddings.py \
    [--manifest receipts.json] [--extra-receipts extras.json] [--limit N] \
    [--vector-source auto|chroma|openai|fixture] [--fixture golden.json] \
    [--wait-timeout 300] [--poll-interval 10] [--skip-wait] \
    [--report-out report.json]
```

The default receipt set is the Round A golden manifest (line-item golden
receipts + the supplemental local-cache cohort); `--manifest`,
`--extra-receipts`, and `--limit` follow the `capture_golden.py`
conventions exactly (the loader is shared).

## Vector sources

| Source | Needs | Notes |
|---|---|---|
| `chroma` | `CHROMA_CLOUD_API_KEY/TENANT/DATABASE` (database must be `receipt_dev`) | **OpenAI-free**: reuses the vectors already stored in Chroma Cloud (ids equal the item keys), preserving vector identity — OpenAI embeddings are not bit-stable across calls. Read-only against Chroma. |
| `openai` | `OPENAI_API_KEY` | Realtime `text-embedding-3-small` re-embed of only the missing items. |
| `fixture` | a captured fixture file (default `tests/fixtures/similarity/golden.json`) | Fully offline; covers only keys present in the fixture corpus, the rest are skip-reported. |
| `auto` (default) | — | chroma if credentialed, else openai if keyed, else a clear error. |

## Behavior

- **Idempotent**: existing embedding items are detected first, so a
  re-run embeds nothing and writes nothing (`written: 0` in the report —
  the phase-2 idempotency proof).
- **Skip-and-report**: a receipt that is absent or empty, an item with
  no vector, or a failed write is reported and skipped; the run never
  aborts. The run ends with a written/skipped/failure report
  (`--report-out` also emits JSON).
- **Searchability wait**: after writing, one sampled line item and one
  sampled word item are polled through `SearchVectors` until they come
  back (their own vector must return them) or `--wait-timeout` elapses.
  Indexing is asynchronous; a timeout is reported, not fatal.

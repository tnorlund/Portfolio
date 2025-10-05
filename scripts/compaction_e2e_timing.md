### ChromaDB compaction end-to-end timing (to EFS)

This document summarizes observed end-to-end timings for recent uploads (dev stack), using EFS promotion as the endpoint. Times are derived from CloudWatch logs via `scripts/aggregate_pipeline_logs.py` and verified snapshot presence via `scripts/verify_chromadb_snapshot.py`.

### Inputs analyzed

- Image IDs (each has 2 receipts):
  - `6c7642fa-0f03-4dd7-bc55-e4adb32ca250`
  - `ef8492b4-4b1c-4b4a-90bc-5be7bad540ff`
  - `872099e0-dbbb-43e2-adeb-4673e872a309`

### Measured times (upload → EFS promotion)

Note: We use the first OCR-processing log as start; for end we use the compactor’s promotion window (proxied by the compaction handling window and immediate post-promotion enqueue of `efs_snapshot_sync`). Promotion itself is seconds; the bulk of time is queueing before compaction.

| Image ID                             | Upload start (OCR begins) | EFS promotion window | Approx. duration |
| ------------------------------------ | ------------------------- | -------------------- | ---------------- |
| 6c7642fa-0f03-4dd7-bc55-e4adb32ca250 | 05:40:27Z                 | ~05:44:44Z           | ~4m 17s          |
| ef8492b4-4b1c-4b4a-90bc-5be7bad540ff | 05:40:19Z                 | ~05:47:58Z           | ~7m 39s          |
| 872099e0-dbbb-43e2-adeb-4673e872a309 | 05:40:35Z                 | ~05:48:11–12Z        | ~7m 37s          |

Average observed duration: ~6m 31s per image (to EFS).

### What’s taking time

- Embedding/delta pipeline latency
  - `embed-from-ndjson` → ECS compaction worker → delta upload. Varies with ECS startup/slot availability and batching.
- Queueing to compaction (FIFO per collection)
  - Bursts within the same message group can cause head-of-line waiting before the compactor picks up the run.
- Compaction Lambda pickup cadence
  - SQS long-polling (20s) is minor but contributes up to ~20s worst-case pickup delay.
- Locking is no longer the bottleneck
  - Promotion lock is short; EFS swap is fast (seconds). Earlier contention was mitigated by scoping the lock to promotion and adding jittered retries.

### Evidence snippets (dev)

- Example compaction start for `6c7642fa…` (lines/words): ~05:44:36–05:44:44Z
- Example `Enqueued EFS snapshot sync` immediately after promotion (proxy for completion): ~05:44:43.935Z

### How to reproduce

Fetch and merge logs for a recent window (dev):

```bash
python scripts/aggregate_pipeline_logs.py \
  --pulumi-env dev \
  --pulumi-groups \
  --start "-90m" \
  --early-stop-empty-pages 3 \
  --summary-out /tmp/summary.csv \
  --per-receipt \
  --include-receipt-terms
```

Filter for a specific image ID and compaction start markers:

```bash
grep -E "Processing COMPACTION_RUN|Enqueued EFS snapshot sync" compactor-latest.csv \
  | grep -E "(6c7642fa-0f03-4dd7-bc55-e4adb32ca250|ef8492b4-4b1c-4b4a-90bc-5be7bad540ff|872099e0-dbbb-43e2-adeb-4673e872a309)"
```

Verify snapshot contents landed in S3:

```bash
python scripts/verify_chromadb_snapshot.py \
  --out-dir /tmp/chroma_snapshots \
  --collections lines,words \
  --image-id 6c7642fa-0f03-4dd7-bc55-e4adb32ca250 \
  --receipt-id 1
```

### Recommendations to reduce end-to-end time

- Increase/auto-scale ECS compaction worker capacity to reduce embedding/delta backlog.
- Ensure per-run SQS grouping and raise Lambda concurrency limits if constrained.
- Keep SQS long-poll at 20s; it lowers contention with negligible end-to-end impact.
- Add explicit "EFS promotion complete" log line to measure promotion completion precisely per run.

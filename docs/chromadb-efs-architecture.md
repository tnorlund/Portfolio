# ChromaDB Compaction Architecture (EFS-first, S3-mirrored)

## Goals

- Fast, ordered compaction without racing pointers
- Minimal network overhead for snapshot IO
- Durable, auditable backups in S3

## Components

- Upload Images (container Lambda): produces embeddings and writes deltas (tarball) to EFS
- Enhanced Compaction Handler (Lambda): merges deltas into ChromaDB snapshot on EFS, atomically promotes latest
- DynamoDB Streams → SQS → Compactor: metadata/label updates, and COMPACTION_RUN messages
- EFS: shared POSIX storage mounted at `/mnt/chroma`
- S3: periodic mirror of EFS snapshots for durability and distribution
- Locking: static per-collection mutex (`chroma-<collection>-update`) in DynamoDB

## Data Flow (EFS-first)

1. Upload path
   - `upload_images` container Lambda (or NDJSON embed worker) generates vectors
   - Writes delta directory to EFS as a single tarball (`delta.tar.gz`) under `/mnt/chroma/<collection>/delta/<run_id>/`
   - Adds a DynamoDB `CompactionRun` item (image_id, receipt_id, run_id, prefixes)
2. Enqueue
   - DynamoDB stream processor sends two SQS messages (lines, words) with FIFO MessageGroupId per run
3. Compaction
   - Enhanced compactor acquires static per-collection lock: `chroma-<collection>-update`
   - Loads current ChromaDB snapshot directly from EFS at `/mnt/chroma/<collection>/snapshot/latest/`
   - Merges EFS delta tarball into the local snapshot dir
   - Atomically promotes latest by updating a pointer file and/or using rename within EFS
4. Metadata/labels
   - Stream updates read the snapshot on EFS in metadata-only mode, update records, and atomically promote

## Atomic Promotion on EFS

- Versioned path: `/mnt/chroma/<collection>/snapshot/timestamped/<version_id>/`
- Pointer file: `/mnt/chroma/<collection>/snapshot/latest-pointer.txt`
- Promotion: write new version to timestamped path, validate lock, update pointer (single write), optionally rename a `latest` symlink/dir

## S3 Mirroring

- Purpose: backup, disaster recovery, external consumers
- Strategy options:
  - Scheduled export: EventBridge rule (e.g., every 10 minutes) triggers exporter Lambda
  - Event-driven export: compactor emits a small SQS message after promotion to trigger exporter
  - DataSync task EFS→S3: fully managed, incremental sync
- Exporter steps:
  - Read `latest-pointer.txt` on EFS for each collection
  - Upload versioned snapshot directory recursively to `s3://<bucket>/<collection>/snapshot/timestamped/<version_id>/`
  - Write/update pointer file in S3 (`latest-pointer.txt`)
  - Optionally compute and upload `.snapshot_hash` for integrity validation

## Locking & Ordering

- SQS: FIFO queues with MessageGroupId per run to preserve intra-run ordering
- Compactor: static per-collection lock prevents concurrent merges for the same collection
- Partial-batch failures: Lambda returns `batchItemFailures` on lock conflict; SQS visibility timeout governs retry backoff

## Performance Notes

- EFS avoids S3 per-object PUT/LIST/GET overhead; expect 2–10x faster compactions for small-file-heavy snapshots
- Keep Lambda in VPC for EFS; consider provisioned concurrency for critical paths
- Batch size 10 in EventSourceMapping; parallelism achieved across collections and runs (subject to lock)

## Failure Handling

- If lock acquisition fails: return partial-batch failure; SQS will retry
- If exporter fails: next run or schedule retries; DataSync can provide robust incremental retries
- Backups: enable AWS Backup for EFS; S3 copy serves as secondary backup

## Migration Plan (incremental)

1. Provision EFS + access point; mount `/mnt/chroma` in compactor and upload Lambdas
2. Switch snapshot and delta paths to EFS; keep S3 code for export
3. Add static per-collection lock in compactor (done)
4. Add exporter (EventBridge schedule or DataSync)
5. Cut over consumers to read from EFS when possible; fall back to S3 mirror where needed

## Environment & Configuration

- EFS mount: `/mnt/chroma`
- Lock: `chroma-<collection>-update`, duration configurable via `LOCK_DURATION_MINUTES`
- Streams/Queues: FIFO with MessageGroupId per run; dedup ids required for producers
- Exporter config: S3 bucket name, schedule (cron), hash algorithm (md5/sha256) if needed

## Open Questions

- Do all consumers reside in the same VPC to read from EFS? If not, keep S3 mirror authoritative for those consumers
- Mirroring frequency vs RPO/RTO requirements
- Hashing strategy performance tradeoffs

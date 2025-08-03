# Chroma → S3 Compaction Pipeline Design

---

## 1 . Objectives

| Goal                                       | Why it matters                                                                   |
| ------------------------------------------ | -------------------------------------------------------------------------------- |
| **Move off Pinecone**                      | Eliminate SaaS cost and vendor lock-in.                                          |
| **Keep everything “serverless”**           | Align with existing Pulumi/Lambda pattern—zero EC2/EKS.                          |
| **Safe multi-writer ingestion**            | Producers drop deltas freely; snapshot integrity is never at risk.               |
| **Idempotent, crash-resilient compaction** | A single failed run can’t corrupt data; the next run picks up where it left off. |
| **One-click deploy per stack**             | Follow the current _infra/_ Pulumi convention (dev/staging/prod).                |

---

## 2 . High-Level Data Flow

1.  **Producer Lambda / API**

    - Embeds docs.
    - Writes a temporary Chroma DB dir (`/tmp/chroma_delta`).
    - `aws s3 cp` → `s3://<bucket>/delta/<timestamp>/…`.
    - Sends SQS **FIFO** message `{ "delta_key": "delta/2025-08-02T08-05-27Z/" }`.

2.  **Event Source Mapping**—SQS ➜ **`compactor_lambda`** (batch size = 10).

3.  `compactor_lambda` steps:

    try acquire lock in DynamoDB → if busy → return
    download snapshot/latest/ \
    download all queued deltas > merge → persist()
    upload snapshot/<new-ts>/ /
    copy snapshot/<new-ts>/ → snapshot/latest/
    delete lock
    delete SQS messages

        4.	Query Lambda mounts EFS read-only at /mnt/chroma (Populated by a nightly DataSync job that mirrors snapshot/latest/).

⸻

## 3 . New/Updated Pulumi Resources

| Resource               | File                         | Stack-scoped name suggestion | Notes                                                                                                                   |
| ---------------------- | ---------------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| S3 Bucket for vectors  | `infra/storage.py`           | `vectorsBucket`              | Versioning ON. Two prefixes: `snapshot/` & `delta/`.                                                                    |
| SQS FIFO Queue         | `infra/queues.py`            | `deltaEventsQueue`           | Content-based dedup OK. DLQ with 5 retries.                                                                             |
| EventBridge Rule (alt) | skip if using SQS directly   | —                            | S3 → SQS glue if you want S3 events not SDK publishes.                                                                  |
| DynamoDB table update  | `infra/dynamo.py`            | existing `receipt_table`     | Add `TYPE = COMPACTION_LOCK` item, plus TTL on `expires`. No new GSIs needed if GSI1 already (PK=LOCK, SK=EXPIRES#…).   |
| `compactor_lambda`     | `infra/lambdas/compactor.py` | `compactorFn`                | Python 3.12, 512 MB, 15 min timeout. Environment: `TABLE_NAME`, `BUCKET`. Add `chroma` & `faiss-cpu` to a Lambda Layer. |
| IAM Role updates       | `infra/iam.py`               | —                            | Allow: `dynamodb:PutItem/DeleteItem`, `s3:GetObject/PutObject/ListBucket`, `sqs:DeleteMessage`.                         |
| CloudWatch Alarms      | `infra/monitoring.py`        | `pendingDeltasAlarm`         | Metric: `ApproximateAgeOfOldestMessage` > 300 s.                                                                        |

⸻

## 4. DynamoDB CompactionLock Entity (recap)

| Attribute | PK                 | SK     | Extra attrs                                            |
| --------- | ------------------ | ------ | ------------------------------------------------------ |
| Example   | `LOCK#chroma-main` | `LOCK` | `owner` (UUID), `expires` (ISO UTC), `heartbeat` (ISO) |

Conditional Put used by `_CompactionLock.acquire_compaction_lock()`:

```python
ConditionExpression="attribute_not_exists(PK) OR expires < :now"
```

TTL enabled on `expires` → stale locks self-delete.

⸻

## 5. compactor_lambda Pseudo-code (ready to port)

```python
def handler(event, ctx):
delta_keys = [json.loads(r["body"])["delta_key"] for r in event["Records"]]

    lock = CompactionLock(
        lock_id="chroma-main",
        owner=str(uuid.uuid4()),
        expires=datetime.utcnow() + timedelta(minutes=15)
    )

    try:
        lock_dao.acquire_compaction_lock(lock)
    except EntityAlreadyExistsError:
        return "busy"

    try:
        with tempfile.TemporaryDirectory() as workdir:
            snap_dir = Path(workdir) / "snapshot"
            download_prefix("snapshot/latest/", snap_dir)

            chroma = PersistentClient(path=str(snap_dir))
            col = chroma.get_collection("docs")

            for key in delta_keys:
                delta_dir = Path(workdir) / "delta"
                download_prefix(key, delta_dir)
                d = PersistentClient(path=str(delta_dir))
                data = d.get_collection("docs")._collection.get(
                    include=["documents", "embeddings", "metadatas", "ids"]
                )
                col.add(**data)

            chroma.persist()
            new_prefix = f"snapshot/{datetime.utcnow().isoformat()}/"
            upload_dir(snap_dir, new_prefix)
            copy_to_latest(new_prefix)
    finally:
        lock_dao.release_compaction_lock("chroma-main", lock.owner)
```

⸻

## 6 . How Collisions Are Prevented

1.  **Atomic acquisition**: Only the first `PutItem … ConditionExpression` succeeds.
2.  **SQS visibility**: Loser Lambda returns without deleting its messages → they reappear when the winner finishes.
3.  **Snapshot swap**: Writer uploads to timestamped folder, then copies to `snapshot/latest/` (or uses S3 "multipart copy replace")—readers see either old or new state, never partial.
4.  **TTL fail-safe**: If winner crashes, `expires` lets the next run take over after 15 min.

⸻

## 7. Folder Layout in S3

```
s3://vectorsBucket/
├─ snapshot/
│ ├─ 2025-08-02T00-00-00Z/
│ │ ├─ chroma.sqlite3
│ │ ├─ embeddings.parquet
│ │ └─ index/...
│ └─ latest/ # pointer (copy) to most recent timestamp
└─ delta/
└─ 2025-08-02T08-05-27Z/
└─ ... (same 3 files)
```

⸻

## 8. Pulumi Wiring Checklist (per stack)

1.  Add new bucket & queue:
    ```python
    vectors_bucket = s3.Bucket("vectorsBucket", versioning_enabled=True)
    delta_queue = sqs.Queue("deltaEventsQueue",
        fifo_queue=True,
        content_based_deduplication=True)
    ```
2.  Grant S3 → SQS notifications (optional if producers send directly).
3.  Define `compactor` Lambda with an event source mapping to the FIFO queue.
4.  Export critical names/ARNs:
    ```python
    pulumi.export("vectorsBucketName", vectors_bucket.id)
    pulumi.export("deltaQueueUrl", delta_queue.id)
    ```

⸻

## 9. Future Enhancements

| Idea                                        | Benefit                                                             |
| ------------------------------------------- | ------------------------------------------------------------------- |
| Provisioned Concurrency on compactor_lambda | Eliminate VPC cold-start when merges are frequent.                  |
| DataSync → EFS One-Zone                     | Keeps query Lambdas hot without paying for multi-AZ EFS.            |
| Step Functions wrapper                      | Visual retry/back-off logic; easier ops.                            |
| S3 Object Lambda                            | Serve similarity queries directly from S3 Vector Buckets (preview). |

⸻

## 10. Copy-paste Reference Tree

```
/infra
  storage.py        # vectorsBucket
  queues.py         # deltaEventsQueue
  lambdas/
    compactor.py    # 🔄 merge logic
  dynamo.py         # add CompactionLock TTL
  monitoring.py     # pendingDeltasAlarm
/docs
  chroma_compaction_plan.md <-- (this file)
/receipt_dynamo
  entities/compaction_lock.py
  data/_compaction_lock.py
```

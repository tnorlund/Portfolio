# Persistence comparison and review repair

Owner steering: deliver the private lunch first; compare the same failures
before stream work. The uncommitted lease/generation prototype was preserved
outside git at `/tmp/nutrition-b2-before-simplification` and replaced. It was
never deployed. B1 product revisions and conditional aliases remain committed.

## Recommendation

For lunch, compute from a private local input document with explicit label,
purchase and portion inputs. No receipt database or queue is required.
When bounded receipt persistence is useful, one atomic document containing
rows and summary is the smallest of the compared DynamoDB designs that passes
the tested failures. It needs a source fingerprint, an explicit current
context hash and compare-and-swap revision. It does not need generations,
leases, a cleanup protocol, or reverse indexes for this deliverable.

Do not replace the already-tested B1 immutable product rows just to make them
mutable. Lunch can store three pinned labels in its local input document;
it needs neither product history nor a catalog re-import protocol. Review
mutable product storage again only if real import/storage costs justify it.

## Same-case evidence

| Failure case | Prior generation prototype | Fixed rows + summary count + FIFO | Single document |
|---|---|---|---|
| Existing resegmentation sees nutrition | Reproduced rejection for bare summary and rows | Same TYPE problem | Both nutrition TYPEs now treated as derived; real plan/apply passes |
| Parent-only delete/recreate, lost event | Reproduced `stale=False` with changed parent | Parent existence alone also insufficient | Current parent content is in source fingerprint; old result stale and replaceable |
| Source lines replaced before event arrives | Reproduced `stale=False` | Count can remain unchanged | Strong source observations before save and during read detect completed rewrite |
| One writer rewrites while reader paginates | Staged active pointer protected rows (other bugs remained) | Reproduced mixed A/B rows with correct final count | Rows and summary are one atomic item |
| Failed refresh halfway through output writes | Old generation retained | Can expose missing/mixed rows; count only detects some | Failed transaction retains complete old document |
| Old worker after a newer correction | Lease conditions help | Normal FIFO helps; it is not a reader transaction | Expected opaque revision rejects obsolete write |
| Identical retry after lost response | Indistinguishable from lost lease | Repeat rewrite | Identical source/context/payload returns existing revision |
| Alias/facts/quantity correction | Required planned fan-out; read did not check current context | Requires fan-out or read check too | Changed caller-supplied current context marks stale without an event |
| Hot transaction contention | No retry for TransactionConflict | Still possible outside queue | Bounded jitter/backoff; exhaustion raises a retryable throughput error |
| Reader lease churn / reverse-page poison | Whole-manifest equality / one corrupt receipt aborts page | No lease churn | No lease or reverse-page API; individual malformed document raises explicitly |
| Orphan cleanup crash / cleanup deletes active row | More control rows and lease cleanup needed | Prefix purge possible | No staged rows or cleanup needed for reads; missing parent hides document |
| Oversize snapshot | Two JSON blobs failed late | Several items possible | Reject payload >300 KB and serialized item >380 KB before I/O |

Tests are executable reproductions, not claims of observed production
incidents. The FIFO/count counterexample uses actual paginated DynamoDB
queries in moto and one writer, so queue writer ordering does not prevent it.
AWS documents that a Query as a whole is not an atomic multi-item snapshot:
[DynamoDB transaction isolation](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html).
FIFO groups block later messages while one is in flight, but visibility
expiry and at-least-once delivery still matter:
[SQS visibility](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html).
No live queue was provisioned or tested.

## Boundaries and remaining limitations

- The full current parent item and ordered line items define the source.
  Two equal strong observations detect observed churn, not a linearizable
  source snapshot. A writer can pause between delete and insert. Existing
  line writers have no transactionally maintained generation marker; none of
  these nutrition-only designs proves a transiently partial source complete.
  Stream integration stays deferred rather than adding that protocol now.
- The save result is conservatively stale until the read API validates it.
  A rewrite after the final prewrite check is detected by the next read.
  A byte-for-byte recreation with identical parent/source values is
  indistinguishable without a source incarnation marker; its calculation
  inputs are identical. Changed timestamp OR other parent content is tested.
- `context` is required on reads and writes. The private caller supplies the
  current pinned labels, explicit aliases/quantity overrides and calculator
  version. This is not automatic alias lookup/fan-out; a caller passing old
  context cannot claim correction propagation. Lunch always recalculates.
- This is bounded private persistence, not a scalable reverse-lookup store.
  Oversize documents fail closed. No migration from the abandoned prototype
  is needed because it was uncommitted and never deployed.
- A corrupt individual document raises validation error. There is no batch
  endpoint whose unrelated receipts can be poisoned. Do not add one without
  per-receipt failure handling.
- Writes remain inside receipt_dynamo, with the existing explicit table guard.
  No production/dev data, index, queue or infrastructure was changed.

## Local evaluation

- Before repair: 2/2 resegmentation cases failed at plan creation; 2/2
  recreated-parent/source-rewrite freshness cases failed (`stale=False`).
- After repair: all 23 resegmentation tests pass, including plan/apply churn
  and cleanup of the source nutrition rows.
- 57 targeted tests pass: 22 single-document/comparison integration cases,
  34 B1 integration regressions and one entity boundary test.
- Test commands and review status are in [EVALUATION.md](EVALUATION.md).

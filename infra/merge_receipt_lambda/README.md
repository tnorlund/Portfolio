# Merge retries

Invoke the Lambda with `image_id`, two distinct positive `receipt_ids`, and
optional `dry_run`. A dry run does not create a journal or write any data.
Callers must check the returned `status`; an error is not a completed merge.

The unordered source pair identifies one operation. Retrying `[2, 1]` resumes
the same output as `[1, 2]`, including when one or both sources are already
deleted. A completed duplicate returns the original result without writing.

Progress lives in the existing table's image partition:

- `MERGE#<first>#<second>` records the reserved output ID, execution claim,
  response, source object references, and `PREPARING`, `READY`, or `COMPLETED`.
- `MERGE_LOCK` serializes active invocations on one image so count writes
  cannot arrive out of order.
- `MERGE_OUTPUT#<id>` prevents two merges from reserving the same output ID.
- `MERGE_SOURCE#<id>` prevents overlapping pairs from consuming one source.

`PREPARING` retries rebuild the reserved output from committed source reads.
The output's parent is conditionally claimed before writing its S3 paths.
An unrelated receipt using that ID is never overwritten; if one appears
before the output is staged, the retry re-mints the next free ID and swaps
the `MERGE_OUTPUT#` reservation in the same transaction. The output row
carries `merge_operation`, and `Receipt` round-trips it, so ordinary
read-modify-write updates do not strip the marker. Source deletion starts
only after the output data, images, native embeddings, and `READY` checkpoint
are durable. `READY` retries resume child/object cleanup, derived queue sends,
and the image count update without reloading source geometry. Each failure
remains an error until the operation can finish.

The 15-minute execution lease exceeds the Lambda's hard 10-minute timeout.
An active duplicate returns an error without changing the owner's work.
Caught failures release the lease immediately; an interrupted invocation can
be retried after its lease expires. The owner and lease fence output writes, image count writes,
and state transitions. Completion releases the image lock atomically. Do not increase the Lambda timeout beyond the lease.

Reservations and completed journals have no TTL because duplicates can arrive
after source deletion. A failed operation retains its reservations, which also
block each source from any other pair; repair the cause and retry the same
pair. Queue sends may repeat during recovery, which is supported by the
existing idempotent recompute handlers.

## Abandoning a pair that must not be retried

`DynamoClient.abandon_receipt_merge(image_id, receipt_ids)` is the supported
way to free a `PREPARING` pair (for example after one source was deleted or
re-segmented). It refuses a live lease unless called with that lease's
`owner`, refuses `READY` and `COMPLETED` journals (finish those by retrying
the pair), fences the journal so a concurrent retry cannot re-claim it, then
removes in one transaction the `MERGE#<first>#<second>` journal, its
`MERGE_OUTPUT#` and two `MERGE_SOURCE#` reservations, the `MERGE_LOCK` when
this pair holds it, and the staged `RECEIPT#<output>` row plus children when
that row carries this pair's `merge_operation` marker. It returns the purged
staged `Receipt`, or `None`; delete that receipt's raw and CDN objects
yourself, because the data layer never touches S3. Do not delete these rows
by hand: a manual delete skips the fence and can race a retry.

This protocol coordinates merge invocations. It does not lock unrelated manual
edits or re-OCR writers, and paginated consistent reads are not a transaction
snapshot across concurrent edits. Partial outputs created before journals were
introduced cannot be attributed safely and are never automatically deleted.

Legacy per-image export/import/delete utilities enumerate known receipt data;
they do not back up, restore, or clear merge journals and reservations. They are
not a merge recovery path. Full table backups include these rows. Any future
owner-directed same-image reset or repair must account for the reservations.

The role uses `dynamodb:ConditionCheckItem` for transaction condition checks;
the existing `PutItem`, `UpdateItem`, and `DeleteItem` permissions govern the
transaction writes ([AWS transaction IAM documentation](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html)).

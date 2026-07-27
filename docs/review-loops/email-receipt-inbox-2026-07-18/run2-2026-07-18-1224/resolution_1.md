# Review Round 1 — Resolution

1. [med] DLQ unmonitored / "loses no mail" overstated — **FIXED (partial, on the merits).**
   Added a CloudWatch `MetricAlarm` on the parser DLQ's
   `ApproximateNumberOfMessagesVisible` so async invokes that exhaust retries
   or the 3600s age window surface for redrive instead of silently aging out at
   14-day retention. Exposed the queue via `self.dlq` + a `dlq_url`
   register_output (addresses "no exported queue identifier"). Corrected the
   misleading "loses no mail" comment to state the real durability model: the
   raw email is stored durably in S3 under `raw/` by the SES receipt rule
   *before* the async S3→Lambda trigger fires, so a dropped trigger event does
   not lose mail — it is redrivable from `raw/`.
   Declined the heavier "automated redrive/replay" and "place SQS between S3 and
   Lambda" alternatives on the merits: for a config-gated, default-off
   experimental component whose source data is already durable in `raw/`, an
   alarm + accurate durability model is the proportionate, contract-safe fix.
   Full automated redrive is a separate feature; inserting SQS between S3 and
   the parser would rewire the ingress path without adding durability beyond
   what `raw/` already provides.

2. [low] S3 invoke Permission omits `source_account` — **FIXED.**
   Added `source_account=account_id` to the `aws.lambda_.Permission`, so a
   bucket name reclaimed in another account after deletion cannot invoke the
   parser. Trivial one-line hardening.

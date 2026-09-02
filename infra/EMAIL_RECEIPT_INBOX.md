# Email receipt inbox and read replica

```text
iCloud/Gmail forward -> receipts@in.tylernorlund.com -> SES receipt rule
                     -> s3://email-receipt-inbox-mail-<stack>-<acct>/raw/     (archive)

Mac (~/receipts-email, the primary)
  emlrec pull-ses    downloads new raw/ objects, applies the SES trust gate,
                     parses with the ONE parser set, upserts into SQLite
  emlrec reconcile   matches receipts to Chase transactions
  emlrec replicate   VACUUM INTO snapshot -> gzip -> s3://.../replica/
                       email_receipts.db.gz + manifest.json

AWS Lambda email-receipt-inbox-mcp (zip, python3.13, stdlib + boto3)
  cold start: HEAD replica -> download -> gunzip to /tmp -> open read-only
  warm: re-HEAD at most once a minute, swap snapshots when the ETag changes
  -> /email/mcp on the shared Cognito gateway (scope portfolio-mcp/email)
```

The SQLite file on the Mac is the primary. AWS holds two things: the raw
mail archive SES writes, and a read replica of the primary that a tiny Lambda
serves over MCP. AWS never parses mail.

## What changed and why

The first revision (PRs #1181, #1218, #1224) ran a second copy of every
sender parser inside a Lambda that woke on each `raw/` object and wrote
`parsed/<id>.<sha256>.json`. Nothing consumed `parsed/`: the reconciliation
plane in `receipts-email` reads mbox exports and its own parsers. Measured on
the dev bucket (2026-07-23 → 2026-09-01): 1,183 messages, 92% classified
`non_receipt`, 64% of them GitHub notifications. Two copies of ~5,300 lines of
regex drifted independently.

Removed: the parser Lambda, its execution role and policies, the async
invoke config, the dead-letter queue and its alarm, the S3 → Lambda
notification and invoke permission, the `parsed/` lifecycle rule, and
`infra/email_receipt_inbox/lambdas/{handler.py,registry.py,parsers/}`.

Kept unchanged: the SES identity, DKIM and verification records, the receipt
rule set (the ATS verification inbox adds its rule to this set), TLS-required
store rule with scanning, the bucket, its public-access block, encryption,
versioning, and the bucket policy pinned to the exact receipt-rule ARN.

Added: a `replica/` prefix (with a seven-day noncurrent-version expiry so
nightly publishes don't accumulate forever), a read-only MCP Lambda whose
role can read `replica/*` and nothing else, and an `/email/mcp` route with
its own Cognito scope.

## The trust gate moved, it did not disappear

`emlrec pull-ses` applies the same fail-closed checks the deleted handler
applied: exactly one SES-added `Authentication-Results` header, DMARC `pass`
aligned with the visible `From` domain, explicit `PASS` virus verdict, spam
verdict not `FAIL`/`PROCESSING_FAILED`, and an `X-Original-From` claim that
does not disagree with the authenticated sender. Rejected messages are
counted and skipped; the raw object stays in S3 for inspection.

## Operating it

Publish (or refresh) the replica from the Mac:

```bash
cd ~/receipts-email
python3.12 -m emlrec.cli pull-ses --bucket email-receipt-inbox-mail-dev-681647709217
python3.12 -m emlrec.cli reconcile
python3.12 -m emlrec.cli replicate --bucket email-receipt-inbox-mail-dev-681647709217
```

`scripts/nightly_replica.sh` in that repo chains the three; the launchd
template `scripts/com.tnorlund.emlrec-replica.plist` runs it at 07:00, after
the 06:30 SimpleFIN sync.

Connect a client to the stack's `email_mcp_server_url` output with the
`mcp_oauth_interactive_client_id` (see `MCP_AUTH.md`). The first call on a
cold container downloads ~7 MB and takes a few seconds; `replica_status`
reports the manifest, ETag, and age so an agent can judge staleness.

## Tool surface

Read subset of `receipts-email/server.py`, same names and shapes:
`get_email_receipt_summaries`, `get_email_receipt`, `search_email_receipts`,
`list_email_merchants`, `get_spend_summary`, `get_coverage`, `get_unmatched`,
`ingest_status`, `query_sql` (SELECT/WITH only, 500 rows, 10 s budget), plus
`replica_status`. No write tool exists here; `confirm_match`,
`reject_match`, `mark_transaction`, `reconcile_chase`, `import_chase_csv`,
`ingest_mbox_index`, and `refresh_paper_snapshot` run on the primary and land
with the next `replicate`.

`lambdas/queries.py` is vendored verbatim from `receipts-email/emlrec/queries.py`;
`infra/tests/test_email_receipt_mcp.py` byte-compares the two when that
checkout is present.

## Limits

- Replica lag is however often `replicate` runs (nightly by default).
- The gateway integration window is 29 s; the function times out at 25 s.
- Reserved concurrency is 5. Each container holds one snapshot in `/tmp`.
- Prod does not enable `portfolio:email_receipt_inbox_enabled`; the inbox,
  replica and `/email/mcp` route exist on dev only.

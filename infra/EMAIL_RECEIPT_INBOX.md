# Email receipt inbox and agent-safe projection

```text
iCloud per-sender rules -> receipts@in.tylernorlund.com -> SES receipt rule
                        -> s3://email-receipt-inbox-mail-<stack>-<acct>/raw/  (archive, no expiry)

Mac (~/receipts-email, the primary)
  emlrec pull-ses            downloads new raw/ objects, applies the SES trust
                             gate, parses with the ONE parser set, upserts SQLite
  emlrec reconcile           matches receipts to Chase transactions
  emlrec replicate           VACUUM INTO snapshot -> gzip -> s3://.../replica/
                             (full replica: owner's machines only)
  emlrec publish-projection  spend + txn tables only -> validate -> gzip
                             -> s3://.../agent/spend.db.gz + manifest.json

AWS Lambda email-receipt-inbox-mcp (zip, python3.14, stdlib + boto3)
  IAM: GetObject/ListBucket on agent/* only (not raw/, not replica/)
  cold start: HEAD agent/ -> download -> gunzip -> VALIDATE contract -> open ro
  warm: re-HEAD at most once a minute, swap snapshots when the ETag changes
  -> /email/mcp on the shared Cognito gateway (scope portfolio-mcp/email)
```

The SQLite file on the Mac is the primary. AWS holds three things: the raw
mail archive SES writes, a full read replica under `replica/` that only the
owner's machines read, and the agent-safe projection under `agent/` that a
tiny Lambda serves over MCP. AWS never parses mail, and no agent-facing
surface can reach the primary, the replica, or raw mail.

What leaves the inbox is decided by iCloud Mail rules, one "Forward messages
from <sender>" rule per receipt sender (the same mechanism the ATS inbox uses
for the five Greenhouse senders). iCloud exposes those rules only in its web
UI; enrolling a new merchant means adding a parser in `receipts-email` and a
rule there. The `github.com` rule was the noise source (receipts and
notifications share `noreply@github.com`) and has been removed.

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

Added: `replica/` and `agent/` prefixes (each with a seven-day
noncurrent-version expiry so nightly publishes don't accumulate forever), a
read-only MCP Lambda whose role can read `agent/*` and nothing else, and an
`/email/mcp` route with its own Cognito scope.

### `raw/` is never expired

`raw/` is the only AWS archive of the mail, so the bucket has no lifecycle
expiry on it and `EmailReceiptInbox` is constructed without
`raw_retention_days`. The Mac copy under `~/receipts-email/mail/ses/` is a
working copy, not a verified independent backup. Any future expiry needs an
explicit retention decision backed by an inventory reconciliation against
that copy and a tested restore; only then pass `raw_retention_days=` at the
call site in `infra/__main__.py`.

### The agent-safe projection is the privacy boundary

Agents never see the primary or the full replica. `emlrec publish-projection`
(the last step of the nightly job) builds a fresh SQLite file containing
exactly two tables and uploads it under `agent/`:

- `spend(source, date, merchant_name, merchant_category, item_description,
  quantity, unit_price_cents, total_cents, receipt_total_cents, receipt_ref,
  currency)`: one row per email or paper receipt **item**. `receipt_total_cents`
  repeats on every item of a receipt, so receipt totals come from
  `SELECT DISTINCT receipt_ref, receipt_total_cents`, not from summing that
  column. Email and paper sources are not deduplicated against each other.
  `receipt_ref` is a 12-hex SHA-256 prefix of the message id for email and
  `image_id:receipt_id` for paper.
- `txn(txn_date, posting_date, merchant_canonical, category, amount_cents,
  txn_class, is_card_purchase, currency)`: every card transaction with signed
  integer cents, merchant/category only from the canonical mapping table
  (NULL when unmapped), no account, no raw descriptor.

Every row carries `currency` (ISO 4217; NULL for paper receipts whose
pipeline never recorded one). It is never coerced to USD: aggregate per
currency. The schema is versioned (`PRAGMA user_version`, the manifest's
`schema_version`, and `SCHEMA_VERSION` in both `emlrec/projection.py` and
`lambdas/mcp.py`; currently 2).

The Lambda validates every downloaded file against the same contract the
publisher enforces (exactly those tables, exactly those columns in order,
none of the forbidden column names such as `message_id`/`subject`/
`card_last4`/`description`, no views or triggers, matching version) and
refuses to serve anything if the check fails; there is no fallback to the
replica, which its role cannot read anyway. `query_sql` then runs under a
SQLite authorizer that allows reads of `spend` and `txn` only (not even
`sqlite_master`), on a read-only immutable connection, with a 10 s statement
budget, a row cap (default 500, ceiling 1000), a 256 KB response cap, and
JSON-safe values (integer cents, base64 for any BLOB literal).

`infra/tests/test_email_receipt_mcp.py` holds the producer/consumer contract
tests: the Lambda accepts exactly the published schema, rejects every
deviation, and, when a `receipts-email` checkout is available (default
`~/receipts-email`, override with `RECEIPTS_EMAIL_DIR`), runs the real
exporter against a synthetic primary and proves no identifier leaks through.

## The trust gate moved, it did not disappear

`emlrec pull-ses` applies the same fail-closed checks the deleted handler
applied: exactly one SES-added `Authentication-Results` header, DMARC `pass`
aligned with the visible `From` domain, explicit `PASS` virus verdict, spam
verdict not `FAIL`/`PROCESSING_FAILED`, and an `X-Original-From` claim that
does not disagree with the authenticated sender. Rejected messages are
counted and skipped; the raw object stays in S3 for inspection.

## Operating it

Publish (or refresh) the replica and the projection from the Mac:

```bash
cd ~/receipts-email
python3 -m emlrec.cli pull-ses --bucket email-receipt-inbox-mail-dev-681647709217
python3 -m emlrec.cli reconcile
python3 -m emlrec.cli replicate --bucket email-receipt-inbox-mail-dev-681647709217
python3 -m emlrec.cli publish-projection --bucket email-receipt-inbox-mail-dev-681647709217
```

`publish-projection --dry-run` prints the manifest it would upload without
touching S3. `scripts/nightly_replica.sh` in that repo chains the four; the launchd
template `scripts/com.tnorlund.emlrec-replica.plist` runs it at 07:00, after
the 06:30 SimpleFIN sync.

Connect a client to the stack's `email_mcp_server_url` output with the
`mcp_oauth_interactive_client_id` (see `MCP_AUTH.md`). The first call on a
cold container downloads under 1 MB and takes a second or two;
`replica_status` reports the manifest, ETag, age, schema, row counts, and
currencies so an agent can judge staleness.

## Tool surface

Two tools: `query_sql` (SELECT/WITH over `spend` and `txn` only; see the
boundary above) and `replica_status`. The local stdio server's receipt-detail,
search, merchant, coverage, unmatched-worklist, and ingestion tools are
deliberately not exposed here: their shapes depend on tables that are not in
the projection (`get_email_receipt` returns mailbox metadata). They stay on
the Mac, along with every write.

## Limits

- Projection lag is however often `publish-projection` runs (nightly).
- The gateway integration window is 29 s; the function times out at 25 s.
- Reserved concurrency is 5. Each container holds one snapshot in `/tmp`.
- Prod does not enable `portfolio:email_receipt_inbox_enabled`; the inbox,
  projection and `/email/mcp` route exist on dev only.

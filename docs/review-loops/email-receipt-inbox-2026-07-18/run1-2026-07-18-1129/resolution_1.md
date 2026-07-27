# Review Round 1 — Resolution

1. [high] FIXED — Hardened the SES→S3→Lambda creation graph. Added the
   `_amazonses.<subdomain>` verification TXT record + `DomainIdentityVerification`
   (identity is now actually verified before use; record stays on the `in.`
   subdomain). Retained resource handles and wired dependencies: the Lambda
   `depends_on` its IAM policies, the `BucketNotification` `depends_on` the
   invoke `Permission`, the `ReceiptRule` `depends_on` the identity verification
   and the bucket policy, and `ActiveReceiptRuleSet` `depends_on` the store rule
   (no briefly-empty active set).

2. [high] FIXED (partial) — Removed `Reply-To` from sender dispatch (free-form,
   spoofable); kept `X-Original-From`→`From` for the documented iCloud
   forwarding case. Added `_ses_auth()` to capture SES `X-SES-Spam-Verdict`,
   `X-SES-Virus-Verdict`, and SPF/DKIM/DMARC from `Authentication-Results` into
   the parsed output; spam/virus `FAIL` is now short-circuited to `non_receipt`
   so it never reaches reconciliation. DECLINED the SES-level hard reject/bounce
   on DKIM/DMARC alignment: this pipeline is designed around iCloud
   auto-forwarding, which rewrites the envelope and breaks SPF/DMARC *alignment*
   by construction (authenticated `From` becomes the forwarder, not chase.com),
   so an alignment-gated SES reject would drop exactly the legitimate forwarded
   receipts the feature ingests. The recorded verdicts let the private
   reconciliation plane gate on trust downstream instead.

3. [med] FIXED — `s3.get_object` and `s3.put_object` moved OUTSIDE the
   parse-error catch so throttling / transient GetObject / missing-object races
   re-raise and Lambda retries them (exhausted → DLQ) instead of being persisted
   as a permanent `parse_error` with a success return. Only deterministic
   email/parser validation now yields `parse_error`.

4. [med] FIXED — Added an SQS DLQ (14-day retention) plus a
   `FunctionEventInvokeConfig` on-failure destination (`maximum_retry_attempts=2`)
   and `sqs:SendMessage` in the execution role, so async invokes that exhaust
   retries are captured durably rather than discarded.

5. [med] FIXED — Enabled `BucketVersioning`; handler now reads `versionId`/
   `eTag`/`sequencer` from the event, fetches the exact `VersionId`, and records
   all three in the output. `ingest_id` is now a `sha256` content digest
   (immutable, not sender-controlled); the RFC `Message-ID` is retained
   separately. Added `s3:GetObjectVersion` to the role.

6. [med] FIXED (partial) — Registered `costco` (costco.com, costco.com.mx →
   `parse_costco.parse`) and `travel-housing` (airbnb.com, tesla.com,
   hellolanding.com, landing.com → `parse_travel_housing.parse`); added a
   library `parse(path)` entry point (via `_dispatch`) to the travel module so
   the registry can call it without argparse. DECLINED vendoring `pypdf`:
   `parse_costco._parse_gas_pdf` already degrades gracefully when `pypdf` is
   absent (`PdfReader is None` → `needs_ocr`/note), which is the reviewer's own
   suggested alternative (route Costco PDFs to OCR without it). Registry now has
   16 groups.

7. [med] DECLINED — Single-owner SES topology is a deployment decision, not a
   mechanical fix. The component is instantiated only behind
   `portfolio:email_receipt_inbox_enabled` (default false; no stack sets it), the
   rule-set name is already stack-scoped (`f"{name}-{stack}"` → distinct sets per
   stack), and the module docstring already CAUTIONS that SES allows one active
   rule set per account+region. The only true singleton is activation, which the
   caution documents; deciding which stack owns `in.tylernorlund.com`
   identity/MX/activation is an ops/topology call that can't be resolved in code
   without picking that owner, and doing so unilaterally would violate the
   config-gated-instantiation contract.

8. [med] FIXED — `classify()` no longer returns `txn_signal` unconditionally for
   `chase-alerts`. It now requires a recognized `alert_type`, a `direction`, and
   a non-null `grand_total`; marketing/security/malformed/spoofed Chase mail the
   parser could not classify returns `non_receipt`.

9. [med] FIXED — Set `tls_policy="Require"` on the receipt rule (was defaulting
   to Optional/opportunistic TLS for financial mail).

10. [low] FIXED (partial) — When `raw_retention_days` is configured, the
    lifecycle now covers BOTH `raw/` and `parsed/` and expires noncurrent
    versions (needed now that versioning is on). DECLINED hard-coding a default
    retention duration (data-governance decision — mechanism stays opt-in) and
    the object-count/bytes CloudWatch alarms (non-trivial for a [low]; advisory).

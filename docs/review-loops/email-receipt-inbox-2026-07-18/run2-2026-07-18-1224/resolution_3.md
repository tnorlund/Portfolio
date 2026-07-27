# Review Round 3 — Resolution

1. [high] Sender authentication recorded but never enforced; X-Original-From
   trust; prefer synchronous SES reject-before-S3 — **ESCALATED (deadlock).**
   This is substantively the same public-ingress trust dispute already escalated
   to a human as `HUMAN.md` item 6 [deadlock]: whether to *enforce* sender
   authentication / reject unapproved or X-Original-From-forwarded mail at
   ingress before populating `receipt`, versus the current design which records
   the SES SPF/DKIM/DMARC + spam/virus verdicts and defers the trust decision to
   the private reconciliation plane (handler.py `_ses_auth`, docstring on
   `_from_domain`). Per the loop rules I neither implement nor re-argue a
   deadlocked item; it remains a human decision. No code change.

2. [med] `pypdf` not packaged → valid Costco gas receipts silently classify as
   `non_receipt`, metadata discarded — **FIXED.**
   Genuine integration break (the design-contract exception permitting a parser
   edit): `parse_costco.py` was written where `pypdf` is present ("present on
   this machine"), but the managed Lambda runtime ships Boto3 only, so
   `PdfReader is None` and the gas-ticket branch returned a record with
   `grand_total=None` → `classify` → `non_receipt`, dropping the real receipt.
   Applied the reviewer's minimal, contract-safe alternative (== HUMAN.md item 7's
   suggested split): a truly-missing PDF still returns as-is, but a present PDF
   with no `pypdf` available now sets `needs_ocr=True`. `classify` maps that to
   `needs_ocr`, and the handler already persists `receipt` for `needs_ocr`
   (round 2), so the base-record metadata (merchant, date) survives and the
   message stays actionable. Verified end-to-end. Writes stay confined to
   `parsed/`; no dependency/infra change, so the artifact remains LAMBDA_DIR-only.

3. [low] Unconditional PutObject into a versioned bucket accumulates parsed-
   object versions; use `IfNoneMatch="*"` + revision-keyed identity —
   **DECLINED (advisory).**
   Not trivial, and the trivial form is actively harmful. The destination key is
   already content-addressed (`ingest_id` = sha256 of the raw body), so a given
   body + code version always yields identical bytes; the only accretion is
   duplicate S3 notifications / Lambda retries writing the same content. Adding
   `IfNoneMatch="*"` *without* a revision component would 412 on any legitimate
   re-parse (same key) and silently defeat the documented "re-parses are a matter
   of re-running over raw/" workflow (handler.py header). Doing it safely needs a
   parser/schema-revision component in the key, changing the key scheme and the
   downstream poller's contract — beyond a [low]'s trivial bar. The "retained
   indefinitely" concern is separately mitigated: the existing lifecycle rule
   (infrastructure.py `expire-parsed`) sets `noncurrent_version_expiration` when
   retention is configured, so superseded versions age out. Left as advisory.

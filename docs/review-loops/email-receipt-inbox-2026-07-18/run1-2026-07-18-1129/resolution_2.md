# Review Round 2 — Resolution

1. [med] FIXED — Hardened the delivery-vs-storage creation ordering. Captured
   the `BucketVersioning` and `BucketNotification` handles. `BucketLifecycleConfiguration`
   now `depends_on=[versioning]` (noncurrent-version expiry is meaningless on an
   unversioned bucket). `ActiveReceiptRuleSet` now `depends_on=[store_rule,
   versioning, notification]`. The inbound MX record was moved to the END of
   `__init__` and `depends_on` the activation (when `activate=True`) plus
   `store_rule`, `versioning`, and `notification` — so mail can only be accepted
   once the versioned bucket, the S3→Lambda trigger, and the active rule set all
   exist.

2. [med] FIXED — Parsed objects are now keyed by content digest:
   `parsed/<name>.<ingest_id>.json` instead of `parsed/<name>.json`. Two versions
   of the same raw S3 key (a replay of the same messageId) can no longer collide
   on one parsed object, so an unordered older replay cannot clobber a newer
   parse. Updated the module docstring to require downstream idempotency on
   `ingest_id` (the sha256 content digest) rather than the sender-controlled
   `message_id`.

3. [med] FIXED — Integration break between registry dispatch (canonical
   `X-Original-From`) and the per-domain parsers (which re-dispatch on the
   message's own `From`, rewritten to the forwarder in the iCloud case). When
   `X-Original-From` is present, the handler now normalizes ONLY the parser's
   temp `.eml` copy — rewriting its `From` to the original sender — so
   `parse_retail`/`parse_services`/`parse_pos_restaurants` etc. dispatch to the
   correct brand instead of falling to the empty else branch. The stored `raw/`
   evidence is untouched; parsers themselves are unchanged.

4. [med] FIXED — Content scanning now fails CLOSED. New `_content_rejected()`
   helper: virus verdict must be an explicit `PASS` (GRAY / PROCESSING_FAILED /
   FAIL / missing all quarantine), and spam `FAIL` or `PROCESSING_FAILED`
   quarantine (spam `GRAY` borderline still allowed through with the verdict
   recorded). Rejected mail gets a new `quarantine` classification, distinct
   from parser-determined `non_receipt`, and never populates `receipt`. The
   reviewer's preferred receipt-action-metadata source is a different trigger
   architecture (this Lambda is S3-triggered and only sees stored headers); the
   "at least" header-based gate they requested is implemented.

5. [low] FIXED — Gave the receipt rule set and rule deterministic physical
   names (`<name>-<stack>`, `<name>-store-<stack>`) and added an
   `aws:SourceArn` StringEquals condition (alongside the existing
   `aws:SourceAccount`) pinning the SES `s3:PutObject` grant under `raw/` to
   that one receipt rule, closing the account-wide confused-deputy gap.

6. [deadlock] ESCALATED — Public-ingress trust / provenance policy. Per loop
   protocol, deadlock findings are not implemented or argued here; escalated to
   a human.

7. [deadlock] ESCALATED — Costco gas-ticket `needs_ocr` on missing `pypdf`.
   Per loop protocol, deadlock findings are not implemented or argued here;
   escalated to a human. (Note: also constrained by the "parsers untouched
   unless integration break" design contract.)

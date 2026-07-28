# Review Round 2 — Resolution

1. [med] S3 first-time versioning propagation window before activation/MX — **DECLINED (on the merits).**
   The cited AWS caveat ("may take 15 minutes") concerns *enabling versioning on
   a bucket that already holds objects* — during that window pre-existing objects
   can still return a null version id. It does not apply here:
   - The bucket is created fresh with versioning Enabled at creation
     (`{name}-mail-versioning`), before any object can exist. There is no
     pre-existing-object lineage to propagate.
   - S3 has been strongly read-after-write consistent since Dec 2020, so a GET of
     an object just written to a versioned bucket does not race a NoSuchKey.
   - The first write cannot occur until the MX record resolves, and MX is
     published LAST with `depends_on=[store_rule, versioning, notification,
     activation]`. Real-world MX/DNS propagation plus SES's own delivery latency
     is minutes-to-longer — far exceeding any config-propagation window — so
     versioning is unambiguously live before mail #1 lands.
   - The handler already degrades gracefully: `version_id = obj.get("versionId")`
     and it only passes `VersionId` when truthy, otherwise fetching the current
     object. An event lacking a versionId therefore cannot produce NoSuchKey.
   A hard 15-minute Pulumi barrier has no clean primitive (it would be a sleep
   hack) and would add deploy friction to a config-gated, default-off
   experimental component with zero durability benefit given the above.

2. [med] `needs_ocr` parser output discarded (Apple PDF fields lost) — **FIXED.**
   `parse_apple.parse_retail_stub` extracts `merchant_name`, `notes` (PDF
   attachment name), `date`, and `order_id` before flagging `needs_pdf=True`,
   which `classify` maps to `needs_ocr`. The handler persisted `parsed` into
   `out["receipt"]` only for `receipt`/`txn_signal`, silently dropping those
   already-extracted fields. Added `needs_ocr` to that set so the parser's raw
   schema is retained for OCR-pending messages too; the classification still says
   `needs_ocr`, so downstream knows to OCR while gaining (not re-deriving) the
   fields the parser already found. Handler-only change — parsers untouched,
   writes remain confined to `parsed/`.

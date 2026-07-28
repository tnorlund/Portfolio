# Review Round 3 — Resolution

1. [med] FIXED — Captured `FunctionEventInvokeConfig` as `async_config` and added it to the S3 `BucketNotification` `depends_on`. Since the rule-set activation and MX record both depend on the notification, this propagates readiness to the entire mail-accepting graph — no delivered message can invoke the parser before the DLQ failure destination exists. Also added `maximum_event_age_in_seconds=3600` to bound the retry window.

2. [med] FIXED — Added a pre-fetch size guard in `handler.py` (`MAX_RAW_BYTES = 15 MB`): oversized payloads are quarantined and recorded under `parsed/` (identity from the S3 ETag) without ever loading the body into memory. Set `reserved_concurrent_executions=10` on the parser to cap its slice of shared account concurrency (throttled async S3 invokes retry, so no mail is lost), and added `maximum_event_age_in_seconds=3600`.

3. [med] FIXED — `registry.classify` now honors parser-provided semantics: Uber messages with `_trip_summary_no_payment` ("This is not a payment receipt") map to `non_receipt`; Venmo `transaction_kind` of `p2p_sent`/`p2p_received` map to `txn_signal`. `merchant_purchase` falls through to the normal receipt path. Parsers untouched — only the integration classifier reads the hints they already emit.

4. [med] FIXED — Narrowed the handler's `except` from bare `Exception` to `(ValueError, KeyError, IndexError, AttributeError, TypeError, email.errors.MessageError)`. Deterministic parse/validation failures still record `parse_error`; `ImportError` (broken deploy), `OSError` (temp-file/disk), and any other unexpected exception now propagate to Lambda retry handling and, once exhausted, the DLQ.

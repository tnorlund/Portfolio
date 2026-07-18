"""S3-triggered parser for inbound receipt emails.

SES writes each inbound message to s3://<bucket>/raw/<messageId>. This handler
parses it through the sender registry and writes a result document to
parsed/<messageId>.<ingest_id>.json:

    {
      "message_id": "...",        # RFC-822 Message-ID (falls back to S3 key)
      "ingest_id": "...",         # sha256 of the raw bytes — the stable, non-
                                  # spoofable identity keying the parsed object
      "s3_key": "raw/...",
      "from_domain": "doordash.com",
      "original_from": "...",     # unwrapped when the mail arrived via a
                                  # forwarding rule (X-Forwarded-For et al.)
      "subject": "...",
      "group": "doordash" | null,
      "classification": "receipt" | "txn_signal" | "needs_ocr" | "non_receipt"
                        | "quarantine" | "unknown_sender" | "parse_error",
      "receipt": {...} | null,    # the parser's raw schema output
      "error": "..." | null
    }

The private reconciliation plane polls parsed/ and MUST ingest idempotently by
``ingest_id`` (the content digest): S3 notifications are unordered, so keying by
message_id/S3 key alone would let an older replay clobber a newer parse. Each
distinct message body maps to exactly one parsed object. This function
deliberately writes derived data only — no DynamoDB coupling — so re-parses are
a matter of re-running over raw/.
"""
from __future__ import annotations

import email
import email.errors
import email.policy
import email.utils
import hashlib
import json
import os
import re
import tempfile
import traceback
import urllib.parse

import boto3

import registry

s3 = boto3.client("s3")

# SES delivers S3-stored messages up to 40 MB, but receipt emails are a few
# hundred KB. This 256 MB function holds the raw bytes, the parsed MIME tree, a
# normalized copy, and parser-specific representations at once, so a large
# payload can OOM. Reject anything oversized BEFORE fetching it into memory.
MAX_RAW_BYTES = 15 * 1024 * 1024


def _from_domain(msg) -> tuple[str, str]:
    """Return (from_addr, domain) for sender dispatch.

    ``Reply-To`` is deliberately NOT consulted: it is free-form and often
    points marketing/spoofed mail at an unrelated domain. ``X-Original-From``
    is honored only for the documented iCloud auto-forwarding case (the
    forwarder rewrites ``From`` to itself but preserves the original sender
    here); the SES authentication verdicts recorded alongside let the
    downstream reconciliation plane decide how much to trust it.
    """
    for header in ("X-Original-From", "From"):
        raw = msg.get(header)
        if not raw:
            continue
        addr = email.utils.parseaddr(raw)[1]
        m = re.search(r"@([\w.\-]+)", addr or "")
        if m:
            return addr, m.group(1).lower()
    return "", ""


def _ses_auth(msg) -> dict:
    """Extract the SES-stamped spam/virus verdicts and SPF/DKIM/DMARC results.

    SES writes these headers into the stored message; scan_enabled only
    *produces* them, it never rejects, so the pipeline must gate on them.
    """
    spam = (msg.get("X-SES-Spam-Verdict") or "").strip().upper() or None
    virus = (msg.get("X-SES-Virus-Verdict") or "").strip().upper() or None
    ar = " ".join(msg.get_all("Authentication-Results") or [])
    auth = {}
    for mech in ("spf", "dkim", "dmarc"):
        m = re.search(rf"\b{mech}=(\w+)", ar)
        if m:
            auth[mech] = m.group(1).lower()
    return {"spam_verdict": spam, "virus_verdict": virus, "auth": auth}


def _content_rejected(spam, virus) -> bool:
    """Fail CLOSED on indeterminate SES content scans.

    SES emits PASS / FAIL / GRAY / PROCESSING_FAILED, and a verdict may be
    absent entirely if the message could not be scanned. Only an explicit
    ``PASS`` clears the virus scan — GRAY, PROCESSING_FAILED, FAIL, or a missing
    verdict all mean SES never affirmed the payload is clean, so quarantine.
    For spam, an explicit FAIL is spam and PROCESSING_FAILED means SES could not
    scan (e.g. malformed MIME); GRAY (borderline) is allowed through with the
    verdict recorded so downstream can weigh it.
    """
    if virus != "PASS":
        return True
    if spam in ("FAIL", "PROCESSING_FAILED"):
        return True
    return False


def lambda_handler(event, _context):
    results = []
    for record in event.get("Records", []):
        obj = record["s3"]["object"]
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(obj["key"])
        version_id = obj.get("versionId")
        out = {"s3_key": key, "version_id": version_id,
               "etag": obj.get("eTag"), "sequencer": obj.get("sequencer"),
               "group": None, "classification": "unknown_sender",
               "receipt": None, "error": None}

        # Size guard BEFORE any fetch: an oversized payload is quarantined
        # without ever loading its body into memory. Identity falls back to the
        # S3 ETag since we deliberately never hash the (unread) bytes; the
        # object is still recorded under parsed/ rather than silently dropped.
        size = obj.get("size")
        if size is not None and size > MAX_RAW_BYTES:
            ident = (obj.get("eTag") or "oversized").strip('"')
            out["ingest_id"] = ident
            out["classification"] = "quarantine"
            out["error"] = f"oversized: {size} bytes exceeds {MAX_RAW_BYTES}"
            dest = ("parsed/" + key.split("/", 1)[-1] + "." + ident + ".json")
            s3.put_object(
                Bucket=bucket, Key=dest,
                Body=json.dumps(out, ensure_ascii=False, default=str).encode(),
                ContentType="application/json")
            results.append({"key": key,
                            "classification": out["classification"]})
            continue

        # Infra fetch stays OUTSIDE the parse-error catch: throttling, transient
        # GetObject failures, and missing-object races must raise so Lambda
        # retries them (and, exhausted, land in the DLQ) rather than being
        # persisted as a permanent parse_error + reported as success.
        get_kwargs = {"Bucket": bucket, "Key": key}
        if version_id:
            get_kwargs["VersionId"] = version_id
        raw = s3.get_object(**get_kwargs)["Body"].read()
        # Immutable identity: content digest, independent of the sender's
        # spoofable RFC Message-ID (which is retained separately below).
        out["ingest_id"] = hashlib.sha256(raw).hexdigest()

        try:
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            out["message_id"] = (msg.get("Message-ID") or key).strip()
            out["subject"] = msg.get("Subject", "")
            out.update(_ses_auth(msg))
            from_addr, domain = _from_domain(msg)
            out["from_domain"] = domain
            out["original_from"] = from_addr
            if _content_rejected(out["virus_verdict"], out["spam_verdict"]):
                # SES flagged (or could not clear) malware/spam; hold it out of
                # reconciliation. Distinct from parser-determined non_receipt so
                # downstream can treat scan failures differently.
                out["classification"] = "quarantine"
            else:
                grp = registry.group_for_domain(domain)
                out["group"] = grp
                if grp:
                    # The registry chose the group from the canonical sender
                    # (X-Original-From in the iCloud-forwarding case), but the
                    # parsers re-dispatch on the message's own ``From`` header —
                    # which the forwarder rewrote to itself. Normalize ONLY the
                    # parser's temp copy so its From matches the sender the
                    # registry keyed on; the stored raw/ evidence is untouched.
                    parser_bytes = raw
                    orig_from = msg.get("X-Original-From")
                    if orig_from:
                        norm = email.message_from_bytes(
                            raw, policy=email.policy.default)
                        if norm.get("From") is not None:
                            norm.replace_header("From", orig_from)
                        else:
                            norm["From"] = orig_from
                        parser_bytes = norm.as_bytes()
                    with tempfile.NamedTemporaryFile(
                            suffix=".eml", delete=False) as tmp:
                        tmp.write(parser_bytes)
                        path = tmp.name
                    try:
                        parsed = registry.run_parser(grp, path)
                        if isinstance(parsed, list):
                            parsed = parsed[0] if parsed else {}
                        out["classification"] = registry.classify(
                            grp, out["subject"], parsed)
                        if out["classification"] in ("receipt", "txn_signal"):
                            out["receipt"] = parsed
                    finally:
                        os.unlink(path)
        except (ValueError, KeyError, IndexError, AttributeError, TypeError,
                email.errors.MessageError):
            # Deterministic email/parser validation failures on THIS message
            # body are permanent: recording parse_error and moving on is
            # correct — a retry would fail identically. Operational failures are
            # NOT caught here: ImportError (a broken deploy / missing parser
            # module), OSError (temp-file/disk), and anything else unexpected
            # propagate so Lambda retries them and, once retries are exhausted,
            # routes the event to the DLQ instead of persisting a false
            # parse_error and reporting success.
            out["classification"] = "parse_error"
            out["error"] = traceback.format_exc(limit=4)

        # put_object is likewise retryable — keep it outside the catch.
        # Key by content digest, not the raw key: two versions of the same S3
        # key (a replay of the same messageId) would otherwise collide on one
        # parsed object, and unordered notifications could let the older version
        # win. ingest_id makes each distinct body its own idempotent object.
        dest = ("parsed/" + key.split("/", 1)[-1] + "."
                + out["ingest_id"] + ".json")
        s3.put_object(
            Bucket=bucket, Key=dest,
            Body=json.dumps(out, ensure_ascii=False, default=str).encode(),
            ContentType="application/json")
        results.append({"key": key, "classification": out["classification"]})
    return {"processed": results}

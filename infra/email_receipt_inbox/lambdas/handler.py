"""S3-triggered parser for inbound receipt emails.

SES writes each inbound message to s3://<bucket>/raw/<messageId>. This handler
parses it through the sender registry and writes a result document to
parsed/<messageId>.json:

    {
      "message_id": "...",        # RFC-822 Message-ID (falls back to S3 key)
      "s3_key": "raw/...",
      "from_domain": "doordash.com",
      "original_from": "...",     # unwrapped when the mail arrived via a
                                  # forwarding rule (X-Forwarded-For et al.)
      "subject": "...",
      "group": "doordash" | null,
      "classification": "receipt" | "txn_signal" | "needs_ocr" | "non_receipt"
                        | "unknown_sender" | "parse_error",
      "receipt": {...} | null,    # the parser's raw schema output
      "error": "..." | null
    }

The private reconciliation plane polls parsed/ and ingests idempotently by
message_id. This function deliberately writes derived data only — no DynamoDB
coupling — so re-parses are a matter of re-running over raw/.
"""
from __future__ import annotations

import email
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
            if out["virus_verdict"] == "FAIL" or out["spam_verdict"] == "FAIL":
                # SES flagged malware/spam; do not feed it to reconciliation.
                out["classification"] = "non_receipt"
            else:
                grp = registry.group_for_domain(domain)
                out["group"] = grp
                if grp:
                    with tempfile.NamedTemporaryFile(
                            suffix=".eml", delete=False) as tmp:
                        tmp.write(raw)
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
        except Exception:
            # Only deterministic email/parser validation errors are permanent.
            out["classification"] = "parse_error"
            out["error"] = traceback.format_exc(limit=4)

        # put_object is likewise retryable — keep it outside the catch.
        dest = "parsed/" + key.split("/", 1)[-1] + ".json"
        s3.put_object(
            Bucket=bucket, Key=dest,
            Body=json.dumps(out, ensure_ascii=False, default=str).encode(),
            ContentType="application/json")
        results.append({"key": key, "classification": out["classification"]})
    return {"processed": results}

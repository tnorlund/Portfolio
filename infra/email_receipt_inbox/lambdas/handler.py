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
      "original_from": "...",     # authenticated sender selected for dispatch
      "transport_from": "...",    # raw RFC-822 From claim
      "claimed_original_from": "..." | null,  # raw X-Original-From claim
      "sender_auth_source": "ses-dmarc" | null,
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
SES_AUTHSERV_ID = "amazonses.com"


def _unique_header(msg, name: str) -> str | None:
    values = msg.get_all(name) or []
    if len(values) != 1:
        return None
    return str(values[0])


def _single_address(values) -> tuple[str, str]:
    """Return one unambiguous mailbox and its ASCII domain, else empty."""
    if isinstance(values, str):
        values = [values]
    addresses = [
        addr.strip()
        for _name, addr in email.utils.getaddresses(list(values or []))
        if addr and "@" in addr
    ]
    if len(addresses) != 1:
        return "", ""
    addr = addresses[0]
    domain = addr.rsplit("@", 1)[1].strip().rstrip(".").lower()
    if not domain or not re.fullmatch(r"[a-z0-9.-]+", domain):
        return "", ""
    return addr, domain


def _trusted_ses_auth_results(msg) -> str | None:
    """Return the sole SES authentication result, rejecting impostor copies.

    SES adds one ``Authentication-Results: amazonses.com`` field to the S3
    object. A sender can add lookalike MIME headers, but cannot suppress the
    SES-added field; requiring exactly one means an unstripped impostor makes
    the message fail closed instead of becoming another spoofing primitive.
    """
    matches = []
    for value in msg.get_all("Authentication-Results") or []:
        authserv_id, separator, _results = str(value).partition(";")
        if separator and authserv_id.strip().lower() == SES_AUTHSERV_ID:
            matches.append(str(value))
    return matches[0] if len(matches) == 1 else None


def _auth_entries(value: str) -> list[tuple[str, str, str]]:
    pattern = re.compile(
        r"(?:^|;)\s*(spf|dkim|dmarc|arc)\s*=\s*([a-z_]+)(.*?)"
        r"(?=;\s*(?:spf|dkim|dmarc|arc)\s*=|$)",
        re.IGNORECASE | re.DOTALL,
    )
    return [
        (mechanism.lower(), status.lower(), details)
        for mechanism, status, details in pattern.findall(value)
    ]


def _identity_domain(value: str) -> str:
    identity = value.strip().strip("<>\"'")
    domain = identity.rsplit("@", 1)[-1].rstrip(".").lower()
    if not domain or not re.fullmatch(r"[a-z0-9.-]+", domain):
        return ""
    return domain


def _authenticated_sender(msg) -> tuple[str, str, str | None]:
    """Bind sender dispatch to SES DMARC PASS for the visible From identity."""
    addr, domain = _single_address(msg.get_all("From") or [])
    auth_results = _trusted_ses_auth_results(msg)
    if not addr or not auth_results:
        return "", "", None
    for mechanism, status, details in _auth_entries(auth_results):
        if mechanism != "dmarc" or status != "pass":
            continue
        match = re.search(
            r"(?:^|[;\s])header\.from\s*=\s*([^;\s()]+)",
            details,
            re.IGNORECASE,
        )
        if match and _identity_domain(match.group(1)) == domain:
            return addr, domain, "ses-dmarc"
    return "", "", None


def _ses_auth(msg) -> dict:
    """Extract unique SES-stamped content and authentication results.

    SES writes these headers into the stored message; scan_enabled only
    *produces* them, it never rejects. Duplicates fail closed because a sender
    may have injected a lookalike before SES added its authoritative field.
    """
    spam = (_unique_header(msg, "X-SES-Spam-Verdict") or "").strip().upper()
    virus = (_unique_header(msg, "X-SES-Virus-Verdict") or "").strip().upper()
    auth = {
        mechanism: status
        for mechanism, status, _details in _auth_entries(
            _trusted_ses_auth_results(msg) or "")
        if mechanism in ("spf", "dkim", "dmarc")
    }
    return {
        "spam_verdict": spam or None,
        "virus_verdict": virus or None,
        "auth": auth,
    }


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
            transport_addr, _transport_domain = _single_address(
                msg.get_all("From") or [])
            claimed_addr, claimed_domain = _single_address(
                msg.get_all("X-Original-From") or [])
            from_addr, domain, auth_source = _authenticated_sender(msg)
            out["from_domain"] = domain
            out["original_from"] = from_addr
            out["transport_from"] = transport_addr
            out["claimed_original_from"] = claimed_addr or None
            out["sender_auth_source"] = auth_source
            if _content_rejected(spam=out["spam_verdict"],
                                 virus=out["virus_verdict"]):
                # SES flagged (or could not clear) malware/spam; hold it out of
                # reconciliation. Distinct from parser-determined non_receipt so
                # downstream can treat scan failures differently.
                out["classification"] = "quarantine"
            elif auth_source is None:
                out["classification"] = "quarantine"
                out["error"] = "sender identity was not authenticated by SES"
            else:
                grp = registry.group_for_domain(domain)
                claimed_grp = registry.group_for_domain(claimed_domain)
                if claimed_grp and claimed_grp != grp:
                    out["classification"] = "quarantine"
                    out["error"] = "unauthenticated X-Original-From claim"
                elif grp:
                    out["group"] = grp
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
                        # Persist the parser's raw schema whenever it produced
                        # one — including needs_ocr, where the parser already
                        # extracted usable fields (e.g. Apple retail PDF stubs
                        # carry merchant/date/order_id/attachment name before
                        # flagging needs_pdf). Dropping it here would force
                        # downstream to re-derive data the parser already has.
                        if out["classification"] in (
                                "receipt", "txn_signal", "needs_ocr"):
                            out["receipt"] = parsed
                    finally:
                        os.unlink(path)
        except (ValueError, KeyError, IndexError, AttributeError, TypeError,
                email.errors.MessageError):
            # Deterministic email/parser validation failures on THIS message
            # body are permanent: recording parse_error and moving on is
            # correct — a retry would fail identically. Integration defects are
            # NOT reachable here: the parser entry points are resolved and
            # signature-checked at cold start and their return type is validated
            # in run_parser, so a missing/renamed entry point, a bad signature,
            # or a wrong return type raises registry.ParserContractError (not a
            # subclass of the exceptions above) and propagates. Operational
            # failures — ImportError (broken deploy), OSError (temp-file/disk),
            # and anything else unexpected — likewise propagate so Lambda
            # retries them and, once exhausted, routes the event to the DLQ
            # instead of persisting a false parse_error and reporting success.
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

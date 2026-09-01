"""Extract short-lived Greenhouse verification codes from SES email.

The raw MIME message is untrusted input. This handler accepts only an exact
Greenhouse sender whose visible From domain passed SES DMARC, requires clean
SES content-scan verdicts, and persists only the extracted code plus minimal
metadata. Subjects and message bodies never cross into DynamoDB or logs.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import hashlib
import os
import re
import time
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser

import boto3

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

MAX_RAW_BYTES = 1 * 1024 * 1024
MAX_BODY_CHARS = 200_000
CODE_TTL_SECONDS = 60 * 60
SES_AUTHSERV_ID = "amazonses.com"

PROVIDERS = {
    "greenhouse": {
        "no-reply@greenhouse.io",
        "no-reply@us.greenhouse-mail.io",
        "no-reply@eu.greenhouse-mail.io",
        "no-reply@anz.greenhouse.io",
    }
}

_SUBJECT_PATTERN = re.compile(
    r"\b(?:security|verification|one[- ]time|human[- ]check)\s+code\b",
    re.IGNORECASE,
)
_CONTEXT_PATTERNS = (
    re.compile(
        r"\b(?:security|verification|one[- ]time|human[- ]check)\s+code"
        r"(?:\s+(?:is|was))?\s*[:\-]?\s*"
        r"([A-Za-z0-9]{8})(?![A-Za-z0-9])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9])([A-Za-z0-9]{8})\s+is\s+(?:your\s+)?"
        r"(?:security|verification|one[- ]time|human[- ]check)\s+code\b",
        re.IGNORECASE,
    ),
)
_CODE_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{8})(?![A-Za-z0-9])")
_NON_CODE_TOKENS = {
    "security",
    "continue",
    "password",
    "applying",
}


class _HtmlText(HTMLParser):
    """Collect visible text and heading text without external dependencies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.headings: list[str] = []
        self._heading_depth = 0
        self._heading_text: list[str] = []

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag.lower() in {"h1", "h2", "h3"}:
            self._heading_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"h1", "h2", "h3"} and self._heading_depth:
            self._heading_depth -= 1
            if not self._heading_depth:
                value = " ".join(self._heading_text).strip()
                if value:
                    self.headings.append(value)
                self._heading_text = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._heading_depth:
            self._heading_text.append(data)


def _unique_header(message, name: str) -> str | None:
    values = message.get_all(name) or []
    if len(values) != 1:
        return None
    return str(values[0])


def _single_address(message) -> tuple[str, str]:
    values = message.get_all("From") or []
    addresses = [
        address.strip().lower()
        for _name, address in email.utils.getaddresses(values)
        if address and "@" in address
    ]
    if len(addresses) != 1:
        return "", ""
    address = addresses[0]
    domain = address.rsplit("@", 1)[1].rstrip(".")
    if not re.fullmatch(r"[a-z0-9.-]+", domain):
        return "", ""
    return address, domain


def _trusted_ses_auth_results(message) -> str | None:
    matches = []
    for value in message.get_all("Authentication-Results") or []:
        authserv_id, separator, _results = str(value).partition(";")
        if separator and authserv_id.strip().lower() == SES_AUTHSERV_ID:
            matches.append(str(value))
    return matches[0] if len(matches) == 1 else None


def _dmarc_passes_for_domain(auth_results: str, domain: str) -> bool:
    entries = re.finditer(
        r"(?:^|;)\s*dmarc\s*=\s*([a-z_]+)(.*?)"
        r"(?=;\s*(?:spf|dkim|dmarc|arc)\s*=|$)",
        auth_results,
        re.IGNORECASE | re.DOTALL,
    )
    for entry in entries:
        if entry.group(1).lower() != "pass":
            continue
        match = re.search(
            r"(?:^|[;\s])header\.from\s*=\s*([^;\s()]+)",
            entry.group(2),
            re.IGNORECASE,
        )
        if not match:
            continue
        identity = match.group(1).strip("<>\"'").rstrip(".").lower()
        identity_domain = identity.rsplit("@", 1)[-1]
        if identity_domain == domain:
            return True
    return False


def _authenticated_provider(message) -> tuple[str, str] | None:
    address, domain = _single_address(message)
    auth_results = _trusted_ses_auth_results(message)
    if not address or not auth_results:
        return None
    if not _dmarc_passes_for_domain(auth_results, domain):
        return None
    for provider, senders in PROVIDERS.items():
        if address in senders:
            return provider, address
    return None


def _clean_scan_verdicts(message) -> bool:
    spam = (_unique_header(message, "X-SES-Spam-Verdict") or "").upper()
    virus = (_unique_header(message, "X-SES-Virus-Verdict") or "").upper()
    return spam == "PASS" and virus == "PASS"


def _body_parts(message) -> tuple[str, list[str]]:
    text_parts: list[str] = []
    headings: list[str] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if (
            part.is_multipart()
            or part.get_content_disposition() == "attachment"
        ):
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", errors="replace")
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        if content_type == "text/html":
            parser = _HtmlText()
            parser.feed(content[:MAX_BODY_CHARS])
            parser.close()
            text_parts.append(" ".join(parser.text))
            headings.extend(parser.headings)
        else:
            text_parts.append(content)
    normalized = " ".join(" ".join(text_parts).split())[:MAX_BODY_CHARS]
    return normalized, headings


def _extract_code(message) -> str | None:
    subject = str(message.get("Subject", ""))
    if not _SUBJECT_PATTERN.search(subject):
        return None

    body, headings = _body_parts(message)
    for heading in headings:
        match = _CODE_PATTERN.fullmatch(heading.strip())
        if match and match.group(1).lower() not in _NON_CODE_TOKENS:
            return match.group(1)
    for pattern in _CONTEXT_PATTERNS:
        match = pattern.search(body)
        if match and match.group(1).lower() not in _NON_CODE_TOKENS:
            return match.group(1)
    return None


def _received_at(record: dict) -> int:
    event_time = record.get("eventTime")
    if isinstance(event_time, str):
        try:
            return int(
                datetime.fromisoformat(
                    event_time.replace("Z", "+00:00")
                ).timestamp()
            )
        except ValueError:
            pass
    return int(time.time())


def _process_record(record: dict) -> str:
    obj = record["s3"]["object"]
    if int(obj.get("size", 0)) > MAX_RAW_BYTES:
        return "ignored_oversized"

    bucket = record["s3"]["bucket"]["name"]
    key = urllib.parse.unquote_plus(obj["key"])
    request = {"Bucket": bucket, "Key": key}
    if obj.get("versionId"):
        request["VersionId"] = obj["versionId"]
    raw = s3.get_object(**request)["Body"].read(MAX_RAW_BYTES + 1)
    if len(raw) > MAX_RAW_BYTES:
        return "ignored_oversized"

    message = email.message_from_bytes(raw, policy=email.policy.default)
    sender = _authenticated_provider(message)
    if sender is None or not _clean_scan_verdicts(message):
        return "ignored_untrusted"
    code = _extract_code(message)
    if code is None:
        return "ignored_no_code"

    provider, sender_address = sender
    received_at = _received_at(record)
    digest = hashlib.sha256(raw).hexdigest()
    table.put_item(
        Item={
            "provider": provider,
            "received_at_id": f"{received_at:010d}#{digest}",
            "received_at": received_at,
            "expires_at": received_at + CODE_TTL_SECONDS,
            "code": code,
            "sender": sender_address,
            "ingest_id": digest,
        }
    )
    return "stored"


def lambda_handler(event, _context):
    outcomes: dict[str, int] = {}
    for record in event.get("Records", []):
        outcome = _process_record(record)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    # Never include a code, subject, sender-controlled body, or raw exception
    # in the result: Lambda may retain return values in service diagnostics.
    return {"processed": sum(outcomes.values()), "outcomes": outcomes}

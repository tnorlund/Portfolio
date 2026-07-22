"""Adversarial sender-authentication tests for the inbound email handler."""

from __future__ import annotations

import importlib.util
import json
from io import BytesIO
from pathlib import Path

import boto3

HANDLER_PATH = (
    Path(__file__).parents[1]
    / "email_receipt_inbox"
    / "lambdas"
    / "handler.py"
)


class FakeS3:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.writes: list[dict] = []

    def get_object(self, **_kwargs):
        return {"Body": BytesIO(self.raw)}

    def put_object(self, **kwargs):
        self.writes.append(kwargs)


def _load_handler(monkeypatch, s3: FakeS3):
    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: s3)
    monkeypatch.syspath_prepend(str(HANDLER_PATH.parent))
    spec = importlib.util.spec_from_file_location(
        "email_receipt_handler", HANDLER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _s3_event() -> dict:
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "mail-dev"},
                    "object": {
                        "key": "raw/message-id",
                        "size": 512,
                        "eTag": "etag",
                    },
                }
            }
        ]
    }


def _message(
    from_header: str,
    *,
    original_from: str | None = None,
    auth_results: tuple[str, ...] = (),
    virus: str = "PASS",
) -> bytes:
    headers = [f"From: {from_header}"]
    if original_from is not None:
        headers.append(f"X-Original-From: {original_from}")
    headers.extend(
        f"Authentication-Results: {value}" for value in auth_results
    )
    headers.extend(
        [
            "X-SES-Spam-Verdict: PASS",
            f"X-SES-Virus-Verdict: {virus}",
            "Subject: Your receipt",
            "Message-ID: <message-id@example.com>",
            "",
            "receipt body",
        ]
    )
    return "\r\n".join(headers).encode()


def test_spoofed_x_original_from_cannot_select_costco_parser(
    monkeypatch,
) -> None:
    raw = b"\r\n".join(
        [
            b"From: Attacker <attacker@evil.example>",
            b"X-Original-From: Costco <receipts@online.costco.com>",
            b"Authentication-Results: amazonses.com; spf=pass "
            b"envelope-from=attacker@evil.example; dkim=pass "
            b"header.i=@evil.example; dmarc=pass "
            b"header.from=evil.example;",
            b"X-SES-Spam-Verdict: PASS",
            b"X-SES-Virus-Verdict: PASS",
            b"Subject: Your Costco receipt",
            b"Message-ID: <spoof@evil.example>",
            b"",
            b"fake receipt body",
        ]
    )
    fake_s3 = FakeS3(raw)
    handler = _load_handler(monkeypatch, fake_s3)
    parser_calls = []
    monkeypatch.setattr(
        handler.registry,
        "run_parser",
        lambda group, path: parser_calls.append((group, path))
        or {"grand_total": 123.45},
    )

    result = handler.lambda_handler(_s3_event(), None)

    assert result["processed"][0]["classification"] == "quarantine"
    assert parser_calls == []
    persisted = json.loads(fake_s3.writes[0]["Body"])
    assert persisted["from_domain"] == "evil.example"
    assert persisted["original_from"] == "attacker@evil.example"
    assert persisted["transport_from"] == "attacker@evil.example"
    assert persisted["claimed_original_from"] == "receipts@online.costco.com"
    assert persisted["sender_auth_source"] == "ses-dmarc"
    assert persisted["error"] == "unauthenticated X-Original-From claim"


def test_authenticated_direct_sender_reaches_costco_parser(
    monkeypatch,
) -> None:
    raw = _message(
        "Costco <receipts@online.costco.com>",
        auth_results=(
            "amazonses.com; spf=pass envelope-from=online.costco.com; "
            "dkim=pass header.i=@online.costco.com; dmarc=pass "
            "header.from=online.costco.com;",
        ),
    )
    fake_s3 = FakeS3(raw)
    handler = _load_handler(monkeypatch, fake_s3)
    parser_calls = []
    monkeypatch.setattr(
        handler.registry,
        "run_parser",
        lambda group, path: parser_calls.append(group)
        or {"grand_total": 123.45},
    )

    result = handler.lambda_handler(_s3_event(), None)

    assert result["processed"][0]["classification"] == "receipt"
    assert parser_calls == ["costco"]
    persisted = json.loads(fake_s3.writes[0]["Body"])
    assert persisted["from_domain"] == "online.costco.com"
    assert persisted["sender_auth_source"] == "ses-dmarc"


def test_duplicate_ses_authentication_results_fail_closed(monkeypatch) -> None:
    raw = _message(
        "Attacker <attacker@evil.example>",
        original_from="Costco <receipts@online.costco.com>",
        auth_results=(
            "amazonses.com; dmarc=pass header.from=online.costco.com;",
            "amazonses.com; dmarc=pass header.from=evil.example;",
        ),
    )
    fake_s3 = FakeS3(raw)
    handler = _load_handler(monkeypatch, fake_s3)
    parser_calls = []
    monkeypatch.setattr(
        handler.registry,
        "run_parser",
        lambda group, path: parser_calls.append((group, path)) or {},
    )

    result = handler.lambda_handler(_s3_event(), None)

    assert result["processed"][0]["classification"] == "quarantine"
    assert parser_calls == []
    persisted = json.loads(fake_s3.writes[0]["Body"])
    assert persisted["sender_auth_source"] is None
    assert persisted["auth"] == {}


def test_non_ses_authentication_results_cannot_override_ses(
    monkeypatch,
) -> None:
    raw = _message(
        "Costco <receipts@online.costco.com>",
        auth_results=(
            "attacker.example; dmarc=pass header.from=evil.example;",
            "amazonses.com; dmarc=pass header.from=online.costco.com;",
        ),
    )
    fake_s3 = FakeS3(raw)
    handler = _load_handler(monkeypatch, fake_s3)
    parser_calls = []
    monkeypatch.setattr(
        handler.registry,
        "run_parser",
        lambda group, path: parser_calls.append(group)
        or {"grand_total": 123.45},
    )

    result = handler.lambda_handler(_s3_event(), None)

    assert result["processed"][0]["classification"] == "receipt"
    assert parser_calls == ["costco"]


def test_misaligned_dmarc_identity_fails_closed(monkeypatch) -> None:
    raw = _message(
        "Costco <receipts@online.costco.com>",
        auth_results=(
            "amazonses.com; spf=pass envelope-from=evil.example; "
            "dkim=pass header.i=@evil.example; dmarc=pass "
            "header.from=evil.example;",
        ),
    )
    fake_s3 = FakeS3(raw)
    handler = _load_handler(monkeypatch, fake_s3)
    parser_calls = []
    monkeypatch.setattr(
        handler.registry,
        "run_parser",
        lambda group, path: parser_calls.append((group, path)) or {},
    )

    result = handler.lambda_handler(_s3_event(), None)

    assert result["processed"][0]["classification"] == "quarantine"
    assert parser_calls == []


def test_aligned_from_with_failing_dmarc_quarantines(monkeypatch) -> None:
    # The canonical direct spoof: the visible From aligns with the DMARC
    # identity, but DMARC itself FAILED. Accepting any non-"pass" status
    # here would let an attacker mail as receipts@online.costco.com; this
    # pins the status == "pass" gate the mutation check found uncovered.
    raw = _message(
        "Costco <receipts@online.costco.com>",
        auth_results=(
            "amazonses.com; spf=fail envelope-from=online.costco.com; "
            "dkim=fail header.i=@online.costco.com; dmarc=fail "
            "header.from=online.costco.com;",
        ),
    )
    fake_s3 = FakeS3(raw)
    handler = _load_handler(monkeypatch, fake_s3)
    parser_calls = []
    monkeypatch.setattr(
        handler.registry,
        "run_parser",
        lambda group, path: parser_calls.append((group, path)) or {},
    )

    result = handler.lambda_handler(_s3_event(), None)

    assert result["processed"][0]["classification"] == "quarantine"
    assert parser_calls == []


def test_unvalidated_arc_cannot_authenticate_original_sender(
    monkeypatch,
) -> None:
    raw = _message(
        "Forwarder <forwarder@icloud.com>",
        original_from="Costco <receipts@online.costco.com>",
        auth_results=(
            "amazonses.com; dmarc=pass header.from=icloud.com; arc=pass;",
        ),
    ).replace(
        b"X-SES-Spam-Verdict",
        b"ARC-Seal: i=1; cv=pass; d=icloud.com; b=forged\r\n"
        b"ARC-Authentication-Results: i=1; dmarc=pass "
        b"header.from=online.costco.com;\r\nX-SES-Spam-Verdict",
    )
    fake_s3 = FakeS3(raw)
    handler = _load_handler(monkeypatch, fake_s3)
    parser_calls = []
    monkeypatch.setattr(
        handler.registry,
        "run_parser",
        lambda group, path: parser_calls.append((group, path)) or {},
    )

    result = handler.lambda_handler(_s3_event(), None)

    assert result["processed"][0]["classification"] == "quarantine"
    assert parser_calls == []
    persisted = json.loads(fake_s3.writes[0]["Body"])
    assert persisted["from_domain"] == "icloud.com"
    assert persisted["error"] == "unauthenticated X-Original-From claim"


def test_content_rejection_precedes_authenticated_dispatch(
    monkeypatch,
) -> None:
    raw = _message(
        "Costco <receipts@online.costco.com>",
        auth_results=(
            "amazonses.com; dmarc=pass header.from=online.costco.com;",
        ),
        virus="FAIL",
    )
    fake_s3 = FakeS3(raw)
    handler = _load_handler(monkeypatch, fake_s3)
    parser_calls = []
    monkeypatch.setattr(
        handler.registry,
        "run_parser",
        lambda group, path: parser_calls.append((group, path)) or {},
    )

    result = handler.lambda_handler(_s3_event(), None)

    assert result["processed"][0]["classification"] == "quarantine"
    assert parser_calls == []

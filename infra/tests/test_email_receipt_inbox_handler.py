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
    spec = importlib.util.spec_from_file_location("email_receipt_handler", HANDLER_PATH)
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


def test_spoofed_x_original_from_cannot_select_costco_parser(monkeypatch) -> None:
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

    assert result["processed"][0]["classification"] == "unknown_sender"
    assert parser_calls == []
    persisted = json.loads(fake_s3.writes[0]["Body"])
    assert persisted["from_domain"] == "evil.example"
    assert persisted["original_from"] == "attacker@evil.example"

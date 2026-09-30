"""Security controls plus opt-in real Clover receipt checks.

No receipt bytes, item names, access URLs, or provider identifiers are fixtures
in this repository. URL strings below are adversarial transport inputs, not
synthetic receipts. Real parsing checks require external private files.
"""

from __future__ import annotations

import io
import json
import os
import socket
import ssl
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import clover_receipt as reader

REPO = Path(__file__).resolve().parents[1]
RECEIPT_ROUTE = "https://www.clover.com/p/security-test"
TRACKING_ROUTE = "https://u1.ct.sendgrid.net/ls/click?upn=security-test"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.clover.com/p/security-test",
        "https://www.clover.com:444/p/security-test",
        "https://name:password@www.clover.com/p/security-test",
        "https://www.clover.com.evil.example/p/security-test",
        "https://www.clover.com./p/security-test",
        "https://www.clover.com/admin",
        "https://www.clover.com/p/security-test?redirect=elsewhere",
        "https://www.clover.com/p/security-test#fragment",
        "https://www.clover.com/p/%2e%2e",
        "https://www.clover.com/p/security-test%0d%0aHeader:value",
        "https://127.0.0.1/p/security-test",
        "https://169.254.169.254/p/security-test",
        "https://[::1]/p/security-test",
        "https://[fe80::1]/p/security-test",
        "https://u1.ct.sendgrid.net/ls/click?upn=",
        "https://u1.ct.sendgrid.net/ls/click?upn=x&next=y",
        "https://u1.ct.sendgrid.net/ls/click?upn=x&upn=y",
        "https://u1.ct.sendgrid.net/other?upn=x",
        "https://www.clover.com\\@localhost/p/security-test",
        "https://www.clover.com/p/" + "a" * reader.MAX_URL_BYTES,
    ],
)
def test_rejects_non_receipt_urls(url: str) -> None:
    with pytest.raises(reader.ReceiptError, match="^unsupported_url$"):
        reader.validate_url(url, initial=True)


def test_tracking_is_allowed_only_at_entry() -> None:
    assert reader.validate_url(TRACKING_ROUTE, initial=True)[0].endswith(
        ".ct.sendgrid.net"
    )
    assert reader.validate_url(RECEIPT_ROUTE, initial=False)[0] == (
        reader.CLOVER_HOST
    )
    with pytest.raises(reader.ReceiptError, match="unsupported_url"):
        reader.validate_url(TRACKING_ROUTE, initial=False)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "::ffff:127.0.0.1",
        "fe80::1",
    ],
)
def test_dns_rejects_any_non_public_address(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
        for ip in ("8.8.8.8", address)
    ]
    monkeypatch.setattr(reader.socket, "getaddrinfo", lambda *args: answers)
    with pytest.raises(reader.ReceiptError, match="blocked_address"):
        reader.resolve_public_ipv4(reader.CLOVER_HOST)


def test_connection_pins_ip_but_validates_original_tls_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = Mock()
    connect = Mock(return_value=raw)
    monkeypatch.setattr(reader.socket, "create_connection", connect)
    connection = reader._PinnedHTTPSConnection(reader.CLOVER_HOST, "8.8.8.8")
    assert connection._context.check_hostname
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    tls = Mock()
    connection._context = tls
    connection.connect()
    connect.assert_called_once_with(
        ("8.8.8.8", 443), timeout=reader.SOCKET_TIMEOUT
    )
    tls.wrap_socket.assert_called_once_with(
        raw, server_hostname=reader.CLOVER_HOST
    )


class _Response:
    def __init__(
        self, status: int, headers: dict[str, str], body: bytes = b""
    ) -> None:
        self.status, self.headers, self.body = status, headers, body
        self.read = Mock(side_effect=lambda size: self.body[:size])

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)


@pytest.mark.parametrize(
    ("framing", "error"),
    [
        (b"-1\r\n", "unsupported_content"),
        (b"1000000\r\n", "response_too_large"),
        (
            (b"1;" + b"x" * 8192 + b"\r\nx\r\n") * 80,
            "response_too_large",
        ),
    ],
    ids=["negative-size", "oversized-chunk", "oversized-extensions"],
)
def test_wire_budget_bounds_http_chunk_decoder(
    framing: bytes, error: str
) -> None:
    wire = io.BytesIO(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        + framing
        + b"x" * (reader.MAX_RESPONSE_BYTES * 3)
    )
    response = reader._BoundedHTTPResponse(
        SimpleNamespace(makefile=lambda *_: wire)
    )
    response.begin()
    with pytest.raises(reader.ReceiptError, match=error):
        response.read(reader.MAX_RESPONSE_BYTES + 1)
    assert wire.tell() <= reader.MAX_RESPONSE_BYTES + 1
    response.close()


def test_wire_budget_includes_response_headers() -> None:
    wire = io.BytesIO(
        b"HTTP/1.1 200 OK\r\n"
        + (b"X-Padding: " + b"x" * 16384 + b"\r\n") * 40
        + b"\r\n"
    )
    response = reader._BoundedHTTPResponse(
        SimpleNamespace(makefile=lambda *_: wire)
    )
    with pytest.raises(reader.ReceiptError, match="response_too_large"):
        response.begin()
    assert wire.tell() <= reader.MAX_RESPONSE_BYTES + 1
    response.close()


def test_bounded_decoder_handles_valid_chunked_transport() -> None:
    wire = io.BytesIO(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"3\r\nabc\r\n0\r\n\r\n"
    )
    response = reader._BoundedHTTPResponse(
        SimpleNamespace(makefile=lambda *_: wire)
    )
    response.begin()
    assert response.read(reader.MAX_RESPONSE_BYTES + 1) == b"abc"
    response.close()


def _transport(
    monkeypatch: pytest.MonkeyPatch, responses: list[_Response]
) -> tuple[list[Mock], Mock]:
    connections = []

    def connection(host: str, address: str) -> Mock:
        result = Mock()
        result.getresponse.return_value = responses[len(connections)]
        connections.append(result)
        return result

    resolve = Mock(return_value="8.8.8.8")
    monkeypatch.setattr(reader, "resolve_public_ipv4", resolve)
    monkeypatch.setattr(reader, "_PinnedHTTPSConnection", connection)
    return connections, resolve


def test_redirect_rechecks_dns_and_closes_every_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections, resolve = _transport(
        monkeypatch,
        [
            _Response(302, {"Location": RECEIPT_ROUTE}),
            _Response(200, {"Content-Type": "text/html"}),
        ],
    )
    body, _, redirects = reader._read_document(TRACKING_ROUTE)
    assert body == b""
    assert redirects == 1
    assert [call.args[0] for call in resolve.call_args_list] == [
        "u1.ct.sendgrid.net",
        reader.CLOVER_HOST,
    ]
    for connection in connections:
        connection.close.assert_called_once()


@pytest.mark.parametrize(
    "location",
    [
        "https://127.0.0.1/",
        "https://169.254.169.254/",
        "https://other.example/",
        TRACKING_ROUTE,
        "/account",
    ],
)
def test_bad_redirect_is_rejected_before_second_request(
    monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    connections, resolve = _transport(
        monkeypatch, [_Response(302, {"Location": location})]
    )
    with pytest.raises(reader.ReceiptError):
        reader._read_document(TRACKING_ROUTE)
    assert len(connections) == 1
    assert resolve.call_count == 1
    connections[0].close.assert_called_once()


def test_second_hop_private_dns_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections, resolve = _transport(
        monkeypatch, [_Response(302, {"Location": RECEIPT_ROUTE})]
    )
    resolve.side_effect = ["8.8.8.8", reader.ReceiptError("blocked_address")]
    with pytest.raises(reader.ReceiptError, match="blocked_address"):
        reader._read_document(TRACKING_ROUTE)
    assert len(connections) == 1


def test_redirect_count_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    connections, _ = _transport(
        monkeypatch,
        [
            _Response(302, {"Location": RECEIPT_ROUTE})
            for _ in range(reader.MAX_REDIRECTS + 1)
        ],
    )
    with pytest.raises(reader.ReceiptError, match="redirect_limit"):
        reader._read_document(RECEIPT_ROUTE)
    assert len(connections) == reader.MAX_REDIRECTS + 1


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (_Response(403, {}), "access_required"),
        (_Response(404, {}), "http_error"),
        (
            _Response(200, {"Content-Type": "application/pdf"}),
            "unsupported_content",
        ),
        (
            _Response(
                200, {"Content-Type": "text/html", "Content-Encoding": "gzip"}
            ),
            "unsupported_content",
        ),
        (
            _Response(
                200,
                {
                    "Content-Type": "text/html",
                    "Content-Length": str(reader.MAX_RESPONSE_BYTES + 1),
                },
            ),
            "response_too_large",
        ),
        (
            _Response(
                200,
                {"Content-Type": "text/html"},
                b" " * (reader.MAX_RESPONSE_BYTES + 1),
            ),
            "response_too_large",
        ),
    ],
)
def test_response_limits(
    monkeypatch: pytest.MonkeyPatch, response: _Response, error: str
) -> None:
    connections, _ = _transport(monkeypatch, [response])
    with pytest.raises(reader.ReceiptError, match=error):
        reader._read_document(RECEIPT_ROUTE)
    connections[0].close.assert_called_once()


def test_hard_deadline_and_safe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(
        side_effect=subprocess.TimeoutExpired(
            "command with a private access URL", reader.TOTAL_TIMEOUT
        )
    )
    monkeypatch.setattr(reader.subprocess, "run", run)
    result = reader.fetch_clover_receipt(RECEIPT_ROUTE)
    assert result["error_code"] == "deadline_exceeded"
    assert "private access URL" not in json.dumps(result)
    assert run.call_args.kwargs["timeout"] == reader.TOTAL_TIMEOUT
    assert RECEIPT_ROUTE not in run.call_args.args[0]
    assert run.call_args.kwargs["stderr"] == subprocess.DEVNULL


def test_worker_exception_does_not_echo_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reader.sys, "stdin", io.StringIO(json.dumps({"url": RECEIPT_ROUTE}))
    )
    monkeypatch.setattr(
        reader, "_retrieve", Mock(side_effect=OSError(RECEIPT_ROUTE))
    )
    result = reader._worker()
    assert result["error_code"] == "retrieval_failed"
    assert RECEIPT_ROUTE not in json.dumps(result)


def test_error_code_cannot_echo_remote_text() -> None:
    result = reader._error(RECEIPT_ROUTE)
    assert result["error_code"] == "retrieval_failed"
    assert RECEIPT_ROUTE not in json.dumps(result)


def test_parent_rejects_invalid_worker_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reader.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(returncode=0, stdout="[]")),
    )
    assert reader.fetch_clover_receipt(RECEIPT_ROUTE)["error_code"] == (
        "worker_error"
    )


def _private_path(variable: str) -> Path:
    value = os.environ.get(variable)
    if not value:
        pytest.skip("real private receipt fixture not configured")
    path = Path(value).resolve(strict=True)
    assert not path.is_relative_to(REPO), "keep real receipt files private"
    return path


def test_real_receipt_matches_independently_rendered_expectations() -> None:
    body = _private_path("CLOVER_RECEIPT_HTML").read_bytes()
    expected = json.loads(_private_path("CLOVER_RECEIPT_EXPECTED").read_text())
    actual = reader.parse_clover_receipt(body)
    # The rendered page uses CSS lowercase for AM/PM. Compare clock text
    # without that typographical distinction; all other fields stay exact.
    actual["time_text"] = actual["time_text"].casefold()
    expected["time_text"] = expected["time_text"].casefold()
    matches = actual == expected
    assert matches, "rendered receipt mismatch (private values suppressed)"


def test_real_receipt_missing_item_marker_fails_closed() -> None:
    body = _private_path("CLOVER_RECEIPT_HTML").read_bytes()
    damaged = body.replace(b'class="line-item"', b'class="removed"')
    marker_present = damaged != body
    assert marker_present, "real fixture must contain the expected item marker"
    with pytest.raises(reader.ReceiptError, match="unrecognized_receipt"):
        reader.parse_clover_receipt(damaged)

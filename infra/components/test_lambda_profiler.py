"""Tests for request-gated Lambda cProfile capture."""

import base64
import gzip
import json
import pstats
from types import SimpleNamespace

import pytest

from infra.components import lambda_profiler


@pytest.fixture(autouse=True)
def reset_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lambda_profiler, "_profile_captured", False)


def test_disabled_profiler_returns_original_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LAMBDA_PROFILE_ENABLED", raising=False)

    def handler(event, context):
        return event, context

    assert lambda_profiler.profile_handler(handler) is handler


def test_profile_requires_opt_in_query(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LAMBDA_PROFILE_ENABLED", "1")

    def handler(event, _context):
        return event["value"] + 1

    wrapped = lambda_profiler.profile_handler(handler)

    assert wrapped({"value": 2}, SimpleNamespace()) == 3
    assert capsys.readouterr().out == ""


def test_profile_chunks_reconstruct_valid_pstats(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    monkeypatch.setenv("LAMBDA_PROFILE_ENABLED", "1")

    def handler(event, _context):
        return sum(range(event["count"]))

    wrapped = lambda_profiler.profile_handler(handler)
    event = {
        "count": 100,
        "queryStringParameters": {"__profile": "1"},
    }
    context = SimpleNamespace(aws_request_id="request-1474")

    assert wrapped(event, context) == 4950

    messages = []
    for line in capsys.readouterr().out.splitlines():
        assert line.startswith(lambda_profiler.PROFILE_MARKER)
        messages.append(
            json.loads(line[len(lambda_profiler.PROFILE_MARKER) :])
        )
    assert messages
    assert {message["profile_id"] for message in messages} == {"request-1474"}
    messages.sort(key=lambda message: message["sequence"])
    encoded = "".join(message["data"] for message in messages)
    profile_path = tmp_path / "handler.prof"
    profile_path.write_bytes(gzip.decompress(base64.b64decode(encoded)))

    stats = pstats.Stats(str(profile_path))
    assert any(key[2] == "handler" for key in stats.stats)

    # Only one capture is allowed per execution environment.
    assert wrapped(event, context) == 4950
    assert capsys.readouterr().out == ""

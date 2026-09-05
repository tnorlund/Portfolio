"""Local transport boundaries and validation."""

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import Mock

import pytest

from planner.http import make_handler


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("PLANNER_ENV", "local")
    monkeypatch.setenv("PLANNER_TABLE", "PlannerTest")
    planner = Mock()
    planner.snapshot.return_value = {"version": 1, "items": []}
    planner.execute.return_value = {"version": 2, "result": {}}
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(planner))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, planner
    server.shutdown()
    server.server_close()


def request(
    api, method="GET", path="/planner/api/snapshot", body=None, headers=None
):
    connection = HTTPConnection("127.0.0.1", api[0])
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    result = (
        response.status,
        dict(response.getheaders()),
        json.loads(response.read()),
    )
    connection.close()
    return result


def test_only_allowed_loopback_origins_can_read(api):
    status, headers, _ = request(
        api, headers={"Origin": "http://127.0.0.1:3400"}
    )
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:3400"
    assert (
        request(api, headers={"Origin": "https://untrusted.example"})[0] == 403
    )
    assert request(api, headers={"Host": "untrusted.example"})[0] == 403


def test_plain_form_posts_cannot_mutate(api):
    assert (
        request(
            api,
            "POST",
            "/planner/api/commands",
            "{}",
            {"Content-Type": "text/plain"},
        )[0]
        == 400
    )
    assert (
        request(
            api,
            "POST",
            "/planner/api/commands",
            "[]",
            {"Content-Type": "application/json"},
        )[0]
        == 400
    )
    api[1].execute.assert_not_called()


def test_valid_command_preserves_retry_identity(api):
    body = {
        "request_id": "same-id-on-retry",
        "command": {"action": "save_item", "text": "Test"},
    }
    assert (
        request(
            api,
            "POST",
            "/planner/api/commands",
            json.dumps(body),
            {"Content-Type": "application/json"},
        )[0]
        == 200
    )
    api[1].execute.assert_called_once_with(body["command"], "same-id-on-retry")

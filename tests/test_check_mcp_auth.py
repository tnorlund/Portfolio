"""Unit tests for check_mcp_auth's pure logic (W1, plan
humble-skipping-quilt): credential-expiry parsing, HTTP-probe
classification, config discovery, and status aggregation.

No network, no real credential files: fixtures live in
tests/fixtures/mcp_auth/ and every value fed to the functions under test is
loaded from there or built inline.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import check_mcp_auth as cma

FIXTURES = Path(__file__).parent / "fixtures" / "mcp_auth"

# One millisecond epoch on each side of the fixture expiry (1810363482067).
FIXTURE_EXPIRY_MS = 1810363482067
BEFORE_EXPIRY_MS = FIXTURE_EXPIRY_MS - 86_400_000  # one day earlier
AFTER_EXPIRY_MS = FIXTURE_EXPIRY_MS + 1


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# --------------------------------------------------------------------------
# claude_oauth expiry parsing
# --------------------------------------------------------------------------


def test_valid_credentials_before_expiry():
    creds = load_fixture("creds_valid.json")
    check = cma.check_claude_oauth(creds, now_ms=BEFORE_EXPIRY_MS)
    assert check["name"] == "claude_oauth"
    assert check["status"] == "ok"
    assert "expires" in check["detail"]


def test_expired_credentials():
    creds = load_fixture("creds_valid.json")
    check = cma.check_claude_oauth(creds, now_ms=AFTER_EXPIRY_MS)
    assert check["status"] == "expired"


def test_missing_credentials_file():
    check = cma.check_claude_oauth(None, now_ms=BEFORE_EXPIRY_MS)
    assert check["status"] == "missing"
    assert "headless" in check["detail"]


def test_malformed_credentials():
    creds = load_fixture("creds_malformed.json")
    check = cma.check_claude_oauth(creds, now_ms=BEFORE_EXPIRY_MS)
    assert check["status"] == "malformed"


def test_non_numeric_expiry_is_malformed():
    creds = {"claudeAiOauth": {"expiresAt": "tomorrow"}}
    check = cma.check_claude_oauth(creds, now_ms=BEFORE_EXPIRY_MS)
    assert check["status"] == "malformed"


def test_no_token_material_in_output():
    """The check must never surface token material, only validity+expiry."""
    creds = load_fixture("creds_valid.json")
    for now in (BEFORE_EXPIRY_MS, AFTER_EXPIRY_MS):
        check = cma.check_claude_oauth(creds, now_ms=now)
        dumped = json.dumps(check)
        assert "fixture-not-a-real-token" not in dumped
        assert "accessToken" not in dumped


# --------------------------------------------------------------------------
# stored MCP OAuth token lookup
# --------------------------------------------------------------------------


def test_find_mcp_token_present():
    creds = load_fixture("creds_with_mcp_token.json")
    entry = cma.find_mcp_oauth_token(creds, "receipt-tools")
    assert entry is not None
    assert entry["accessToken"] == "fixture-mcp-access-token"


def test_find_mcp_token_absent():
    creds = load_fixture("creds_valid.json")
    assert cma.find_mcp_oauth_token(creds, "receipt-tools") is None
    assert cma.find_mcp_oauth_token(None, "receipt-tools") is None


# --------------------------------------------------------------------------
# HTTP probe classification (pure: takes status code, never the network)
# --------------------------------------------------------------------------


def test_probe_200_with_token_is_authenticated():
    status, _ = cma.classify_receipt_tools_http(200, has_token=True)
    assert status == "authenticated"


def test_probe_401_without_token_is_needs_auth():
    status, detail = cma.classify_receipt_tools_http(401, has_token=False)
    assert status == "needs_auth"
    assert "no stored OAuth token" in detail


def test_probe_403_with_token_is_needs_auth():
    status, detail = cma.classify_receipt_tools_http(403, has_token=True)
    assert status == "needs_auth"
    assert "rejected" in detail


def test_probe_network_error_is_unreachable():
    status, _ = cma.classify_receipt_tools_http(None, has_token=True)
    assert status == "unreachable"


def test_probe_200_without_token_is_honest_unknown():
    # A protected gateway should never 200 an anonymous call; if it does we
    # refuse to claim "authenticated".
    status, _ = cma.classify_receipt_tools_http(200, has_token=False)
    assert status == "unknown-needs-interactive-check"


def test_probe_weird_status_is_unknown():
    status, _ = cma.classify_receipt_tools_http(500, has_token=True)
    assert status == "unknown-needs-interactive-check"


# --------------------------------------------------------------------------
# config discovery precedence
# --------------------------------------------------------------------------


def test_discovery_prefers_exact_project_entry():
    cfg = load_fixture("claude_config.json")
    found = cma.discover_mcp_server(cfg, "receipt-tools", Path("/repo/root"))
    assert found["type"] == "stdio"


def test_discovery_falls_back_to_global():
    cfg = load_fixture("claude_config.json")
    found = cma.discover_mcp_server(
        cfg, "receipt-tools", Path("/unrelated/repo")
    )
    assert found["type"] == "http"
    assert "global.example" in found["url"]


def test_discovery_handles_missing_config():
    assert cma.discover_mcp_server(None, "receipt-tools", Path("/x")) is None
    assert cma.discover_mcp_server({}, "receipt-tools", Path("/x")) is None


# --------------------------------------------------------------------------
# status aggregation + exit codes
# --------------------------------------------------------------------------


def mk(status: str) -> dict:
    return {"name": "c", "status": status, "detail": ""}


def test_all_ok_is_healthy():
    checks = [mk("ok"), mk("authenticated"), mk("configured_stdio")]
    assert cma.aggregate(checks) == "healthy"


def test_needs_auth_degrades():
    assert cma.aggregate([mk("ok"), mk("needs_auth")]) == "degraded"


def test_unreachable_and_unknown_degrade():
    assert cma.aggregate([mk("unreachable")]) == "degraded"
    assert (
        cma.aggregate([mk("unknown-needs-interactive-check")]) == "degraded"
    )


def test_missing_oauth_is_red_even_with_other_oks():
    assert cma.aggregate([mk("missing"), mk("ok"), mk("ok")]) == "red"


def test_expired_is_red_and_beats_degraded():
    assert cma.aggregate([mk("expired"), mk("needs_auth")]) == "red"


def test_exit_codes():
    assert cma.overall_exit_code("healthy") == 0
    assert cma.overall_exit_code("degraded") == 1
    assert cma.overall_exit_code("red") == 2
    assert cma.overall_exit_code("garbage") == 2

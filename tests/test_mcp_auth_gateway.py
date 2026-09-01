"""Regression tests for the shared remote MCP authentication boundary."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HANDLERS = {
    "receipt": (REPO_ROOT / "infra/mcp_server_lambda/lambdas/handler.py"),
    "glyph": (REPO_ROOT / "infra/glyph_mcp_lambda/lambdas/handler.py"),
}


@pytest.fixture
def adapter_stubs(monkeypatch):
    """Install small MCP adapter stubs and report handler dispatches."""
    calls = []
    mcp = types.ModuleType("mcp")
    client = types.ModuleType("mcp.client")
    stdio = types.ModuleType("mcp.client.stdio")
    mcp_lambda = types.ModuleType("mcp_lambda")

    class StdioServerParameters:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class StdioServerAdapterRequestHandler:
        def __init__(self, server_params):
            self.server_params = server_params

    def event_handler(kind):
        class EventHandler:
            def __init__(self, request_handler):
                self.request_handler = request_handler

            def handle(self, event, context):
                calls.append((kind, event, context))
                return {"kind": kind}

        return EventHandler

    stdio.StdioServerParameters = StdioServerParameters
    mcp_lambda.StdioServerAdapterRequestHandler = (
        StdioServerAdapterRequestHandler
    )
    mcp_lambda.APIGatewayProxyEventHandler = event_handler("rest-v1")
    mcp_lambda.LambdaFunctionURLEventHandler = event_handler("url-v2")

    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio)
    monkeypatch.setitem(sys.modules, "mcp_lambda", mcp_lambda)
    return calls


@pytest.mark.parametrize(("name", "path"), HANDLERS.items())
def test_handlers_dispatch_both_supported_event_envelopes(
    name,
    path,
    adapter_stubs,
):
    spec = importlib.util.spec_from_file_location(f"{name}_http_handler", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.handler({"version": "1.0"}, "ctx") == {"kind": "rest-v1"}
    assert module.handler({"version": "2.0"}, "ctx") == {"kind": "url-v2"}
    assert [call[0] for call in adapter_stubs] == ["rest-v1", "url-v2"]


@pytest.mark.parametrize(
    "path",
    [
        REPO_ROOT / "infra/mcp_server_lambda/infrastructure.py",
        REPO_ROOT / "infra/glyph_mcp_lambda/infrastructure.py",
    ],
)
def test_direct_function_urls_require_sigv4(path):
    source = path.read_text()
    assert 'authorization_type="AWS_IAM"' in source
    assert 'authorization_type="NONE"' not in source


def test_gateway_uses_separate_cognito_scopes():
    source = (REPO_ROOT / "infra/mcp_auth_gateway.py").read_text()
    assert 'authorizer_type="JWT"' in source
    assert '("receipt", receipt_lambda, "Use receipt MCP tools")' in source
    assert '("glyph", glyph_lambda, "Use glyph MCP tools")' in source
    assert '"Read recent ATS verification codes"' in source
    assert 'f"{_RESOURCE_SERVER_ID}/{route_name}"' in source
    assert (
        'authorization_scopes=[f"{_RESOURCE_SERVER_ID}/{route_name}"]'
        in source
    )
    # Scheduled callers remain intentionally receipt-only.
    assert 'f"{_RESOURCE_SERVER_ID}/receipt"' in source
    assert 'f"{_RESOURCE_SERVER_ID}/ats"' in source
    assert "/mcp/oauth/ats-automation-client" in source
    assert 'f"POST /{route_name}/mcp"' in source
    assert "self.ats_authorizer.id" in source
    assert "oauth-protected-resource" in source


def test_ats_machine_auth_is_rotated_and_monitored():
    source = (REPO_ROOT / "infra/mcp_auth_gateway.py").read_text()
    rotation = (
        REPO_ROOT / "infra/mcp_auth_automation/lambdas/rotation.py"
    ).read_text()
    canary = (
        REPO_ROOT / "infra/mcp_auth_automation/lambdas/canary.py"
    ).read_text()

    assert "SecretRotation(" in source
    assert '"automatically_after_days": 7' in source
    assert "timeout=180" in source
    assert 'schedule_expression="rate(15 minutes)"' in source
    assert "ats-auth-canary-heartbeat" in source
    assert 'route_key="POST /ats/mcp"' in source
    assert "throttling_rate_limit=5.0" in source
    assert "access_log_settings" in source
    assert "depends_on=[access_log_group, *protected_routes]" in source
    assert "add_user_pool_client_secret" in rotation
    assert "delete_user_pool_client_secret" in rotation
    assert "AWSPENDING" in rotation
    assert "AWSCURRENT" in rotation
    assert "get_secret_value" in canary
    assert "client_credentials" in canary


def test_gateway_supports_claude_connector_oauth():
    """claude.ai connectors need their callback allowed and RFC 9728
    discovery via the path-derived well-known location — which only
    works on an HTTP API $default stage (no /{stage}/ path prefix)."""
    source = (REPO_ROOT / "infra/mcp_auth_gateway.py").read_text()
    assert "https://claude.ai/api/mcp/auth_callback" in source
    assert 'name="$default"' in source
    assert "GET /.well-known/oauth-protected-resource" in source
    assert "apigatewayv2" in source
    assert "apigateway.RestApi" not in source


def test_label_fixer_sends_bearer_token():
    """The scheduled agent must fetch a client-credentials token and
    send it on every MCP tools/call once the gateway fronts the URL."""
    import json as _json

    config = _json.loads(
        (
            REPO_ROOT / "infra/scheduled_agents/receipt_label_fixer.json"
        ).read_text()
    )
    prompt = config["prompt"]
    assert "grant_type=client_credentials" in prompt
    assert "/mcp/oauth/receipt-automation-client" in prompt
    assert '-H "Authorization: Bearer $MCP_TOKEN"' in prompt

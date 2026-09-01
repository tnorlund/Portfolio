"""Tests for MCP OAuth discovery and constrained client registration."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

LAMBDA_PATH = (
    Path(__file__).parents[1]
    / "mcp_auth_automation"
    / "lambdas"
    / "metadata.py"
)
CURSOR_CALLBACK = "https://www.cursor.com/agents/mcp/oauth/callback"


def _load(monkeypatch):
    monkeypatch.setenv(
        "METADATA_DOCS",
        json.dumps(
            {
                "/.well-known/oauth-authorization-server": {
                    "issuer": "https://mcp.example.test",
                    "registration_endpoint": (
                        "https://mcp.example.test/oauth/register"
                    ),
                }
            }
        ),
    )
    monkeypatch.setenv("DCR_CLIENT_ID", "public-client-id")
    monkeypatch.setenv(
        "DCR_ALLOWED_CALLBACK_URLS",
        json.dumps([CURSOR_CALLBACK, "http://localhost:8787/callback"]),
    )
    monkeypatch.setenv(
        "DCR_ALLOWED_SCOPES",
        json.dumps(["openid", "email", "portfolio-mcp/ats"]),
    )
    spec = importlib.util.spec_from_file_location("mcp_metadata", LAMBDA_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registration_event(body: object) -> dict:
    return {
        "rawPath": "/oauth/register",
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(body),
    }


def test_discovery_returns_authorization_server_metadata(monkeypatch) -> None:
    module = _load(monkeypatch)

    response = module.lambda_handler(
        {
            "rawPath": "/.well-known/oauth-authorization-server",
            "requestContext": {"http": {"method": "GET"}},
        },
        None,
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["issuer"] == (
        "https://mcp.example.test"
    )
    assert response["headers"]["cache-control"] == "max-age=3600"


def test_registration_returns_fixed_public_client(monkeypatch) -> None:
    module = _load(monkeypatch)

    response = module.lambda_handler(
        _registration_event(
            {
                "client_name": "Cursor hosted MCP",
                "redirect_uris": [CURSOR_CALLBACK],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "portfolio-mcp/ats",
            }
        ),
        None,
    )

    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["client_id"] == "public-client-id"
    assert body["redirect_uris"] == [CURSOR_CALLBACK]
    assert body["scope"] == "portfolio-mcp/ats"
    assert "client_secret" not in body


def test_registration_rejects_unregistered_redirect(monkeypatch) -> None:
    module = _load(monkeypatch)

    response = module.lambda_handler(
        _registration_event(
            {"redirect_uris": ["https://attacker.example/callback"]}
        ),
        None,
    )

    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert body["error"] == "invalid_redirect_uri"
    assert body["error_description"].endswith(
        "https://attacker.example/callback"
    )


def test_registration_redirect_diagnostic_strips_query_and_fragment(
    monkeypatch,
) -> None:
    module = _load(monkeypatch)

    response = module.lambda_handler(
        _registration_event(
            {
                "redirect_uris": [
                    "https://attacker.example/callback?token=secret#fragment"
                ]
            }
        ),
        None,
    )

    description = json.loads(response["body"])["error_description"]
    assert description.endswith("https://attacker.example/callback")
    assert "secret" not in description
    assert "fragment" not in description


def test_registration_rejects_unapproved_scope(monkeypatch) -> None:
    module = _load(monkeypatch)

    response = module.lambda_handler(
        _registration_event(
            {
                "redirect_uris": [CURSOR_CALLBACK],
                "scope": "portfolio-mcp/admin",
            }
        ),
        None,
    )

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"] == ("invalid_client_metadata")


def test_registration_rejects_confidential_client(monkeypatch) -> None:
    module = _load(monkeypatch)

    response = module.lambda_handler(
        _registration_event(
            {
                "redirect_uris": [CURSOR_CALLBACK],
                "token_endpoint_auth_method": "client_secret_post",
            }
        ),
        None,
    )

    assert response["statusCode"] == 400
    assert "client_secret" not in response["body"]

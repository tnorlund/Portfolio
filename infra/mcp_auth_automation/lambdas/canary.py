"""Scheduled proof that ATS machine credentials can reach the MCP server."""

from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
from typing import Any, cast

import boto3

secrets = boto3.client("secretsmanager")


def _credentials() -> dict[str, Any]:
    response = secrets.get_secret_value(
        SecretId=os.environ["MCP_CREDENTIALS_SECRET_ARN"],
        VersionStage="AWSCURRENT",
    )
    return cast(dict[str, Any], json.loads(response["SecretString"]))


def _access_token(credentials: dict[str, Any]) -> str:
    basic = base64.b64encode(
        f"{credentials['client_id']}:{credentials['client_secret']}".encode()
    ).decode()
    request = urllib.request.Request(
        credentials["token_url"],
        data=urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "scope": " ".join(credentials["scopes"]),
            }
        ).encode(),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Cognito did not return an access token")
    return token


def _initialize(credentials: dict[str, Any], token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        credentials["server_url"],
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "portfolio-ats-auth-canary",
                        "version": "1",
                    },
                },
            }
        ).encode(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError("ATS MCP canary did not return HTTP 200")
        return cast(dict[str, Any], json.load(response))


def lambda_handler(_event: dict[str, Any], _context: Any) -> dict[str, str]:
    credentials = _credentials()
    result = _initialize(credentials, _access_token(credentials))
    server_name = result.get("result", {}).get("serverInfo", {}).get("name")
    if server_name != "portfolio-ats-verification":
        raise RuntimeError("ATS MCP canary returned an unexpected server")
    print(json.dumps({"status": "ok", "server": server_name}))
    return {"status": "ok"}

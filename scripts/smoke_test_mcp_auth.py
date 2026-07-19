#!/usr/bin/env python3
"""Verify the deployed development MCP rejects anonymous access and accepts OAuth."""

import argparse
import base64
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import boto3


def _stack_output(stack: str, name: str) -> str:
    result = subprocess.run(
        ["pulumi", "stack", "output", "--stack", stack, name],
        cwd="infra",
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _request_status(request: urllib.request.Request) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _decode_mcp_response(payload: bytes) -> dict:
    text = payload.decode("utf-8").strip()
    if text.startswith("event: message"):
        text = text.split("data: ", 1)[1].strip()
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="dev")
    args = parser.parse_args()
    if args.stack != "dev":
        raise SystemExit("The personal MCP connector smoke test is dev-only")

    mcp_url = _stack_output(args.stack, "mcp_server_url")
    secret_arn = _stack_output(
        args.stack,
        "mcp_oauth_automation_secret_arn",
    )
    request_body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    ).encode()
    common_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    anonymous_status, _ = _request_status(
        urllib.request.Request(
            mcp_url,
            data=request_body,
            headers=common_headers,
            method="POST",
        )
    )
    if anonymous_status != 401:
        raise SystemExit(
            f"Expected anonymous MCP request to return 401, got {anonymous_status}"
        )

    secret_value = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)[
        "SecretString"
    ]
    credentials = json.loads(secret_value)
    basic = base64.b64encode(
        f"{credentials['client_id']}:{credentials['client_secret']}".encode()
    ).decode()
    token_request = urllib.request.Request(
        credentials["token_url"],
        data=urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "scope": "portfolio-mcp/receipt",
            }
        ).encode(),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    token_status, token_payload = _request_status(token_request)
    if token_status != 200:
        raise SystemExit(f"OAuth token request failed with {token_status}")
    access_token = json.loads(token_payload)["access_token"]

    authenticated_status, authenticated_payload = _request_status(
        urllib.request.Request(
            mcp_url,
            data=request_body,
            headers={
                **common_headers,
                "Authorization": f"Bearer {access_token}",
            },
            method="POST",
        )
    )
    response = _decode_mcp_response(authenticated_payload)
    tools = response.get("result", {}).get("tools", [])
    tool_names = {tool.get("name") for tool in tools}
    required_tools = {
        "list_receipt_health_issues",
        "update_receipt_health_issue",
        "update_word_label",
    }
    if authenticated_status != 200 or not required_tools <= tool_names:
        missing = sorted(required_tools - tool_names)
        raise SystemExit(
            "Authenticated MCP smoke test failed "
            f"(status={authenticated_status}, missing_tools={missing})"
        )

    print(
        json.dumps(
            {
                "anonymous_status": anonymous_status,
                "authenticated_status": authenticated_status,
                "tool_count": len(tools),
                "required_routine_tools": "present",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

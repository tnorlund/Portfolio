"""Small stateless MCP server for recent ATS verification codes."""

from __future__ import annotations

import base64
import json
import os
import re
import time

import boto3
from boto3.dynamodb.conditions import Key

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {PROTOCOL_VERSION, "2024-11-05"}
DEFAULT_MAX_AGE_SECONDS = 600
MIN_MAX_AGE_SECONDS = 30
MAX_MAX_AGE_SECONDS = 900
SUPPORTED_PROVIDERS = {"greenhouse"}
TOOL_NAME = "get_latest_verification_code"

TOOL = {
    "name": TOOL_NAME,
    "title": "Get latest ATS verification code",
    "description": (
        "Return the latest recent code from an authenticated ATS email. "
        "Use it only in the Greenhouse form that triggered the email; message "
        "content is never exposed."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "enum": ["greenhouse"],
                "default": "greenhouse",
            },
            "max_age_seconds": {
                "type": "integer",
                "minimum": MIN_MAX_AGE_SECONDS,
                "maximum": MAX_MAX_AGE_SECONDS,
                "default": DEFAULT_MAX_AGE_SECONDS,
                "description": "Reject codes older than this many seconds.",
            },
        },
        "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "provider": {"type": "string"},
            "code": {"type": "string"},
            "received_at": {"type": "integer"},
            "age_seconds": {"type": "integer"},
        },
        "required": ["found", "provider"],
    },
}


def _response(status: int, body=None, *, protocol_version: str | None = None):
    headers = {
        "content-type": "application/json",
        "cache-control": "no-store",
    }
    if protocol_version:
        headers["mcp-protocol-version"] = protocol_version
    return {
        "statusCode": status,
        "headers": headers,
        "body": (
            "" if body is None else json.dumps(body, separators=(",", ":"))
        ),
    }


def _result(request_id, result, *, protocol_version: str | None = None):
    return _response(
        200,
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        protocol_version=protocol_version,
    )


def _error(request_id, code: int, message: str, *, status: int = 200):
    return _response(
        status,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
    )


def _tool_error(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": {"found": False, "provider": "greenhouse"},
        "isError": True,
    }


def _latest_code(provider: str, max_age_seconds: int) -> dict:
    now = int(time.time())
    response = table.query(
        KeyConditionExpression=Key("provider").eq(provider),
        ScanIndexForward=False,
        ConsistentRead=True,
        Limit=10,
        ProjectionExpression="#code, provider, received_at, expires_at",
        ExpressionAttributeNames={"#code": "code"},
    )
    for item in response.get("Items", []):
        try:
            received_at = int(item["received_at"])
            expires_at = int(item["expires_at"])
            code = str(item["code"])
        except (KeyError, TypeError, ValueError):
            continue
        if not re.fullmatch(r"[A-Za-z0-9]{8}", code):
            continue
        age_seconds = now - received_at
        if (
            received_at > now + 60
            or expires_at <= now
            or age_seconds > max_age_seconds
        ):
            continue
        age_seconds = max(0, age_seconds)
        payload = {
            "found": True,
            "provider": provider,
            "code": code,
            "received_at": received_at,
            "age_seconds": age_seconds,
        }
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Latest {provider} verification code: "
                        f"{code} (received {age_seconds}s ago)."
                    ),
                }
            ],
            "structuredContent": payload,
            "isError": False,
        }
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"No {provider} verification code was received within "
                    f"the last {max_age_seconds} seconds."
                ),
            }
        ],
        "structuredContent": {"found": False, "provider": provider},
        "isError": False,
    }


def _call_tool(arguments) -> dict:
    if not isinstance(arguments, dict):
        return _tool_error("Tool arguments must be an object.")
    unknown = set(arguments) - {"provider", "max_age_seconds"}
    if unknown:
        return _tool_error("Unsupported tool argument.")
    provider = arguments.get("provider", "greenhouse")
    if provider not in SUPPORTED_PROVIDERS:
        return _tool_error("Unsupported ATS provider.")
    max_age = arguments.get("max_age_seconds", DEFAULT_MAX_AGE_SECONDS)
    if (
        isinstance(max_age, bool)
        or not isinstance(max_age, int)
        or not MIN_MAX_AGE_SECONDS <= max_age <= MAX_MAX_AGE_SECONDS
    ):
        return _tool_error(
            f"max_age_seconds must be between {MIN_MAX_AGE_SECONDS} and "
            f"{MAX_MAX_AGE_SECONDS}."
        )
    return _latest_code(provider, max_age)


def lambda_handler(event, _context):
    method = event.get("requestContext", {}).get("http", {}).get("method")
    if method and method.upper() != "POST":
        response = _response(405, {"error": "Method not allowed"})
        response["headers"]["allow"] = "POST"
        return response

    raw_body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        except (ValueError, UnicodeError):
            return _error(None, -32700, "Invalid request encoding", status=400)
    try:
        request = json.loads(raw_body)
    except (TypeError, json.JSONDecodeError):
        return _error(None, -32700, "Invalid JSON", status=400)
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid JSON-RPC request", status=400)

    request_id = request.get("id")
    rpc_method = request.get("method")
    if rpc_method == "notifications/initialized":
        return _response(202)
    if request_id is None:
        return _response(202)
    if rpc_method == "initialize":
        params = request.get("params") or {}
        requested_version = params.get("protocolVersion")
        version = (
            requested_version
            if requested_version in SUPPORTED_PROTOCOL_VERSIONS
            else PROTOCOL_VERSION
        )
        return _result(
            request_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "portfolio-ats-verification",
                    "version": "1.0.0",
                },
            },
            protocol_version=version,
        )
    if rpc_method == "ping":
        return _result(request_id, {})
    if rpc_method == "tools/list":
        return _result(request_id, {"tools": [TOOL]})
    if rpc_method == "tools/call":
        params = request.get("params") or {}
        if not isinstance(params, dict) or params.get("name") != TOOL_NAME:
            return _error(request_id, -32602, "Unknown tool")
        return _result(request_id, _call_tool(params.get("arguments", {})))
    return _error(request_id, -32601, "Method not found")

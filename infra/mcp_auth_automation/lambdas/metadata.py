"""OAuth discovery and constrained client registration for remote MCPs.

The registration endpoint is deliberately not a general-purpose Cognito
client creator. It returns the existing public authorization-code client only
when every requested redirect URI and scope is already allowlisted. No secret
is issued and no AWS control-plane API is called.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def _json_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": (
                "no-store" if status_code != 200 else "max-age=3600"
            ),
        },
        "body": json.dumps(body, separators=(",", ":")),
    }


def _error(error: str, description: str) -> dict[str, Any]:
    return _json_response(
        400,
        {"error": error, "error_description": description},
    )


def _string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty array")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{field} must contain non-empty strings")
    return value


def _safe_redirect_diagnostic(uri: str) -> str:
    """Return a bounded callback identifier without query or fragment data."""
    parts = urlsplit(uri)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))[:256]


def _registration_response(
    request: dict[str, Any],
    *,
    client_id: str,
    allowed_callbacks: set[str],
    allowed_scopes: set[str],
) -> dict[str, Any]:
    """Validate RFC 7591 metadata and return the fixed public client."""
    try:
        redirect_uris = _string_list(
            request.get("redirect_uris"), field="redirect_uris"
        )
    except ValueError as exc:
        return _error("invalid_redirect_uri", str(exc))

    if len(redirect_uris) != len(set(redirect_uris)):
        return _error(
            "invalid_redirect_uri", "redirect_uris must not contain duplicates"
        )
    unregistered = [
        uri for uri in redirect_uris if uri not in allowed_callbacks
    ]
    if unregistered:
        return _error(
            "invalid_redirect_uri",
            "redirect_uri is not pre-registered: "
            f"{_safe_redirect_diagnostic(unregistered[0])}",
        )

    grant_types = request.get(
        "grant_types", ["authorization_code", "refresh_token"]
    )
    response_types = request.get("response_types", ["code"])
    try:
        grant_types = _string_list(grant_types, field="grant_types")
        response_types = _string_list(response_types, field="response_types")
    except ValueError as exc:
        return _error("invalid_client_metadata", str(exc))

    if "authorization_code" not in grant_types or any(
        grant not in {"authorization_code", "refresh_token"}
        for grant in grant_types
    ):
        return _error(
            "invalid_client_metadata",
            "only authorization_code and refresh_token grants are supported",
        )
    if set(response_types) != {"code"}:
        return _error(
            "invalid_client_metadata",
            "only the code response type is supported",
        )

    token_auth_method = request.get("token_endpoint_auth_method", "none")
    if token_auth_method != "none":
        return _error(
            "invalid_client_metadata",
            "the public client only supports token_endpoint_auth_method none",
        )

    requested_scope = request.get("scope")
    if requested_scope is None:
        scopes = []
    elif isinstance(requested_scope, str):
        scopes = requested_scope.split()
    else:
        return _error("invalid_client_metadata", "scope must be a string")
    if any(scope not in allowed_scopes for scope in scopes):
        return _error(
            "invalid_client_metadata", "one or more scopes are not allowed"
        )

    response: dict[str, Any] = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "redirect_uris": redirect_uris,
        "grant_types": grant_types,
        "response_types": response_types,
        "token_endpoint_auth_method": "none",
    }
    if scopes:
        response["scope"] = " ".join(scopes)
    if request.get("client_name"):
        response["client_name"] = str(request["client_name"])[:128]
    return _json_response(201, response)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    path = event.get("rawPath") or event.get("path") or ""
    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "GET"
    ).upper()

    docs = json.loads(os.environ["METADATA_DOCS"])
    if method == "GET":
        doc = docs.get(path)
        if doc is None:
            return _json_response(404, {})
        return _json_response(200, doc)

    if method == "POST" and path == "/oauth/register":
        try:
            request = json.loads(event.get("body") or "{}")
        except (TypeError, json.JSONDecodeError):
            return _error("invalid_client_metadata", "body must be valid JSON")
        if not isinstance(request, dict):
            return _error("invalid_client_metadata", "body must be an object")
        return _registration_response(
            request,
            client_id=os.environ["DCR_CLIENT_ID"],
            allowed_callbacks=set(
                json.loads(os.environ["DCR_ALLOWED_CALLBACK_URLS"])
            ),
            allowed_scopes=set(json.loads(os.environ["DCR_ALLOWED_SCOPES"])),
        )

    return _json_response(404, {})

"""Rotate a Cognito app-client secret through AWS Secrets Manager.

Cognito permits two active secrets for a confidential app client. A rotation
adds the pending secret, tests it against the protected MCP resource, promotes
it, and leaves the prior secret active for a short overlap. A scheduled cleanup
event removes the prior Cognito secret after that overlap.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, cast

import boto3
from botocore.exceptions import ClientError

cognito = boto3.client("cognito-idp")
secrets = boto3.client("secretsmanager")

MIN_OVERLAP_SECONDS = 60 * 60
COGNITO_PROPAGATION_TIMEOUT_SECONDS = 150
COGNITO_PROPAGATION_RETRY_SECONDS = 5
REQUIRED_FIELDS = {
    "client_id",
    "client_secret",
    "scopes",
    "server_url",
    "token_url",
    "user_pool_id",
}


def _load_secret(
    secret_id: str,
    *,
    version_id: str | None = None,
    version_stage: str | None = None,
) -> dict[str, Any]:
    request = {"SecretId": secret_id}
    if version_id is not None:
        request["VersionId"] = version_id
    if version_stage is not None:
        request["VersionStage"] = version_stage
    value = cast(
        dict[str, Any],
        json.loads(secrets.get_secret_value(**request)["SecretString"]),
    )
    missing = REQUIRED_FIELDS - value.keys()
    if missing:
        raise ValueError("MCP credential secret is missing required fields")
    if not isinstance(value["scopes"], list) or not value["scopes"]:
        raise ValueError("MCP credential secret must contain scopes")
    return value


def _client_secrets(credentials: dict[str, Any]) -> list[dict[str, Any]]:
    response = cognito.list_user_pool_client_secrets(
        UserPoolId=credentials["user_pool_id"],
        ClientId=credentials["client_id"],
    )
    return cast(list[dict[str, Any]], response.get("ClientSecrets", []))


def _delete_client_secret(credentials: dict[str, Any], secret_id: str) -> None:
    cognito.delete_user_pool_client_secret(
        UserPoolId=credentials["user_pool_id"],
        ClientId=credentials["client_id"],
        ClientSecretId=secret_id,
    )


def _pending_exists(secret_id: str, token: str) -> bool:
    try:
        _load_secret(
            secret_id,
            version_id=token,
            version_stage="AWSPENDING",
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {
            "ResourceNotFoundException",
            "InvalidRequestException",
        }:
            return False
        raise
    return True


def _create_secret(secret_id: str, token: str) -> None:
    if _pending_exists(secret_id, token):
        return

    current = _load_secret(secret_id, version_stage="AWSCURRENT")
    descriptors = _client_secrets(current)
    if len(descriptors) >= 2:
        current_remote_id = current.get("client_secret_id")
        if not current_remote_id:
            raise RuntimeError(
                "Cannot identify the active Cognito client secret; refusing "
                "to delete either credential"
            )
        stale = [
            item
            for item in descriptors
            if item.get("ClientSecretId") != current_remote_id
        ]
        if len(stale) != 1:
            raise RuntimeError("Unexpected Cognito client-secret state")
        _delete_client_secret(current, stale[0]["ClientSecretId"])

    response = cognito.add_user_pool_client_secret(
        UserPoolId=current["user_pool_id"],
        ClientId=current["client_id"],
    )
    descriptor = response["ClientSecretDescriptor"]
    pending = {
        **current,
        "client_secret": descriptor["ClientSecretValue"],
        "client_secret_id": descriptor["ClientSecretId"],
        "client_secret_created_at": int(
            descriptor["ClientSecretCreateDate"].timestamp()
            if hasattr(descriptor["ClientSecretCreateDate"], "timestamp")
            else descriptor["ClientSecretCreateDate"]
        ),
    }
    secrets.put_secret_value(
        SecretId=secret_id,
        ClientRequestToken=token,
        SecretString=json.dumps(pending, separators=(",", ":")),
        VersionStages=["AWSPENDING"],
    )


def _set_secret(secret_id: str, token: str) -> None:
    pending = _load_secret(
        secret_id,
        version_id=token,
        version_stage="AWSPENDING",
    )
    remote_ids = {
        item.get("ClientSecretId") for item in _client_secrets(pending)
    }
    if pending.get("client_secret_id") not in remote_ids:
        raise RuntimeError("Pending Cognito client secret is not active")


def _request_token(credentials: dict[str, Any]) -> str:
    basic = base64.b64encode(
        f"{credentials['client_id']}:{credentials['client_secret']}".encode()
    ).decode()
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "scope": " ".join(credentials["scopes"]),
        }
    ).encode()
    request = urllib.request.Request(
        credentials["token_url"],
        data=body,
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


def _call_initialize(credentials: dict[str, Any], access_token: str) -> None:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "portfolio-secret-rotation",
                    "version": "1",
                },
            },
        }
    ).encode()
    request = urllib.request.Request(
        credentials["server_url"],
        data=body,
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError("MCP initialize probe did not return HTTP 200")
        payload = json.load(response)
    if payload.get("result", {}).get("serverInfo", {}).get("name") != (
        "portfolio-ats-verification"
    ):
        raise RuntimeError(
            "MCP initialize probe returned an unexpected server"
        )


def _test_secret(secret_id: str, token: str) -> None:
    pending = _load_secret(
        secret_id,
        version_id=token,
        version_stage="AWSPENDING",
    )
    deadline = time.monotonic() + COGNITO_PROPAGATION_TIMEOUT_SECONDS
    while True:
        try:
            access_token = _request_token(pending)
            break
        except urllib.error.HTTPError as error:
            if error.code not in {400, 401} or time.monotonic() >= deadline:
                raise
            # New Cognito client secrets are eventually consistent. Keep the
            # expected propagation failure inside one Lambda invocation so a
            # successful rotation does not emit a transient Lambda error.
            time.sleep(COGNITO_PROPAGATION_RETRY_SECONDS)
    _call_initialize(pending, access_token)


def _finish_secret(secret_id: str, token: str) -> None:
    metadata = secrets.describe_secret(SecretId=secret_id)
    versions = metadata.get("VersionIdsToStages", {})
    if "AWSCURRENT" in versions.get(token, []):
        return
    current_version = next(
        (
            version_id
            for version_id, stages in versions.items()
            if "AWSCURRENT" in stages
        ),
        None,
    )
    if current_version is None:
        raise RuntimeError("Secret has no AWSCURRENT version")
    secrets.update_secret_version_stage(
        SecretId=secret_id,
        VersionStage="AWSCURRENT",
        MoveToVersionId=token,
        RemoveFromVersionId=current_version,
    )


def _cleanup(secret_id: str) -> dict[str, int | str]:
    current = _load_secret(secret_id, version_stage="AWSCURRENT")
    current_remote_id = current.get("client_secret_id")
    if not current_remote_id:
        return {"deleted": 0, "reason": "initial-secret"}
    created_at = current.get("client_secret_created_at")
    if not isinstance(created_at, (int, float)):
        raise RuntimeError(
            "Current Cognito client secret has no creation time"
        )
    now = time.time()
    if now - created_at < MIN_OVERLAP_SECONDS:
        return {"deleted": 0, "reason": "overlap"}
    deleted = 0
    for descriptor in _client_secrets(current):
        remote_id = descriptor.get("ClientSecretId")
        if remote_id and remote_id != current_remote_id:
            _delete_client_secret(current, remote_id)
            deleted += 1
    return {"deleted": deleted}


def _validate_rotation_request(event: dict[str, Any]) -> tuple[str, str, str]:
    secret_id = event["SecretId"]
    token = event["ClientRequestToken"]
    step = event["Step"]
    metadata = secrets.describe_secret(SecretId=secret_id)
    if not metadata.get("RotationEnabled"):
        raise RuntimeError("Secret rotation is not enabled")
    stages = metadata.get("VersionIdsToStages", {}).get(token)
    if stages is None:
        raise RuntimeError("Rotation token is not associated with this secret")
    if "AWSCURRENT" in stages:
        return secret_id, token, step
    if "AWSPENDING" not in stages:
        raise RuntimeError("Rotation token is not marked AWSPENDING")
    return secret_id, token, step


def lambda_handler(
    event: dict[str, Any], _context: Any
) -> dict[str, int | str]:
    if event.get("operation") == "cleanup":
        result = _cleanup(event["secret_id"])
        print(json.dumps({"operation": "cleanup", **result}))
        return result

    secret_id, token, step = _validate_rotation_request(event)
    handlers = {
        "createSecret": _create_secret,
        "setSecret": _set_secret,
        "testSecret": _test_secret,
        "finishSecret": _finish_secret,
    }
    try:
        handler = handlers[step]
    except KeyError as error:
        raise ValueError("Unsupported rotation step") from error
    handler(secret_id, token)
    print(json.dumps({"operation": "rotation", "step": step}))
    return {"step": step}

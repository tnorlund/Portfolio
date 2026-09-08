"""Unit tests for unattended MCP credential rotation and health checks."""

from __future__ import annotations

import importlib.util
import json
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

LAMBDA_DIR = Path(__file__).parents[1] / "mcp_auth_automation" / "lambdas"


class FakeSecrets:
    def __init__(self, current: dict, pending_token: str) -> None:
        self.values = {
            "current-version": json.dumps(current),
        }
        self.stages = {
            "current-version": ["AWSCURRENT"],
            pending_token: ["AWSPENDING"],
        }

    def describe_secret(self, **_kwargs):
        return {
            "RotationEnabled": True,
            "VersionIdsToStages": self.stages,
        }

    def get_secret_value(self, **kwargs):
        version_id = kwargs.get("VersionId")
        stage = kwargs.get("VersionStage")
        if version_id is None and stage is not None:
            version_id = next(
                (
                    candidate
                    for candidate, stages in self.stages.items()
                    if stage in stages
                ),
                None,
            )
        if version_id not in self.values:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ResourceNotFoundException",
                        "Message": "missing",
                    }
                },
                "GetSecretValue",
            )
        if stage and stage not in self.stages.get(version_id, []):
            raise ClientError(
                {
                    "Error": {
                        "Code": "InvalidRequestException",
                        "Message": "wrong stage",
                    }
                },
                "GetSecretValue",
            )
        return {"SecretString": self.values[version_id]}

    def put_secret_value(self, **kwargs):
        token = kwargs["ClientRequestToken"]
        self.values[token] = kwargs["SecretString"]
        self.stages[token] = kwargs["VersionStages"]

    def update_secret_version_stage(self, **kwargs):
        current = kwargs["RemoveFromVersionId"]
        pending = kwargs["MoveToVersionId"]
        self.stages[current] = ["AWSPREVIOUS"]
        self.stages[pending] = ["AWSPENDING", "AWSCURRENT"]


class FakeCognito:
    def __init__(self) -> None:
        self.descriptors = [
            {
                "ClientSecretId": "initial-id",
                "ClientSecretCreateDate": datetime.fromtimestamp(
                    100, tz=timezone.utc
                ),
            }
        ]
        self.deleted: list[str] = []

    def list_user_pool_client_secrets(self, **_kwargs):
        return {"ClientSecrets": list(self.descriptors)}

    def add_user_pool_client_secret(self, **_kwargs):
        descriptor = {
            "ClientSecretId": "rotated-id",
            "ClientSecretValue": "rotated-secret-value-1234567890",
            "ClientSecretCreateDate": datetime.fromtimestamp(
                1_000, tz=timezone.utc
            ),
        }
        self.descriptors.append(descriptor)
        return {"ClientSecretDescriptor": descriptor}

    def delete_user_pool_client_secret(self, **kwargs):
        secret_id = kwargs["ClientSecretId"]
        self.deleted.append(secret_id)
        self.descriptors = [
            item
            for item in self.descriptors
            if item["ClientSecretId"] != secret_id
        ]


def _load(monkeypatch, filename: str, clients: dict):
    monkeypatch.setattr(
        boto3,
        "client",
        lambda service: clients[service],
    )
    path = LAMBDA_DIR / filename
    spec = importlib.util.spec_from_file_location(filename[:-3], path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _credentials() -> dict:
    return {
        "client_id": "client-id",
        "client_secret": "initial-secret-value-1234567890",
        "scopes": ["portfolio-mcp/ats"],
        "server_url": "https://example.test/ats/mcp",
        "token_url": "https://auth.example.test/oauth2/token",
        "user_pool_id": "us-east-1_example",
    }


def test_rotation_promotes_tested_secret_and_cleans_up_overlap(
    monkeypatch,
) -> None:
    token = "rotation-token"
    secret_store = FakeSecrets(_credentials(), token)
    cognito = FakeCognito()
    module = _load(
        monkeypatch,
        "rotation.py",
        {"secretsmanager": secret_store, "cognito-idp": cognito},
    )
    tested: list[str] = []
    monkeypatch.setattr(module, "_request_token", lambda _creds: "access")
    monkeypatch.setattr(
        module,
        "_call_initialize",
        lambda credentials, _token: tested.append(
            credentials["client_secret"]
        ),
    )

    for step in ["createSecret", "setSecret", "testSecret", "finishSecret"]:
        module.lambda_handler(
            {
                "SecretId": "secret-arn",
                "ClientRequestToken": token,
                "Step": step,
            },
            None,
        )

    current = json.loads(
        secret_store.get_secret_value(
            SecretId="secret-arn", VersionStage="AWSCURRENT"
        )["SecretString"]
    )
    assert current["client_secret"] == "rotated-secret-value-1234567890"
    assert current["client_secret_id"] == "rotated-id"
    assert tested == ["rotated-secret-value-1234567890"]

    monkeypatch.setattr(module.time, "time", lambda: 2_000)
    assert module.lambda_handler(
        {"operation": "cleanup", "secret_id": "secret-arn"}, None
    ) == {"deleted": 0, "reason": "overlap"}
    assert cognito.deleted == []

    monkeypatch.setattr(module.time, "time", lambda: 10_000)
    assert module.lambda_handler(
        {"operation": "cleanup", "secret_id": "secret-arn"}, None
    ) == {"deleted": 1}
    assert cognito.deleted == ["initial-id"]


def test_canary_uses_current_secret_without_exposing_it(monkeypatch) -> None:
    class CanarySecrets:
        def get_secret_value(self, **kwargs):
            assert kwargs["VersionStage"] == "AWSCURRENT"
            return {"SecretString": json.dumps(_credentials())}

    module = _load(
        monkeypatch,
        "canary.py",
        {"secretsmanager": CanarySecrets()},
    )
    monkeypatch.setenv("MCP_CREDENTIALS_SECRET_ARN", "secret-arn")
    monkeypatch.setattr(module, "_access_token", lambda _creds: "access")
    monkeypatch.setattr(
        module,
        "_initialize",
        lambda _creds, _token: {
            "result": {"serverInfo": {"name": "portfolio-ats-verification"}}
        },
    )

    assert module.lambda_handler({}, None) == {"status": "ok"}


def test_rotation_absorbs_cognito_secret_propagation(monkeypatch) -> None:
    token = "rotation-token"
    secret_store = FakeSecrets(_credentials(), token)
    pending = {**_credentials(), "client_secret_id": "rotated-id"}
    secret_store.values[token] = json.dumps(pending)
    cognito = FakeCognito()
    module = _load(
        monkeypatch,
        "rotation.py",
        {"secretsmanager": secret_store, "cognito-idp": cognito},
    )
    attempts = 0

    def request_token(_credentials):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.HTTPError(
                "https://auth.example.test/oauth2/token",
                400,
                "eventual consistency",
                {},
                None,
            )
        return "access"

    initialized: list[str] = []
    monkeypatch.setattr(module, "_request_token", request_token)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        module,
        "_call_initialize",
        lambda _credentials, access_token: initialized.append(access_token),
    )

    module._test_secret("secret-arn", token)

    assert attempts == 2
    assert initialized == ["access"]

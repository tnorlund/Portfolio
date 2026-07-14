"""Unit tests for shared API route Lambda resources."""

# pylint: disable=redefined-outer-name

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest

from infra.components import route_lambda

CreatedResource = tuple[str, dict[str, Any], SimpleNamespace]
CreatedResources = dict[str, list[CreatedResource]]


@pytest.fixture
def mocked_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> CreatedResources:
    created: CreatedResources = {
        "roles": [],
        "policies": [],
        "inline_policies": [],
        "attachments": [],
        "functions": [],
        "log_groups": [],
    }

    def resource(kind: str, **outputs: Any) -> Callable[..., SimpleNamespace]:
        def create(resource_name: str, **kwargs: Any) -> SimpleNamespace:
            value = SimpleNamespace(resource_name=resource_name, **outputs)
            created[kind].append((resource_name, kwargs, value))
            return value

        return create

    monkeypatch.setattr(
        route_lambda.aws.iam,
        "Role",
        resource("roles", name="role-name", arn="role-arn", id="role-id"),
    )
    monkeypatch.setattr(
        route_lambda.aws.iam,
        "Policy",
        resource("policies", arn="policy-arn"),
    )
    monkeypatch.setattr(
        route_lambda.aws.iam,
        "RolePolicy",
        resource("inline_policies"),
    )
    monkeypatch.setattr(
        route_lambda.aws.iam,
        "RolePolicyAttachment",
        resource("attachments"),
    )
    monkeypatch.setattr(
        route_lambda.aws.lambda_,
        "Function",
        resource("functions", name="function-name", arn="function-arn"),
    )
    monkeypatch.setattr(
        route_lambda.aws.cloudwatch,
        "LogGroup",
        resource("log_groups"),
    )
    monkeypatch.setattr(route_lambda.pulumi, "get_stack", lambda: "dev")
    monkeypatch.setattr(
        route_lambda.pulumi.Output,
        "concat",
        lambda *values: "".join(values),
    )

    return created


def test_create_route_lambda_with_managed_policy(
    mocked_resources: CreatedResources,
) -> None:
    function_options = route_lambda.pulumi.ResourceOptions(
        ignore_changes=["layers"]
    )
    definition = route_lambda.RouteLambdaDefinition(
        role_name="api_receipts_lambda_role",
        basic_execution_attachment_name="api_receipts_lambda_basic_execution",
        function_name="api_receipts_GET_lambda",
        log_group_name="api_receipts_lambda_log_group",
        handler_directory="/tmp/handler",
        policy=route_lambda.ManagedPolicyDefinition(
            resource_name="api_receipts_lambda_policy",
            attachment_name="api_receipts_lambda_policy_attachment",
            document="policy-json",
            description="DynamoDB access",
        ),
        environment={"TABLE_NAME": "table-name"},
        layers=("layer-arn",),
        memory_size=1024,
        timeout=120,
        handler="index.lambda_handler",
        reserved_concurrent_executions=100,
        function_options=function_options,
    )

    resources = route_lambda.create_route_lambda(definition)

    assert mocked_resources["roles"][0][:2] == (
        "api_receipts_lambda_role",
        {"assume_role_policy": route_lambda.LAMBDA_ASSUME_ROLE_POLICY},
    )
    assert mocked_resources["policies"][0][:2] == (
        "api_receipts_lambda_policy",
        {"policy": "policy-json", "description": "DynamoDB access"},
    )
    assert [item[:2] for item in mocked_resources["attachments"]] == [
        (
            "api_receipts_lambda_policy_attachment",
            {"role": "role-name", "policy_arn": "policy-arn"},
        ),
        (
            "api_receipts_lambda_basic_execution",
            {
                "role": "role-name",
                "policy_arn": route_lambda.BASIC_EXECUTION_POLICY_ARN,
            },
        ),
    ]
    function_args = mocked_resources["functions"][0][1]
    assert function_args["runtime"] == "python3.12"
    assert function_args["architectures"] == ["arm64"]
    assert function_args["role"] == "role-arn"
    assert function_args["handler"] == "index.lambda_handler"
    assert function_args["layers"] == ["layer-arn"]
    assert function_args["environment"] == {
        "variables": {"TABLE_NAME": "table-name"}
    }
    assert function_args["memory_size"] == 1024
    assert function_args["timeout"] == 120
    assert function_args["reserved_concurrent_executions"] == 100
    assert function_args["opts"] is function_options
    assert function_args["tags"] == {"environment": "dev"}
    assert mocked_resources["log_groups"][0][:2] == (
        "api_receipts_lambda_log_group",
        {"retention_in_days": 30, "name": None},
    )
    assert cast(Any, resources.function) is mocked_resources["functions"][0][2]


def test_create_route_lambda_with_inline_policy_and_named_log_group(
    mocked_resources: CreatedResources,
) -> None:
    definition = route_lambda.RouteLambdaDefinition(
        role_name="api_timeline_lambda_role",
        basic_execution_attachment_name="api_timeline_basic_execution",
        function_name="api_timeline_GET_lambda",
        log_group_name="api_timeline_log_group",
        handler_directory="/tmp/handler",
        policy=route_lambda.InlinePolicyDefinition(
            resource_name="api_timeline_s3_policy",
            document="policy-json",
        ),
        use_function_log_group_name=True,
        log_retention_in_days=7,
    )

    resources = route_lambda.create_route_lambda(definition)

    assert mocked_resources["inline_policies"][0][:2] == (
        "api_timeline_s3_policy",
        {"role": "role-id", "policy": "policy-json"},
    )
    assert mocked_resources["log_groups"][0][:2] == (
        "api_timeline_log_group",
        {
            "retention_in_days": 7,
            "name": "/aws/lambda/function-name",
        },
    )
    assert resources.policy_attachment is None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"role_name": ""}, "role_name"),
        ({"memory_size": 0}, "memory_size"),
        ({"timeout": 0}, "timeout"),
        ({"log_retention_in_days": 0}, "log_retention_in_days"),
    ],
)
def test_route_lambda_definition_validates_inputs(
    changes: dict[str, object], message: str
) -> None:
    definition = route_lambda.RouteLambdaDefinition(
        role_name="role",
        basic_execution_attachment_name="basic",
        function_name="function",
        log_group_name="logs",
        handler_directory="/tmp/handler",
    )

    with pytest.raises(ValueError, match=message):
        replace(definition, **cast(Any, changes))


def test_route_lambda_definition_rejects_two_log_group_names() -> None:
    with pytest.raises(ValueError, match="choose either"):
        route_lambda.RouteLambdaDefinition(
            role_name="role",
            basic_execution_attachment_name="basic",
            function_name="function",
            log_group_name="logs",
            handler_directory="/tmp/handler",
            explicit_log_group_name="/aws/lambda/function",
            use_function_log_group_name=True,
        )

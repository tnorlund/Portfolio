"""Reusable IAM, Lambda, and log wiring for API route handlers.

Route modules pass every Pulumi logical name explicitly.  This module is a
plain factory rather than a ``ComponentResource`` so migrating an existing
route does not change its resource parent or URN.
"""

import json
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import pulumi
import pulumi_aws as aws

BASIC_EXECUTION_POLICY_ARN = (
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
)

LAMBDA_ASSUME_ROLE_POLICY = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Action": "sts:AssumeRole",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Effect": "Allow",
                "Sid": "",
            }
        ],
    }
)


@dataclass(frozen=True)
class ManagedPolicyDefinition:
    """A customer-managed IAM policy attached to the route Lambda role."""

    resource_name: str
    attachment_name: str
    document: pulumi.Input[str]
    description: str | None = None


@dataclass(frozen=True)
class InlinePolicyDefinition:
    """An inline IAM policy attached directly to the route Lambda role."""

    resource_name: str
    document: pulumi.Input[str]


RoutePolicyDefinition = ManagedPolicyDefinition | InlinePolicyDefinition


@dataclass(frozen=True)
class RouteLambdaDefinition:
    """Configuration for one file-archive Lambda used by an API route."""

    role_name: str
    basic_execution_attachment_name: str
    function_name: str
    log_group_name: str
    handler_directory: str
    policy: RoutePolicyDefinition | None = None
    environment: Mapping[str, pulumi.Input[str]] = field(default_factory=dict)
    layers: Sequence[pulumi.Input[str]] = field(default_factory=tuple)
    memory_size: int | None = None
    timeout: int | None = None
    log_retention_in_days: int = 30
    explicit_log_group_name: pulumi.Input[str] | None = None
    use_function_log_group_name: bool = False
    runtime: str = "python3.12"
    architecture: str = "arm64"
    handler: str = "index.handler"
    reserved_concurrent_executions: int | None = None
    function_options: pulumi.ResourceOptions | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "role_name",
            "basic_execution_attachment_name",
            "function_name",
            "log_group_name",
            "handler_directory",
            "handler",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must not be empty")
        if self.memory_size is not None and self.memory_size <= 0:
            raise ValueError("memory_size must be positive")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.log_retention_in_days <= 0:
            raise ValueError("log_retention_in_days must be positive")
        if (
            self.explicit_log_group_name is not None
            and self.use_function_log_group_name
        ):
            raise ValueError(
                "choose either explicit_log_group_name or "
                "use_function_log_group_name"
            )


@dataclass(frozen=True)
class RouteLambdaResources:
    """Pulumi resources created for a route Lambda."""

    role: aws.iam.Role
    basic_execution_attachment: aws.iam.RolePolicyAttachment
    function: aws.lambda_.Function
    log_group: aws.cloudwatch.LogGroup
    policy: aws.iam.Policy | aws.iam.RolePolicy | None
    policy_attachment: aws.iam.RolePolicyAttachment | None


def create_route_lambda(
    definition: RouteLambdaDefinition,
) -> RouteLambdaResources:
    """Create the common resources for a file-archive API Lambda."""
    role = aws.iam.Role(
        definition.role_name,
        assume_role_policy=LAMBDA_ASSUME_ROLE_POLICY,
    )

    policy_resource: aws.iam.Policy | aws.iam.RolePolicy | None = None
    policy_attachment: aws.iam.RolePolicyAttachment | None = None
    if isinstance(definition.policy, ManagedPolicyDefinition):
        managed_policy = aws.iam.Policy(
            definition.policy.resource_name,
            policy=definition.policy.document,
            description=definition.policy.description,
        )
        policy_resource = managed_policy
        policy_attachment = aws.iam.RolePolicyAttachment(
            definition.policy.attachment_name,
            role=role.name,
            policy_arn=managed_policy.arn,
        )
    elif isinstance(definition.policy, InlinePolicyDefinition):
        policy_resource = aws.iam.RolePolicy(
            definition.policy.resource_name,
            role=role.id,
            policy=definition.policy.document,
        )

    basic_execution_attachment = aws.iam.RolePolicyAttachment(
        definition.basic_execution_attachment_name,
        role=role.name,
        policy_arn=BASIC_EXECUTION_POLICY_ARN,
    )

    function = aws.lambda_.Function(
        definition.function_name,
        runtime=definition.runtime,
        architectures=[definition.architecture],
        role=role.arn,
        code=pulumi.AssetArchive(
            {".": pulumi.FileArchive(definition.handler_directory)}
        ),
        handler=definition.handler,
        layers=list(definition.layers) if definition.layers else None,
        environment=(
            {"variables": dict(definition.environment)}
            if definition.environment
            else None
        ),
        memory_size=definition.memory_size,
        timeout=definition.timeout,
        reserved_concurrent_executions=(
            definition.reserved_concurrent_executions
        ),
        tags={"environment": pulumi.get_stack()},
        opts=definition.function_options,
    )

    physical_log_group_name = definition.explicit_log_group_name
    if definition.use_function_log_group_name:
        physical_log_group_name = pulumi.Output.concat(
            "/aws/lambda/", function.name
        )
    log_group = aws.cloudwatch.LogGroup(
        definition.log_group_name,
        retention_in_days=definition.log_retention_in_days,
        name=physical_log_group_name,
    )

    return RouteLambdaResources(
        role=role,
        basic_execution_attachment=basic_execution_attachment,
        function=function,
        log_group=log_group,
        policy=policy_resource,
        policy_attachment=policy_attachment,
    )

"""Reusable API Gateway HTTP API to Lambda route wiring.

The Portfolio API has a mix of routes created at program import time and routes
whose Lambda functions are created later in ``__main__.py``.  Keeping the
three-resource wiring here gives both call sites the same behavior without
forcing those Lambda functions into the same construction phase.
"""

from dataclasses import dataclass
from typing import Sequence

import pulumi
import pulumi_aws as aws


@dataclass(frozen=True)
class RouteDefinition:
    """Configuration for one route sharing a Lambda integration."""

    resource_name: str
    route_key: str
    authorization_type: str | None = None

    def __post_init__(self) -> None:
        if not self.resource_name:
            raise ValueError("route resource_name must not be empty")
        if " " not in self.route_key:
            raise ValueError(
                "route_key must contain an HTTP method and path, for example "
                "'GET /health_check'"
            )


@dataclass(frozen=True)
class LambdaRouteResources:
    """Pulumi resources created for one Lambda-backed route group."""

    integration: aws.apigatewayv2.Integration
    routes: tuple[aws.apigatewayv2.Route, ...]
    permission: aws.lambda_.Permission | None

    @property
    def route(self) -> aws.apigatewayv2.Route:
        """Return the only route in a single-route registration."""
        if len(self.routes) != 1:
            raise ValueError("route is only available for single-route groups")
        return self.routes[0]


def create_lambda_routes(
    *,
    api: aws.apigatewayv2.Api,
    integration_name: str,
    lambda_function: aws.lambda_.Function,
    routes: Sequence[RouteDefinition],
    permission_name: str | None = None,
) -> LambdaRouteResources:
    """Connect one Lambda to one or more HTTP API routes.

    ``permission_name`` is optional because a Lambda needs only one broad
    permission per API even when multiple integrations point to it.  All names
    are explicit so adopting this helper never changes existing Pulumi URNs.
    """
    if not routes:
        raise ValueError("at least one route is required")

    integration = aws.apigatewayv2.Integration(
        integration_name,
        api_id=api.id,
        integration_type="AWS_PROXY",
        integration_uri=lambda_function.invoke_arn,
        integration_method="POST",
        payload_format_version="2.0",
    )

    route_resources = tuple(
        aws.apigatewayv2.Route(
            route.resource_name,
            api_id=api.id,
            route_key=route.route_key,
            authorization_type=route.authorization_type,
            target=integration.id.apply(
                lambda integration_id: f"integrations/{integration_id}"
            ),
            opts=pulumi.ResourceOptions(
                replace_on_changes=["route_key", "target"],
                delete_before_replace=True,
            ),
        )
        for route in routes
    )

    permission = None
    if permission_name is not None:
        permission = aws.lambda_.Permission(
            permission_name,
            action="lambda:InvokeFunction",
            function=lambda_function.name,
            principal="apigateway.amazonaws.com",
            source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
        )

    return LambdaRouteResources(
        integration=integration,
        routes=route_resources,
        permission=permission,
    )


def create_lambda_route(
    *,
    api: aws.apigatewayv2.Api,
    integration_name: str,
    route_name: str,
    route_key: str,
    lambda_function: aws.lambda_.Function,
    permission_name: str | None = None,
    authorization_type: str | None = None,
) -> LambdaRouteResources:
    """Convenience wrapper for the common one-integration, one-route case."""
    return create_lambda_routes(
        api=api,
        integration_name=integration_name,
        lambda_function=lambda_function,
        routes=(
            RouteDefinition(
                resource_name=route_name,
                route_key=route_key,
                authorization_type=authorization_type,
            ),
        ),
        permission_name=permission_name,
    )

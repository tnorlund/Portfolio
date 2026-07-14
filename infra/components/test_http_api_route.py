"""Unit tests for the shared HTTP API route wiring."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from infra.components import http_api_route


class FakeOutput:
    """Minimal Output stand-in for checking apply transformations."""

    def __init__(self, value: str) -> None:
        self.value = value

    def apply(self, callback):
        return callback(self.value)


@pytest.fixture
def mocked_aws(monkeypatch):
    integrations = []
    routes = []
    permissions = []

    def integration(resource_name, **kwargs):
        resource = SimpleNamespace(id=FakeOutput("integration-id"))
        integrations.append((resource_name, kwargs, resource))
        return resource

    def route(resource_name, **kwargs):
        resource = SimpleNamespace(resource_name=resource_name)
        routes.append((resource_name, kwargs, resource))
        return resource

    def permission(resource_name, **kwargs):
        resource = SimpleNamespace(resource_name=resource_name)
        permissions.append((resource_name, kwargs, resource))
        return resource

    monkeypatch.setattr(
        http_api_route.aws.apigatewayv2, "Integration", integration
    )
    monkeypatch.setattr(http_api_route.aws.apigatewayv2, "Route", route)
    monkeypatch.setattr(http_api_route.aws.lambda_, "Permission", permission)
    monkeypatch.setattr(http_api_route.pulumi, "ResourceOptions", Mock)

    return integrations, routes, permissions


def test_create_lambda_route_preserves_names_and_gateway_settings(mocked_aws):
    integrations, routes, permissions = mocked_aws
    api = SimpleNamespace(
        id="api-id", execution_arn=FakeOutput("arn:aws:execute-api:api-id")
    )
    function = SimpleNamespace(
        name="function-name", invoke_arn="function-invoke-arn"
    )

    resources = http_api_route.create_lambda_route(
        api=api,
        integration_name="health_check_lambda_integration",
        route_name="health_check_route",
        route_key="GET /health_check",
        lambda_function=function,
        permission_name="health_check_lambda_permission",
    )

    assert integrations[0][:2] == (
        "health_check_lambda_integration",
        {
            "api_id": "api-id",
            "integration_type": "AWS_PROXY",
            "integration_uri": "function-invoke-arn",
            "integration_method": "POST",
            "payload_format_version": "2.0",
        },
    )
    assert routes[0][0] == "health_check_route"
    assert routes[0][1]["route_key"] == "GET /health_check"
    assert routes[0][1]["target"] == "integrations/integration-id"
    assert permissions[0][:2] == (
        "health_check_lambda_permission",
        {
            "action": "lambda:InvokeFunction",
            "function": "function-name",
            "principal": "apigateway.amazonaws.com",
            "source_arn": "arn:aws:execute-api:api-id/*/*",
        },
    )
    assert resources.route is resources.routes[0]


def test_create_lambda_routes_reuses_integration_and_permission(mocked_aws):
    integrations, routes, permissions = mocked_aws
    api = SimpleNamespace(id="api-id", execution_arn=FakeOutput("api-arn"))
    function = SimpleNamespace(name="lambda-name", invoke_arn="invoke-arn")

    resources = http_api_route.create_lambda_routes(
        api=api,
        integration_name="layoutlm_inference_lambda_integration",
        lambda_function=function,
        routes=(
            http_api_route.RouteDefinition(
                "layoutlm_inference_route", "GET /layoutlm_inference"
            ),
            http_api_route.RouteDefinition(
                "layoutlm_inference_cache_route",
                "GET /layoutlm-inference-cache",
            ),
        ),
        permission_name="layoutlm_inference_lambda_permission",
    )

    assert len(integrations) == 1
    assert [name for name, _, _ in routes] == [
        "layoutlm_inference_route",
        "layoutlm_inference_cache_route",
    ]
    assert len(permissions) == 1
    assert len(resources.routes) == 2
    with pytest.raises(ValueError, match="single-route"):
        _ = resources.route


def test_route_definition_requires_a_resource_name():
    with pytest.raises(ValueError, match="resource_name"):
        http_api_route.RouteDefinition("", "GET /health")


def test_route_definition_requires_method_and_path():
    with pytest.raises(ValueError, match="HTTP method and path"):
        http_api_route.RouteDefinition("health_route", "/health")


def test_create_lambda_routes_requires_at_least_one_route(mocked_aws):
    api = SimpleNamespace(id="api-id", execution_arn=FakeOutput("api-arn"))
    function = SimpleNamespace(name="lambda-name", invoke_arn="invoke-arn")

    with pytest.raises(ValueError, match="at least one route"):
        http_api_route.create_lambda_routes(
            api=api,
            integration_name="unused",
            lambda_function=function,
            routes=(),
        )

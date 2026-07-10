"""Shared OAuth ingress for remotely hosted MCP servers.

The Lambda Function URLs remain available behind ``AWS_IAM`` for callers
that can sign requests. Off-the-shelf MCP clients use this API Gateway and
obtain OAuth access tokens from the shared Cognito user pool.
"""

import hashlib
import json
from typing import List, Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Config, Input, Output, ResourceOptions

_RESOURCE_SERVER_ID = "portfolio-mcp"
_DEFAULT_CALLBACK_URLS = [
    "http://localhost:8765/callback",
    "http://127.0.0.1:8765/callback",
    "http://localhost:6274/oauth/callback",
]


def _callback_urls(config: Config) -> List[str]:
    """Return configured OAuth callbacks with safe local defaults."""
    value = config.get_object("mcpOAuthCallbackUrls")
    if value is None:
        return _DEFAULT_CALLBACK_URLS
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(
            "portfolio:mcpOAuthCallbackUrls must be a non-empty JSON list "
            "of callback URLs"
        )
    if not value:
        raise ValueError(
            "portfolio:mcpOAuthCallbackUrls must contain at least one URL"
        )
    return value


class McpAuthGateway(ComponentResource):
    """Cognito-protected API Gateway routes for receipt and glyph MCP."""

    def __init__(
        self,
        name: str,
        *,
        receipt_lambda: aws.lambda_.Function,
        glyph_lambda: aws.lambda_.Function,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("portfolio:infra:McpAuthGateway", name, None, opts)

        stack = pulumi.get_stack()
        config = Config("portfolio")
        region = aws.get_region().region
        account_id = aws.get_caller_identity().account_id
        child_opts = ResourceOptions(parent=self)

        self.user_pool = aws.cognito.UserPool(
            f"{name}-users",
            name=f"{name}-{stack}",
            username_attributes=["email"],
            auto_verified_attributes=["email"],
            admin_create_user_config={
                "allow_admin_create_user_only": True,
            },
            account_recovery_setting={
                "recovery_mechanisms": [
                    {"name": "verified_email", "priority": 1}
                ]
            },
            password_policy={
                "minimum_length": 16,
                "require_lowercase": True,
                "require_numbers": True,
                "require_symbols": True,
                "require_uppercase": True,
                "temporary_password_validity_days": 7,
            },
            opts=child_opts,
        )

        self.resource_server = aws.cognito.ResourceServer(
            f"{name}-resource-server",
            identifier=_RESOURCE_SERVER_ID,
            name="Portfolio MCP servers",
            user_pool_id=self.user_pool.id,
            scopes=[
                {
                    "scope_name": "receipt",
                    "scope_description": "Use receipt MCP tools",
                },
                {
                    "scope_name": "glyph",
                    "scope_description": "Use glyph MCP tools",
                },
            ],
            opts=child_opts,
        )

        callbacks = _callback_urls(config)
        self.interactive_client = aws.cognito.UserPoolClient(
            f"{name}-interactive-client",
            name=f"{name}-{stack}-interactive",
            user_pool_id=self.user_pool.id,
            generate_secret=False,
            allowed_oauth_flows_user_pool_client=True,
            allowed_oauth_flows=["code"],
            allowed_oauth_scopes=[
                "openid",
                "email",
                f"{_RESOURCE_SERVER_ID}/receipt",
                f"{_RESOURCE_SERVER_ID}/glyph",
            ],
            callback_urls=callbacks,
            default_redirect_uri=callbacks[0],
            supported_identity_providers=["COGNITO"],
            enable_token_revocation=True,
            prevent_user_existence_errors="ENABLED",
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.resource_server],
            ),
        )

        self.automation_client = aws.cognito.UserPoolClient(
            f"{name}-receipt-automation-client",
            name=f"{name}-{stack}-receipt-automation",
            user_pool_id=self.user_pool.id,
            generate_secret=True,
            allowed_oauth_flows_user_pool_client=True,
            allowed_oauth_flows=["client_credentials"],
            allowed_oauth_scopes=[
                f"{_RESOURCE_SERVER_ID}/receipt",
            ],
            enable_token_revocation=True,
            prevent_user_existence_errors="ENABLED",
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.resource_server],
            ),
        )

        domain_prefix = f"portfolio-mcp-{account_id}-{stack}".lower()
        self.domain = aws.cognito.UserPoolDomain(
            f"{name}-domain",
            domain=domain_prefix,
            user_pool_id=self.user_pool.id,
            opts=child_opts,
        )
        self.issuer_url = Output.format(
            "https://cognito-idp.{}.amazonaws.com/{}",
            region,
            self.user_pool.id,
        )
        self.token_url = Output.format(
            "https://{}.auth.{}.amazoncognito.com/oauth2/token",
            domain_prefix,
            region,
        )

        automation_secret = aws.secretsmanager.Secret(
            f"{name}-receipt-automation-credentials",
            name=f"/{stack}/mcp/oauth/receipt-automation-client",
            description=(
                "OAuth client credentials for scheduled receipt MCP callers"
            ),
            opts=child_opts,
        )
        automation_credentials: Output[str] = Output.json_dumps(
            {
                "client_id": self.automation_client.id,
                "client_secret": self.automation_client.client_secret,
                "token_url": self.token_url,
                "scopes": [f"{_RESOURCE_SERVER_ID}/receipt"],
            }
        )
        aws.secretsmanager.SecretVersion(
            f"{name}-receipt-automation-credentials-version",
            secret_id=automation_secret.id,
            secret_string=Output.secret(automation_credentials),
            opts=child_opts,
        )
        self.automation_secret_arn = automation_secret.arn

        self.api = aws.apigateway.RestApi(
            f"{name}-api",
            name=f"{name}-{stack}",
            description="OAuth-protected ingress for Portfolio MCP servers",
            endpoint_configuration={"types": "REGIONAL"},
            opts=child_opts,
        )
        self.authorizer = aws.apigateway.Authorizer(
            f"{name}-authorizer",
            name=f"{name}-{stack}-cognito",
            rest_api=self.api.id,
            type="COGNITO_USER_POOLS",
            provider_arns=[self.user_pool.arn],
            identity_source="method.request.header.Authorization",
            opts=child_opts,
        )

        route_resources: List[pulumi.Resource] = []
        route_resources.extend(
            self._add_mcp_route(
                "receipt",
                receipt_lambda,
                f"{_RESOURCE_SERVER_ID}/receipt",
                region,
            )
        )
        route_resources.extend(
            self._add_mcp_route(
                "glyph",
                glyph_lambda,
                f"{_RESOURCE_SERVER_ID}/glyph",
                region,
            )
        )

        base_url = Output.format(
            "https://{}.execute-api.{}.amazonaws.com/{}",
            self.api.id,
            region,
            stack,
        )
        self.receipt_url = Output.format("{}/receipt/mcp", base_url)
        self.glyph_url = Output.format("{}/glyph/mcp", base_url)

        well_known = aws.apigateway.Resource(
            f"{name}-well-known",
            rest_api=self.api.id,
            parent_id=self.api.root_resource_id,
            path_part=".well-known",
            opts=child_opts,
        )
        protected_resource = aws.apigateway.Resource(
            f"{name}-protected-resource",
            rest_api=self.api.id,
            parent_id=well_known.id,
            path_part="oauth-protected-resource",
            opts=child_opts,
        )
        metadata_resources: List[pulumi.Resource] = [
            well_known,
            protected_resource,
        ]
        metadata_resources.extend(
            self._add_metadata_route(
                "receipt",
                self.receipt_url,
                f"{_RESOURCE_SERVER_ID}/receipt",
                protected_resource,
            )
        )
        metadata_resources.extend(
            self._add_metadata_route(
                "glyph",
                self.glyph_url,
                f"{_RESOURCE_SERVER_ID}/glyph",
                protected_resource,
            )
        )

        aws.apigateway.Response(
            f"{name}-unauthorized-response",
            rest_api_id=self.api.id,
            response_type="UNAUTHORIZED",
            status_code="401",
            response_parameters={
                "gatewayresponse.header.WWW-Authenticate": "'Bearer'"
            },
            response_templates={
                "application/json": json.dumps(
                    {"error": "authentication_required"}
                )
            },
            opts=child_opts,
        )
        aws.apigateway.Response(
            f"{name}-forbidden-response",
            rest_api_id=self.api.id,
            response_type="ACCESS_DENIED",
            status_code="403",
            response_parameters={
                "gatewayresponse.header.WWW-Authenticate": (
                    "'Bearer error=\"insufficient_scope\"'"
                )
            },
            response_templates={
                "application/json": json.dumps({"error": "insufficient_scope"})
            },
            opts=child_opts,
        )

        deployment_inputs = [
            resource.urn for resource in route_resources + metadata_resources
        ]
        redeployment = Output.all(*deployment_inputs).apply(
            lambda values: hashlib.sha256(
                json.dumps(values, sort_keys=True).encode("utf-8")
            ).hexdigest()
        )
        deployment = aws.apigateway.Deployment(
            f"{name}-deployment",
            rest_api=self.api.id,
            triggers={"redeployment": redeployment},
            opts=ResourceOptions(
                parent=self,
                depends_on=route_resources + metadata_resources,
            ),
        )
        self.stage = aws.apigateway.Stage(
            f"{name}-stage",
            rest_api=self.api.id,
            deployment=deployment.id,
            stage_name=stack,
            opts=child_opts,
        )

        self.register_outputs(
            {
                "receipt_url": self.receipt_url,
                "glyph_url": self.glyph_url,
                "issuer_url": self.issuer_url,
                "interactive_client_id": self.interactive_client.id,
                "automation_secret_arn": self.automation_secret_arn,
            }
        )

    def _add_mcp_route(
        self,
        route_name: str,
        lambda_function: aws.lambda_.Function,
        scope: str,
        region: str,
    ) -> List[pulumi.Resource]:
        """Add ``/<name>/mcp`` and protect it with a custom scope."""
        parent = aws.apigateway.Resource(
            f"mcp-auth-{route_name}",
            rest_api=self.api.id,
            parent_id=self.api.root_resource_id,
            path_part=route_name,
            opts=ResourceOptions(parent=self),
        )
        resource = aws.apigateway.Resource(
            f"mcp-auth-{route_name}-mcp",
            rest_api=self.api.id,
            parent_id=parent.id,
            path_part="mcp",
            opts=ResourceOptions(parent=self),
        )
        method = aws.apigateway.Method(
            f"mcp-auth-{route_name}-method",
            rest_api=self.api.id,
            resource_id=resource.id,
            http_method="ANY",
            authorization="COGNITO_USER_POOLS",
            authorizer_id=self.authorizer.id,
            authorization_scopes=[scope],
            opts=ResourceOptions(parent=self),
        )
        integration_uri = Output.format(
            "arn:aws:apigateway:{}:lambda:path/2015-03-31/functions/{}"
            "/invocations",
            region,
            lambda_function.arn,
        )
        integration = aws.apigateway.Integration(
            f"mcp-auth-{route_name}-integration",
            rest_api=self.api.id,
            resource_id=resource.id,
            http_method=method.http_method,
            integration_http_method="POST",
            type="AWS_PROXY",
            uri=integration_uri,
            timeout_milliseconds=29000,
            opts=ResourceOptions(parent=self),
        )
        permission = aws.lambda_.Permission(
            f"mcp-auth-{route_name}-invoke",
            action="lambda:InvokeFunction",
            function=lambda_function.name,
            principal="apigateway.amazonaws.com",
            source_arn=self.api.execution_arn.apply(
                lambda arn: f"{arn}/*/*/{route_name}/mcp"
            ),
            opts=ResourceOptions(parent=self),
        )
        return [parent, resource, method, integration, permission]

    def _add_metadata_route(
        self,
        route_name: str,
        resource_url: Input[str],
        scope: str,
        protected_resource: aws.apigateway.Resource,
    ) -> List[pulumi.Resource]:
        """Publish RFC 9728 metadata without requiring a bearer token."""
        server = aws.apigateway.Resource(
            f"mcp-auth-metadata-server-{route_name}",
            rest_api=self.api.id,
            parent_id=protected_resource.id,
            path_part=route_name,
            opts=ResourceOptions(parent=self),
        )
        resource = aws.apigateway.Resource(
            f"mcp-auth-metadata-mcp-{route_name}",
            rest_api=self.api.id,
            parent_id=server.id,
            path_part="mcp",
            opts=ResourceOptions(parent=self),
        )
        method = aws.apigateway.Method(
            f"mcp-auth-metadata-method-{route_name}",
            rest_api=self.api.id,
            resource_id=resource.id,
            http_method="GET",
            authorization="NONE",
            opts=ResourceOptions(parent=self),
        )
        integration = aws.apigateway.Integration(
            f"mcp-auth-metadata-integration-{route_name}",
            rest_api=self.api.id,
            resource_id=resource.id,
            http_method=method.http_method,
            type="MOCK",
            passthrough_behavior="NEVER",
            request_templates={
                "application/json": json.dumps({"statusCode": 200})
            },
            opts=ResourceOptions(parent=self),
        )
        method_response = aws.apigateway.MethodResponse(
            f"mcp-auth-metadata-method-response-{route_name}",
            rest_api=self.api.id,
            resource_id=resource.id,
            http_method=method.http_method,
            status_code="200",
            response_parameters={
                "method.response.header.Content-Type": True,
                "method.response.header.Cache-Control": True,
            },
            opts=ResourceOptions(parent=self),
        )
        metadata = Output.all(resource_url, self.issuer_url).apply(
            lambda values: json.dumps(
                {
                    "resource": values[0],
                    "authorization_servers": [values[1]],
                    "scopes_supported": [scope],
                    "bearer_methods_supported": ["header"],
                }
            )
        )
        integration_response = aws.apigateway.IntegrationResponse(
            f"mcp-auth-metadata-integration-response-{route_name}",
            rest_api=self.api.id,
            resource_id=resource.id,
            http_method=method.http_method,
            status_code=method_response.status_code,
            response_parameters={
                "method.response.header.Content-Type": "'application/json'",
                "method.response.header.Cache-Control": "'max-age=3600'",
            },
            response_templates={"application/json": metadata},
            opts=ResourceOptions(parent=self),
        )
        return [
            server,
            resource,
            method,
            integration,
            method_response,
            integration_response,
        ]

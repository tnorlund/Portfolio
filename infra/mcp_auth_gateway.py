"""Shared OAuth ingress for remotely hosted MCP servers.

The Lambda Function URLs remain available behind ``AWS_IAM`` for callers
that can sign requests. Off-the-shelf MCP clients use this HTTP API and
obtain OAuth access tokens from the shared Cognito user pool.

The API is an HTTP API (v2) on the ``$default`` stage deliberately: v2
URLs have no stage path prefix, so the RFC 9728 well-known location
(``/.well-known/oauth-protected-resource/<server>/mcp``) derives
correctly from each MCP resource URL — a REST API's ``/{stage}/`` prefix
breaks that derivation, and REST gateway responses cannot emit a
per-route ``WWW-Authenticate`` hint to compensate ($context variables do
not interpolate inside static response parameters).
"""

import json
from typing import List, Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Config, Output, ResourceOptions

_RESOURCE_SERVER_ID = "portfolio-mcp"
_DEFAULT_CALLBACK_URLS = [
    # claude.ai / Claude desktop custom-connector OAuth callbacks — the
    # primary remote clients for both MCP servers.
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    # Local development: mcp-remote default and MCP Inspector.
    "http://localhost:8765/callback",
    "http://127.0.0.1:8765/callback",
    "http://localhost:6274/oauth/callback",
]

# Serves the RFC 9728 protected-resource metadata documents. HTTP APIs
# have no mock integrations, so a minimal Lambda answers the well-known
# routes from an env-var lookup table.
_METADATA_HANDLER_CODE = """\
import json
import os

DOCS = json.loads(os.environ["METADATA_DOCS"])


def handler(event, context):
    path = event.get("rawPath") or event.get("path") or ""
    doc = DOCS.get(path)
    if doc is None:
        return {"statusCode": 404, "body": "{}"}
    return {
        "statusCode": 200,
        "headers": {
            "content-type": "application/json",
            "cache-control": "max-age=3600",
        },
        "body": json.dumps(doc),
    }
"""


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
        raise ValueError("portfolio:mcpOAuthCallbackUrls must contain at least one URL")
    return value


class McpAuthGateway(ComponentResource):
    """Cognito-protected HTTP API routes for receipt and glyph MCP."""

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
            deletion_protection="ACTIVE",
            username_attributes=["email"],
            auto_verified_attributes=["email"],
            admin_create_user_config={
                "allow_admin_create_user_only": True,
            },
            account_recovery_setting={
                "recovery_mechanisms": [{"name": "verified_email", "priority": 1}]
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
            description=("OAuth client credentials for scheduled receipt MCP callers"),
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

        # ------------------------------------------------------------
        # HTTP API ($default stage — no path prefix, see module doc)
        # ------------------------------------------------------------
        self.api = aws.apigatewayv2.Api(
            f"{name}-api",
            name=f"{name}-{stack}",
            protocol_type="HTTP",
            description="OAuth-protected ingress for Portfolio MCP servers",
            cors_configuration={
                "allow_origins": ["*"],
                "allow_methods": ["GET", "POST", "OPTIONS"],
                "allow_headers": [
                    "authorization",
                    "content-type",
                    "mcp-protocol-version",
                    "mcp-session-id",
                ],
                "max_age": 3600,
            },
            opts=child_opts,
        )
        self.receipt_url = Output.format("{}/receipt/mcp", self.api.api_endpoint)
        self.glyph_url = Output.format("{}/glyph/mcp", self.api.api_endpoint)

        # Cognito access tokens carry the client id in the client_id
        # claim; HTTP API JWT authorizers accept it in place of aud.
        self.authorizer = aws.apigatewayv2.Authorizer(
            f"{name}-authorizer",
            api_id=self.api.id,
            authorizer_type="JWT",
            name=f"{name}-{stack}-cognito-jwt",
            identity_sources=["$request.header.Authorization"],
            jwt_configuration={
                "issuer": self.issuer_url,
                "audiences": [
                    self.interactive_client.id,
                    self.automation_client.id,
                ],
            },
            opts=child_opts,
        )

        for route_name, lambda_function in (
            ("receipt", receipt_lambda),
            ("glyph", glyph_lambda),
        ):
            integration = aws.apigatewayv2.Integration(
                f"mcp-auth-{route_name}-integration",
                api_id=self.api.id,
                integration_type="AWS_PROXY",
                integration_uri=lambda_function.arn,
                payload_format_version="2.0",
                opts=child_opts,
            )
            aws.apigatewayv2.Route(
                f"mcp-auth-{route_name}-route",
                api_id=self.api.id,
                route_key=f"ANY /{route_name}/mcp",
                target=integration.id.apply(lambda iid: f"integrations/{iid}"),
                authorization_type="JWT",
                authorizer_id=self.authorizer.id,
                authorization_scopes=[f"{_RESOURCE_SERVER_ID}/{route_name}"],
                opts=child_opts,
            )
            aws.lambda_.Permission(
                f"mcp-auth-{route_name}-invoke",
                action="lambda:InvokeFunction",
                function=lambda_function.name,
                principal="apigateway.amazonaws.com",
                source_arn=self.api.execution_arn.apply(
                    lambda arn, rn=route_name: f"{arn}/*/*/{rn}/mcp"
                ),
                opts=child_opts,
            )

        # RFC 9728 metadata: the well-known location derives from the
        # resource URL because $default has no stage prefix.
        metadata_role = aws.iam.Role(
            f"{name}-metadata-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=child_opts,
        )
        aws.iam.RolePolicyAttachment(
            f"{name}-metadata-role-logs",
            role=metadata_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/" "AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=metadata_role),
        )
        metadata_docs = Output.json_dumps(
            {
                "/.well-known/oauth-protected-resource/receipt/mcp": {
                    "resource": self.receipt_url,
                    "authorization_servers": [self.issuer_url],
                    "scopes_supported": [f"{_RESOURCE_SERVER_ID}/receipt"],
                    "bearer_methods_supported": ["header"],
                },
                "/.well-known/oauth-protected-resource/glyph/mcp": {
                    "resource": self.glyph_url,
                    "authorization_servers": [self.issuer_url],
                    "scopes_supported": [f"{_RESOURCE_SERVER_ID}/glyph"],
                    "bearer_methods_supported": ["header"],
                },
            }
        )
        metadata_lambda = aws.lambda_.Function(
            f"{name}-metadata",
            role=metadata_role.arn,
            runtime="python3.12",
            handler="index.handler",
            timeout=5,
            memory_size=128,
            code=pulumi.AssetArchive(
                {"index.py": pulumi.StringAsset(_METADATA_HANDLER_CODE)}
            ),
            environment={"variables": {"METADATA_DOCS": metadata_docs}},
            opts=child_opts,
        )
        metadata_integration = aws.apigatewayv2.Integration(
            f"{name}-metadata-integration",
            api_id=self.api.id,
            integration_type="AWS_PROXY",
            integration_uri=metadata_lambda.arn,
            payload_format_version="2.0",
            opts=child_opts,
        )
        for route_name in ("receipt", "glyph"):
            aws.apigatewayv2.Route(
                f"{name}-metadata-route-{route_name}",
                api_id=self.api.id,
                route_key=(
                    "GET /.well-known/oauth-protected-resource" f"/{route_name}/mcp"
                ),
                target=metadata_integration.id.apply(lambda iid: f"integrations/{iid}"),
                authorization_type="NONE",
                opts=child_opts,
            )
        aws.lambda_.Permission(
            f"{name}-metadata-invoke",
            action="lambda:InvokeFunction",
            function=metadata_lambda.name,
            principal="apigateway.amazonaws.com",
            source_arn=self.api.execution_arn.apply(
                lambda arn: f"{arn}/*/*/.well-known/*"
            ),
            opts=child_opts,
        )

        self.stage = aws.apigatewayv2.Stage(
            f"{name}-stage",
            api_id=self.api.id,
            name="$default",
            auto_deploy=True,
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

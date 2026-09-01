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
import os
from typing import List, Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Config, Output, ResourceOptions

_RESOURCE_SERVER_ID = "portfolio-mcp"
_DEFAULT_CALLBACK_URLS = [
    # Hosted and desktop connector callbacks for the supported MCP clients.
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "https://www.cursor.com/agents/mcp/oauth/callback",
    "http://localhost:8787/callback",
    # Local development: mcp-remote default and MCP Inspector.
    "http://localhost:8765/callback",
    "http://127.0.0.1:8765/callback",
    "http://localhost:6274/oauth/callback",
]

_DEFAULT_REFRESH_TOKEN_VALIDITY_DAYS = 30
_DEFAULT_ACCESS_TOKEN_VALIDITY_HOURS = 1
_AUTOMATION_LAMBDA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "mcp_auth_automation",
    "lambdas",
)

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
        raise ValueError(
            "portfolio:mcpOAuthCallbackUrls must contain at least one URL"
        )
    return value


def _token_validity(config: Config) -> tuple[int, int]:
    """Return validated refresh-day and access-hour lifetimes."""
    configured_refresh_days = config.get_int(
        "mcpOAuthRefreshTokenValidityDays"
    )
    configured_access_hours = config.get_int(
        "mcpOAuthAccessTokenValidityHours"
    )
    refresh_days = configured_refresh_days
    if refresh_days is None:
        refresh_days = _DEFAULT_REFRESH_TOKEN_VALIDITY_DAYS
    access_hours = configured_access_hours
    if access_hours is None:
        access_hours = _DEFAULT_ACCESS_TOKEN_VALIDITY_HOURS

    if not 1 <= refresh_days <= 3650:
        raise ValueError(
            "portfolio:mcpOAuthRefreshTokenValidityDays must be between "
            "1 and 3650"
        )
    if not 1 <= access_hours <= 24:
        raise ValueError(
            "portfolio:mcpOAuthAccessTokenValidityHours must be between "
            "1 and 24"
        )

    return refresh_days, access_hours


class McpAuthGateway(ComponentResource):
    """Cognito-protected HTTP API routes for Portfolio MCP servers."""

    def __init__(
        self,
        name: str,
        *,
        receipt_lambda: aws.lambda_.Function,
        glyph_lambda: aws.lambda_.Function,
        ats_lambda: Optional[aws.lambda_.Function] = None,
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

        routes = [
            ("receipt", receipt_lambda, "Use receipt MCP tools"),
            ("glyph", glyph_lambda, "Use glyph MCP tools"),
        ]
        if ats_lambda is not None:
            routes.append(
                (
                    "ats",
                    ats_lambda,
                    "Read recent ATS verification codes",
                )
            )

        self.resource_server = aws.cognito.ResourceServer(
            f"{name}-resource-server",
            identifier=_RESOURCE_SERVER_ID,
            name="Portfolio MCP servers",
            user_pool_id=self.user_pool.id,
            scopes=[
                {
                    "scope_name": route_name,
                    "scope_description": description,
                }
                for route_name, _function, description in routes
            ],
            opts=child_opts,
        )

        callbacks = _callback_urls(config)
        refresh_token_validity_days, access_token_validity_hours = (
            _token_validity(config)
        )
        token_validity_units = (
            aws.cognito.UserPoolClientTokenValidityUnitsArgs(
                access_token="hours",
                id_token="hours",
                refresh_token="days",
            )
        )
        self.interactive_client = aws.cognito.UserPoolClient(
            f"{name}-interactive-client",
            name=f"{name}-{stack}-interactive",
            user_pool_id=self.user_pool.id,
            generate_secret=False,
            allowed_oauth_flows_user_pool_client=True,
            allowed_oauth_flows=["code"],
            allowed_oauth_scopes=["openid", "email"]
            + [
                f"{_RESOURCE_SERVER_ID}/{route_name}"
                for route_name, _function, _description in routes
            ],
            callback_urls=callbacks,
            default_redirect_uri=callbacks[0],
            access_token_validity=access_token_validity_hours,
            id_token_validity=access_token_validity_hours,
            refresh_token_validity=refresh_token_validity_days,
            token_validity_units=token_validity_units,
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
        self.ats_automation_client = (
            aws.cognito.UserPoolClient(
                f"{name}-ats-automation-client",
                name=f"{name}-{stack}-ats-automation",
                user_pool_id=self.user_pool.id,
                generate_secret=True,
                allowed_oauth_flows_user_pool_client=True,
                allowed_oauth_flows=["client_credentials"],
                allowed_oauth_scopes=[f"{_RESOURCE_SERVER_ID}/ats"],
                enable_token_revocation=True,
                prevent_user_existence_errors="ENABLED",
                opts=ResourceOptions(
                    parent=self,
                    depends_on=[self.resource_server],
                ),
            )
            if ats_lambda is not None
            else None
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
        self.receipt_url = Output.format(
            "{}/receipt/mcp", self.api.api_endpoint
        )
        self.glyph_url = Output.format("{}/glyph/mcp", self.api.api_endpoint)
        self.ats_url = (
            Output.format("{}/ats/mcp", self.api.api_endpoint)
            if ats_lambda is not None
            else None
        )

        self.ats_automation_secret_arn = None
        if self.ats_automation_client is not None and self.ats_url is not None:
            ats_automation_secret = aws.secretsmanager.Secret(
                f"{name}-ats-automation-credentials",
                name=f"/{stack}/mcp/oauth/ats-automation-client",
                description=(
                    "Rotating OAuth client credentials for unattended ATS "
                    "MCP callers"
                ),
                opts=child_opts,
            )
            ats_automation_credentials: Output[str] = Output.json_dumps(
                {
                    "client_id": self.ats_automation_client.id,
                    "client_secret": (
                        self.ats_automation_client.client_secret
                    ),
                    "scopes": [f"{_RESOURCE_SERVER_ID}/ats"],
                    "server_url": self.ats_url,
                    "token_url": self.token_url,
                    "user_pool_id": self.user_pool.id,
                }
            )
            ats_automation_secret_version = aws.secretsmanager.SecretVersion(
                f"{name}-ats-automation-credentials-version",
                secret_id=ats_automation_secret.id,
                secret_string=Output.secret(ats_automation_credentials),
                opts=child_opts,
            )
            self.ats_automation_secret_arn = ats_automation_secret.arn

            rotation_role = aws.iam.Role(
                f"{name}-ats-secret-rotation-role",
                assume_role_policy=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {
                                    "Service": "lambda.amazonaws.com"
                                },
                                "Action": "sts:AssumeRole",
                            }
                        ],
                    }
                ),
                opts=child_opts,
            )
            aws.iam.RolePolicyAttachment(
                f"{name}-ats-secret-rotation-logs",
                role=rotation_role.name,
                policy_arn=(
                    "arn:aws:iam::aws:policy/service-role/"
                    "AWSLambdaBasicExecutionRole"
                ),
                opts=ResourceOptions(parent=rotation_role),
            )
            rotation_policy = aws.iam.RolePolicy(
                f"{name}-ats-secret-rotation-policy",
                role=rotation_role.id,
                policy=Output.all(
                    ats_automation_secret.arn,
                    self.user_pool.arn,
                ).apply(
                    lambda values: json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": [
                                        "secretsmanager:DescribeSecret",
                                        "secretsmanager:GetSecretValue",
                                        "secretsmanager:PutSecretValue",
                                        (
                                            "secretsmanager:"
                                            "UpdateSecretVersionStage"
                                        ),
                                    ],
                                    "Resource": values[0],
                                },
                                {
                                    "Effect": "Allow",
                                    "Action": [
                                        (
                                            "cognito-idp:"
                                            "AddUserPoolClientSecret"
                                        ),
                                        (
                                            "cognito-idp:"
                                            "DeleteUserPoolClientSecret"
                                        ),
                                        (
                                            "cognito-idp:"
                                            "ListUserPoolClientSecrets"
                                        ),
                                    ],
                                    "Resource": values[1],
                                },
                            ],
                        }
                    )
                ),
                opts=ResourceOptions(parent=rotation_role),
            )
            rotation_lambda = aws.lambda_.Function(
                f"{name}-ats-secret-rotation",
                runtime="python3.13",
                handler="rotation.lambda_handler",
                role=rotation_role.arn,
                timeout=180,
                memory_size=192,
                code=pulumi.AssetArchive(
                    {
                        "rotation.py": pulumi.FileAsset(
                            os.path.join(
                                _AUTOMATION_LAMBDA_DIR,
                                "rotation.py",
                            )
                        )
                    }
                ),
                opts=ResourceOptions(
                    parent=self,
                    depends_on=[rotation_policy],
                ),
            )
            rotation_permission = aws.lambda_.Permission(
                f"{name}-ats-secret-rotation-invoke",
                action="lambda:InvokeFunction",
                function=rotation_lambda.name,
                principal="secretsmanager.amazonaws.com",
                source_account=account_id,
                source_arn=ats_automation_secret.arn,
                opts=child_opts,
            )
            aws.secretsmanager.SecretRotation(
                f"{name}-ats-automation-rotation",
                secret_id=ats_automation_secret.id,
                rotation_lambda_arn=rotation_lambda.arn,
                rotate_immediately=False,
                rotation_rules={"automatically_after_days": 7},
                opts=ResourceOptions(
                    parent=self,
                    depends_on=[
                        ats_automation_secret_version,
                        rotation_permission,
                    ],
                ),
            )

            cleanup_rule = aws.cloudwatch.EventRule(
                f"{name}-ats-secret-cleanup-schedule",
                description=(
                    "Remove superseded ATS Cognito secrets after overlap"
                ),
                schedule_expression="rate(1 hour)",
                opts=child_opts,
            )
            cleanup_permission = aws.lambda_.Permission(
                f"{name}-ats-secret-cleanup-invoke",
                action="lambda:InvokeFunction",
                function=rotation_lambda.name,
                principal="events.amazonaws.com",
                source_arn=cleanup_rule.arn,
                opts=child_opts,
            )
            aws.cloudwatch.EventTarget(
                f"{name}-ats-secret-cleanup-target",
                rule=cleanup_rule.name,
                arn=rotation_lambda.arn,
                input=Output.json_dumps(
                    {
                        "operation": "cleanup",
                        "secret_id": ats_automation_secret.id,
                    }
                ),
                opts=ResourceOptions(
                    parent=cleanup_rule,
                    depends_on=[cleanup_permission],
                ),
            )

            canary_role = aws.iam.Role(
                f"{name}-ats-auth-canary-role",
                assume_role_policy=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {
                                    "Service": "lambda.amazonaws.com"
                                },
                                "Action": "sts:AssumeRole",
                            }
                        ],
                    }
                ),
                opts=child_opts,
            )
            aws.iam.RolePolicyAttachment(
                f"{name}-ats-auth-canary-logs",
                role=canary_role.name,
                policy_arn=(
                    "arn:aws:iam::aws:policy/service-role/"
                    "AWSLambdaBasicExecutionRole"
                ),
                opts=ResourceOptions(parent=canary_role),
            )
            canary_policy = aws.iam.RolePolicy(
                f"{name}-ats-auth-canary-secret",
                role=canary_role.id,
                policy=ats_automation_secret.arn.apply(
                    lambda secret_arn: json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": (
                                        "secretsmanager:GetSecretValue"
                                    ),
                                    "Resource": secret_arn,
                                }
                            ],
                        }
                    )
                ),
                opts=ResourceOptions(parent=canary_role),
            )
            canary_lambda = aws.lambda_.Function(
                f"{name}-ats-auth-canary",
                runtime="python3.13",
                handler="canary.lambda_handler",
                role=canary_role.arn,
                timeout=45,
                memory_size=128,
                reserved_concurrent_executions=1,
                code=pulumi.AssetArchive(
                    {
                        "canary.py": pulumi.FileAsset(
                            os.path.join(
                                _AUTOMATION_LAMBDA_DIR,
                                "canary.py",
                            )
                        )
                    }
                ),
                environment={
                    "variables": {
                        "MCP_CREDENTIALS_SECRET_ARN": (
                            ats_automation_secret.arn
                        )
                    }
                },
                opts=ResourceOptions(
                    parent=self,
                    depends_on=[canary_policy],
                ),
            )
            canary_rule = aws.cloudwatch.EventRule(
                f"{name}-ats-auth-canary-schedule",
                description="Continuously prove ATS machine authentication",
                schedule_expression="rate(15 minutes)",
                opts=child_opts,
            )
            canary_permission = aws.lambda_.Permission(
                f"{name}-ats-auth-canary-invoke",
                action="lambda:InvokeFunction",
                function=canary_lambda.name,
                principal="events.amazonaws.com",
                source_arn=canary_rule.arn,
                opts=child_opts,
            )
            aws.cloudwatch.EventTarget(
                f"{name}-ats-auth-canary-target",
                rule=canary_rule.name,
                arn=canary_lambda.arn,
                opts=ResourceOptions(
                    parent=canary_rule,
                    depends_on=[canary_permission],
                ),
            )
            aws.cloudwatch.MetricAlarm(
                f"{name}-ats-auth-canary-errors",
                comparison_operator="GreaterThanThreshold",
                evaluation_periods=1,
                metric_name="Errors",
                namespace="AWS/Lambda",
                period=900,
                statistic="Sum",
                threshold=0,
                alarm_description=(
                    "Unattended ATS authentication or MCP initialization failed"
                ),
                dimensions={"FunctionName": canary_lambda.name},
                treat_missing_data="notBreaching",
                opts=child_opts,
            )
            aws.cloudwatch.MetricAlarm(
                f"{name}-ats-auth-canary-heartbeat",
                comparison_operator="LessThanThreshold",
                evaluation_periods=1,
                metric_name="Invocations",
                namespace="AWS/Lambda",
                period=1800,
                statistic="Sum",
                threshold=1,
                alarm_description="ATS authentication canary stopped running",
                dimensions={"FunctionName": canary_lambda.name},
                treat_missing_data="breaching",
                opts=child_opts,
            )
            aws.cloudwatch.MetricAlarm(
                f"{name}-ats-secret-rotation-errors",
                comparison_operator="GreaterThanThreshold",
                evaluation_periods=1,
                metric_name="Errors",
                namespace="AWS/Lambda",
                period=300,
                statistic="Sum",
                threshold=0,
                alarm_description="ATS OAuth client-secret rotation failed",
                dimensions={"FunctionName": rotation_lambda.name},
                treat_missing_data="notBreaching",
                opts=child_opts,
            )

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
        self.ats_authorizer = (
            aws.apigatewayv2.Authorizer(
                f"{name}-ats-authorizer",
                api_id=self.api.id,
                authorizer_type="JWT",
                name=f"{name}-{stack}-ats-cognito-jwt",
                identity_sources=["$request.header.Authorization"],
                jwt_configuration={
                    "issuer": self.issuer_url,
                    "audiences": [
                        self.interactive_client.id,
                        self.ats_automation_client.id,
                        self.ats_url,
                    ],
                },
                opts=child_opts,
            )
            if self.ats_automation_client is not None
            and self.ats_url is not None
            else None
        )

        protected_routes = []
        for route_name, lambda_function, _description in routes:
            integration = aws.apigatewayv2.Integration(
                f"mcp-auth-{route_name}-integration",
                api_id=self.api.id,
                integration_type="AWS_PROXY",
                integration_uri=lambda_function.arn,
                payload_format_version="2.0",
                opts=child_opts,
            )
            protected_route = aws.apigatewayv2.Route(
                f"mcp-auth-{route_name}-route",
                api_id=self.api.id,
                route_key=(
                    f"POST /{route_name}/mcp"
                    if route_name == "ats"
                    else f"ANY /{route_name}/mcp"
                ),
                target=integration.id.apply(lambda iid: f"integrations/{iid}"),
                authorization_type="JWT",
                authorizer_id=(
                    self.ats_authorizer.id
                    if route_name == "ats" and self.ats_authorizer is not None
                    else self.authorizer.id
                ),
                authorization_scopes=[f"{_RESOURCE_SERVER_ID}/{route_name}"],
                opts=child_opts,
            )
            protected_routes.append(protected_route)
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
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=metadata_role),
        )
        route_urls = {
            "receipt": self.receipt_url,
            "glyph": self.glyph_url,
        }
        if self.ats_url is not None:
            route_urls["ats"] = self.ats_url
        metadata_docs = Output.json_dumps(
            {
                f"/.well-known/oauth-protected-resource/{route_name}/mcp": {
                    "resource": route_urls[route_name],
                    "authorization_servers": [self.issuer_url],
                    "scopes_supported": [
                        f"{_RESOURCE_SERVER_ID}/{route_name}"
                    ],
                    "bearer_methods_supported": ["header"],
                }
                for route_name, _function, _description in routes
            }
        )
        metadata_lambda = aws.lambda_.Function(
            f"{name}-metadata",
            role=metadata_role.arn,
            runtime="python3.13",
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
        for route_name, _function, _description in routes:
            aws.apigatewayv2.Route(
                f"{name}-metadata-route-{route_name}",
                api_id=self.api.id,
                route_key=(
                    "GET /.well-known/oauth-protected-resource"
                    f"/{route_name}/mcp"
                ),
                target=metadata_integration.id.apply(
                    lambda iid: f"integrations/{iid}"
                ),
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

        access_log_group = aws.cloudwatch.LogGroup(
            f"{name}-api-access-logs",
            name=f"/aws/apigateway/{name}-{stack}",
            retention_in_days=30 if stack != "prod" else 90,
            opts=child_opts,
        )
        route_settings = []
        if ats_lambda is not None:
            route_settings.append(
                aws.apigatewayv2.StageRouteSettingArgs(
                    route_key="POST /ats/mcp",
                    detailed_metrics_enabled=True,
                    throttling_burst_limit=10,
                    throttling_rate_limit=5.0,
                )
            )
        self.stage = aws.apigatewayv2.Stage(
            f"{name}-stage",
            api_id=self.api.id,
            name="$default",
            auto_deploy=True,
            access_log_settings={
                "destination_arn": access_log_group.arn,
                "format": json.dumps(
                    {
                        "requestId": "$context.requestId",
                        "routeKey": "$context.routeKey",
                        "status": "$context.status",
                        "sourceIp": "$context.identity.sourceIp",
                        "clientId": (
                            "$context.authorizer.jwt.claims.client_id"
                        ),
                        "responseLength": "$context.responseLength",
                        "responseLatency": "$context.responseLatency",
                        "integrationError": (
                            "$context.integrationErrorMessage"
                        ),
                    },
                    separators=(",", ":"),
                ),
            },
            route_settings=route_settings,
            opts=ResourceOptions(
                parent=self,
                depends_on=[access_log_group, *protected_routes],
            ),
        )

        if ats_lambda is not None:
            for metric_name, threshold, description in [
                (
                    "4xx",
                    20,
                    "Sustained rejected requests against the MCP gateway",
                ),
                (
                    "5xx",
                    0,
                    "MCP gateway integration or server failures",
                ),
            ]:
                aws.cloudwatch.MetricAlarm(
                    f"{name}-api-{metric_name}-alarm",
                    comparison_operator="GreaterThanThreshold",
                    evaluation_periods=1,
                    metric_name=metric_name,
                    namespace="AWS/ApiGateway",
                    period=300,
                    statistic="Sum",
                    threshold=threshold,
                    alarm_description=description,
                    dimensions={
                        "ApiId": self.api.id,
                        "Stage": "$default",
                    },
                    treat_missing_data="notBreaching",
                    opts=child_opts,
                )

        self.register_outputs(
            {
                "receipt_url": self.receipt_url,
                "glyph_url": self.glyph_url,
                "ats_url": self.ats_url,
                "issuer_url": self.issuer_url,
                "interactive_client_id": self.interactive_client.id,
                "automation_secret_arn": self.automation_secret_arn,
                "ats_automation_secret_arn": (self.ats_automation_secret_arn),
            }
        )

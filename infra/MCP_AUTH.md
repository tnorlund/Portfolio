# Remote MCP authentication

Receipt, Glyph Studio, and the optional ATS verification reader share one
authentication service while retaining route-specific scopes:

1. API Gateway exposes `/receipt/mcp`, `/glyph/mcp`, and, when enabled,
   `/ats/mcp`.
2. A Cognito user pool issues OAuth access tokens.
3. API Gateway requires the `portfolio-mcp/receipt` or
   `portfolio-mcp/glyph` custom scope before invoking the corresponding
   Lambda. The ATS route separately requires `portfolio-mcp/ats`.
4. Each MCP route publishes RFC 9728 protected-resource metadata under
   `/.well-known/oauth-protected-resource/<server>/mcp`.

The receipt and glyph Lambda Function URLs are retained for internal and
recovery access, but they use `AWS_IAM`. The ATS Lambda has no Function URL;
its only ingress is the scoped API Gateway route. There is no anonymous origin
that bypasses the Cognito authorizer.

The ATS mail trust gates, iCloud forwarding setup, retention, and acceptance
test are documented in [ATS_VERIFICATION_INBOX.md](ATS_VERIFICATION_INBOX.md).

## Client types

The `mcp_oauth_interactive_client_id` stack output identifies a public OAuth
client that uses authorization code flow. Configure the MCP client with that
client ID, the appropriate `mcp_server_url`, `glyph_mcp_server_url`, or
`ats_mcp_server_url`, and a callback URL allowed by
`portfolio:mcpOAuthCallbackUrls`. The defaults cover Claude connectors,
Cursor/Grok Bot's hosted and desktop callbacks, and local development clients.
Cognito does not provide dynamic client registration, so the exported client
ID is required.

Codex appends a stable, server-specific callback ID to its configured base
callback URL. Register that complete derived URL in the development stack
(for example, `http://127.0.0.1:8765/callback/<id>`). Keep personal Codex
MCP access pointed at the development gateway rather than the production
gateway.

For a claude.ai custom connector: add the connector with the gateway URL
(`mcp_server_url`, `glyph_mcp_server_url`, or `ats_mcp_server_url`), open
Advanced settings, and paste `mcp_oauth_interactive_client_id` as the OAuth
client ID (no secret — it is a public PKCE client). Discovery uses the
standard RFC 9728
path-derived well-known location: the gateway is an HTTP API on the
`$default` stage, so resource URLs have no stage path prefix and
`/.well-known/oauth-protected-resource/<server>/mcp` resolves exactly as
clients derive it. (A REST API's `/{stage}/` prefix breaks that
derivation, and REST gateway responses can't emit a per-route
`WWW-Authenticate` hint — that is why this is an HTTP API.)

For local Cursor validation, configure a user-scoped remote MCP with the
gateway URL, `mcp_oauth_interactive_client_id`, and only the route's required
scope. That `~/.cursor/mcp.json` entry does not install a connector in Grok
Bot's hosted plugin catalog. Grok Bot must receive the remote MCP through an
account-installed Cursor plugin or a Team MCP linked to a team marketplace;
do not configure Grok CLI. The ATS runbook documents the checked-in
development plugin and installation flow. Cursor's fixed redirects are:

- `https://www.cursor.com/agents/mcp/oauth/callback` for hosted agents;
- `http://localhost:8787/callback` for the desktop app.

Both must remain registered on the Cognito public client. The complete
ATS-specific configuration and verification steps are in
[ATS_VERIFICATION_INBOX.md](ATS_VERIFICATION_INBOX.md).

User signup is administrator-only. Create the first user after deployment:

```bash
aws cognito-idp admin-create-user \
  --user-pool-id "$(pulumi stack output mcp_oauth_user_pool_id)" \
  --username you@example.com \
  --user-attributes Name=email,Value=you@example.com \
  --desired-delivery-mediums EMAIL
```

Scheduled receipt callers use client credentials. Pulumi stores a client ID,
generated client secret, token URL, and the receipt-only scope in Secrets
Manager and exports only `mcp_oauth_automation_secret_arn`. Grant each
scheduled receipt workload read access to that one secret, fetch a short-lived
token, and send it as `Authorization: Bearer <token>`. Never copy the client
secret into a config file or repository variable. Glyph or ATS automation
should get its own single-scope client if it is ever needed. Do not add ATS
access to the receipt automation client.

## Configuration and rollout

Override interactive callback URLs when needed:

```bash
pulumi config set --path \
  'portfolio:mcpOAuthCallbackUrls[0]' \
  'http://localhost:8765/callback'
```

Token lifetimes are stack-configurable. The defaults remain one hour for
access and ID tokens and 30 days for refresh tokens. The development stack
uses the Cognito maximum of 24 hours for access and ID tokens plus a 365-day
refresh token to reduce local-tool reconnect friction:

```bash
pulumi config set portfolio:mcpOAuthAccessTokenValidityHours 24
pulumi config set portfolio:mcpOAuthRefreshTokenValidityDays 365
```

Review infrastructure without applying it:

```bash
cd infra
pulumi preview
```

Deployment is manual and prod-gated. Before applying, update remote clients
to use the exported API Gateway URLs and configure the interactive client ID,
or migrate scheduled callers to the automation secret. The API Gateway route
uses the standard buffered MCP adapter and has a 29-second integration
window. Signed internal callers that need longer operations should continue
to use the IAM Function URL.

## IAM principal inventory (do not conflate)

Three unrelated kinds of principals touch this system:

- **MCP Lambda execution roles** (`receipt-mcp-lambda-role-*`,
  `glyph-mcp-lambda-role-*`, and `ats-verification-inbox-mcp-role-*` when
  enabled): what a Lambda may do once invoked. The ATS role can only query its
  short-lived code table; it cannot read S3 or write DynamoDB. Authorization,
  not authentication; nothing in this document changes the receipt or glyph
  roles.
- **Cognito user pool** (this gateway): authenticates *remote MCP clients*
  (claude.ai connectors, scheduled callers). Issues OAuth tokens; has no
  AWS API permissions at all.
- **`claude-cloud-dev` IAM user**: credentials for Claude Code cloud
  sessions to reach dev AWS directly. Entirely separate from both of the
  above — do not grant it MCP-related policies to "fix" a connector, and
  do not point connector auth at it.

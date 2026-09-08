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
Cognito does not provide dynamic client registration. The gateway therefore
publishes a constrained RFC 7591 compatibility endpoint for hosted MCP clients
that cannot retain a static client ID. It never creates a Cognito client or
returns a secret: it returns the existing public interactive client only when
every requested redirect URI and scope is already allowlisted. API Gateway
throttles that route independently. Clients that support static OAuth should
continue to use the exported client ID directly.

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
Bot's hosted plugin catalog. Add the same URL through the account-level MCP
picker at `cursor.com/agents` for Grok Bot, or distribute the checked-in plugin
through a team marketplace; do not configure Grok CLI. The hosted custom-MCP
path uses the gateway's constrained registration endpoint and still resolves
to the same public Cognito client. The ATS runbook documents both installation
flows. Cursor's fixed redirects are:

- `https://www.cursor.com/agents/mcp/oauth/callback` for hosted agents;
- `https://www.cursor.com/bot/mcp/oauth/callback` for Grok Bot;
- `http://localhost:8787/callback` for the desktop app;
- `cursor://anysphere.cursor-mcp/oauth/callback`, which Cursor's current
  account-level DCR request includes alongside the two documented redirects.

All four must remain registered on the Cognito public client while the hosted
installer sends the combined DCR request. The complete
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
Manager and exports only `mcp_oauth_automation_secret_arn`.

When the ATS component is enabled, unattended ATS callers get a separate
confidential client with only `portfolio-mcp/ats`. Its secret ARN is exported
as `mcp_oauth_ats_automation_secret_arn`; the credential itself is never a
stack output. AWS Secrets Manager rotates it every seven days using Cognito's
two-active-secret support. This follows AWS's
[Lambda rotation contract](https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotate-secrets_lambda-functions.html)
and Cognito's
[app-client secret APIs](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_ListUserPoolClientSecrets.html).
Rotation proves the pending credential can mint a token and initialize the ATS
MCP before promotion, including Cognito's brief new-secret propagation delay.
The previous Cognito secret remains active for at least one hour, then a
scheduled cleanup removes it. A
separate canary repeats the token-and-initialize check every 15 minutes and
CloudWatch alarms cover canary failure, a missing canary heartbeat, rotation
failure, and API 4xx/5xx errors.

Grant an unattended AWS workload only
`secretsmanager:GetSecretValue` on the single ATS secret ARN. At runtime it
must read the `AWSCURRENT` version, request a short-lived token from `token_url`
with the stored client ID, client secret, and exact scopes, then send
`Authorization: Bearer <token>` to `server_url`. Re-read `AWSCURRENT` when a
token request fails so rotation is self-healing. Do not put the secret in a
repository variable, local config, prompt, log, or a long-lived environment
variable.

The machine credential solves unattended AWS/CI/daemon access. It does not
make a hosted Cursor/Grok Bot process an AWS principal. Grok Bot continues to
use the account-installed plugin's one-time interactive authorization and
Cursor-hosted token refresh. Cursor is not currently listed as supporting the
[MCP OAuth Client Credentials extension](https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials)
in the official
[client support matrix](https://modelcontextprotocol.io/extensions/client-matrix),
so copying the rotating ATS client secret into the Cursor plugin would create
a second, stale secret store and is not supported. Do not add ATS access to the
receipt automation client.

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

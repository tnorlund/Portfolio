# Remote MCP authentication

Receipt and Glyph Studio share one authentication boundary:

1. API Gateway exposes `/receipt/mcp` and `/glyph/mcp`.
2. A Cognito user pool issues OAuth access tokens.
3. API Gateway requires the `portfolio-mcp/receipt` or
   `portfolio-mcp/glyph` custom scope before invoking the corresponding
   Lambda.
4. Each MCP route publishes RFC 9728 protected-resource metadata under
   `/.well-known/oauth-protected-resource/<server>/mcp`.

The Lambda Function URLs are retained for internal and recovery access, but
they use `AWS_IAM`. A caller must sign those requests with SigV4. There is no
anonymous origin that bypasses the Cognito authorizer.

## Client types

The `mcp_oauth_interactive_client_id` stack output identifies a public OAuth
client that uses authorization code flow. Configure the MCP client with that
client ID, the appropriate `mcp_server_url` or `glyph_mcp_server_url`, and a
callback URL allowed by `portfolio:mcpOAuthCallbackUrls`. The defaults cover
the claude.ai / Claude desktop connector callbacks
(`https://claude.ai/api/mcp/auth_callback`, `https://claude.com/...`) and
local callbacks on ports 8765 and 6274. Cognito does not provide dynamic
client registration, so the exported client ID is required.

Codex appends a stable, server-specific callback ID to its configured base
callback URL. Register that complete derived URL in the development stack
(for example, `http://127.0.0.1:8765/callback/<id>`). Keep personal Codex
MCP access pointed at the development gateway rather than the production
gateway.

For a claude.ai custom connector: add the connector with the gateway URL
(`mcp_server_url` or `glyph_mcp_server_url`), open Advanced settings, and
paste `mcp_oauth_interactive_client_id` as the OAuth client ID (no secret —
it is a public PKCE client). Discovery uses the standard RFC 9728
path-derived well-known location: the gateway is an HTTP API on the
`$default` stage, so resource URLs have no stage path prefix and
`/.well-known/oauth-protected-resource/<server>/mcp` resolves exactly as
clients derive it. (A REST API's `/{stage}/` prefix breaks that
derivation, and REST gateway responses can't emit a per-route
`WWW-Authenticate` hint — that is why this is an HTTP API.)

The development stack publishes the stable receipt connector URL
`https://mcp-dev.tylernorlund.com/receipt/mcp`. The hostname is publicly
routable because Claude Routines execute in Anthropic's cloud, but the MCP
route is not anonymously accessible: API Gateway requires a valid Cognito
access token with the receipt scope. OAuth discovery documents remain public
by design. `portfolio:mcpPublicHostname` is rejected outside the development
stack so the personal connector cannot accidentally acquire a production
hostname.

Claude Routines must attach the already-connected `receipt-tools-dev`
connector from the Routine's **Connectors** tab. Authenticate that connector
once in Claude Settings, then let Claude retain and refresh the delegated
OAuth grant. Routine prompts must call native MCP tools; they must not fetch
the automation client secret, construct bearer tokens, or call the MCP URL
with `curl`. The checked-in Routine config records the required connector by
name because account connector grants cannot be represented safely in Git.

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
secret into a config file or repository variable. Glyph automation should get
its own single-scope client if it is ever needed.

This client-credentials path is for non-Claude machine callers. Claude
Routines use their account-connected authorization-code grant instead.

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
pulumi config set portfolio:mcpPublicHostname mcp-dev.tylernorlund.com
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
  `glyph-mcp-lambda-role-*`, one per stack): what the Lambda may do once
  invoked — DynamoDB, scoped S3/SQS, and the `web-analytics-read-policy`
  for the Athena-backed `analytics_*` tools. Authorization, not
  authentication; nothing in this document changes them.
- **Cognito user pool** (this gateway): authenticates *remote MCP clients*
  (claude.ai connectors, scheduled callers). Issues OAuth tokens; has no
  AWS API permissions at all.
- **`claude-cloud-dev` IAM user**: credentials for Claude Code cloud
  sessions to reach dev AWS directly. Entirely separate from both of the
  above — do not grant it MCP-related policies to "fix" a connector, and
  do not point connector auth at it.

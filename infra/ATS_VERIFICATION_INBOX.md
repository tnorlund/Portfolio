# ATS verification inbox

Issue [#1502](https://github.com/tnorlund/Portfolio/issues/1502) is served by
an isolated Greenhouse verification-code path:

```text
Greenhouse -> tnorlund@icloud.com -> exact-sender iCloud rule
           -> ats@in.tylernorlund.com -> existing SES receipt rule set
           -> isolated S3 bucket (1 day) -> trust-gate Lambda
           -> isolated DynamoDB table (1 hour) -> read-only ATS MCP Lambda
           -> /ats/mcp (Cognito scope portfolio-mcp/ats) -> Grok Bot
```

The application email remains `tnorlund@icloud.com`. Grok Bot never receives
iCloud credentials or access to the personal mailbox.

## Why this shape fits the existing infrastructure

The development account already receives `receipts@in.tylernorlund.com`
through SES and already has a Cognito-protected MCP gateway. SES permits only
one active receipt rule set in an account and region, so this component adds
an exact-recipient rule to `email-receipt-inbox-dev`; it does not create or
activate a competing rule set. See the AWS documentation for
[receipt-rule processing and the one-active-set constraint](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-concepts.html).

A new applicant address was rejected for the first release. Greenhouse says a
MyGreenhouse account is tied to one email address, accounts cannot be merged,
and a submitted application's email cannot be changed. Greenhouse's fraud
detection also uses an email address older than one year as one authenticity
signal. Keeping the established address avoids identity fragmentation and an
unnecessary change to that signal:

- [MyGreenhouse FAQ for candidates](https://support.greenhouse.io/hc/en-us/articles/43418495049499-MyGreenhouse-FAQ-for-Candidates)
- [Greenhouse fraud-detection operational guide](https://support.greenhouse.io/hc/en-us/articles/44681941657243-Operational-readiness-guide-Fraud-Detection-policy)

AgentMail and a dedicated Gmail inbox remain reasonable fallback providers,
but both add a new mailbox/security boundary without removing the need to
preserve the iCloud application identity. The existing SES plane is already
deployed, monitored, and near-real-time.

## Security and privacy boundaries

- The SES rule matches only the envelope recipient
  `ats@in.tylernorlund.com`, requires TLS, and enables spam and malware scans.
- The extractor accepts only the documented Greenhouse no-reply addresses,
  an exact visible `From` mailbox, one SES-added authentication result with an
  aligned DMARC `PASS`, and explicit `PASS` spam and virus verdicts. SES adds
  these results but does not enforce them automatically, so the Lambda must
  fail closed. See [SES receiving authentication and scan behavior](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-concepts.html).
- `X-Original-From`, ARC claims, display names, and sender-supplied
  `Authentication-Results` headers are not trust inputs.
- A subject must identify a security/verification code, and the body must
  contain Greenhouse's eight-character code in a heading or code context.
- The raw MIME object is private, versioned, and has a one-day lifecycle. A
  DynamoDB TTL marks the item for deletion after one hour, and point-in-time
  recovery is deliberately disabled. Because DynamoDB TTL deletion is
  asynchronous, the MCP independently enforces expiry and a caller-selected
  freshness limit of 30-900 seconds (default 10 minutes).
- Only the code, provider, authenticated sender, receive time, expiry, and a
  content digest enter DynamoDB. Email subjects and bodies never do. This
  keeps email prompt injection outside the model-visible boundary.
- The ingest role can read only `raw/` in the ATS bucket and write only the ATS
  table. The MCP role can only `Query` that table. It cannot read raw email,
  write records, enumerate the receipt store, or send mail.
- `/ats/mcp` has its own `portfolio-mcp/ats` OAuth scope. Existing receipt
  automation credentials remain receipt-only.

The accepted Greenhouse sender list follows Greenhouse's published
[no-reply address inventory](https://support.greenhouse.io/hc/en-us/articles/17675865619099-Greenhouse-Recruiting-no-reply-email-addresses).

## Deployment and one-time iCloud setup

`portfolio:ats_verification_inbox_enabled` is enabled only in
`Pulumi.dev.yaml`, next to the already-active receipt inbox. Do not enable the
same SES receiving components in another stack in this AWS account/region:
that stack would compete for the single active receipt rule set.

Follow the dev-only Pulumi safeguards in the repository's `AGENTS.md`. Never
select or trigger the production stack. After the development update completes,
record these non-secret stack outputs:

- `ats_verification_inbox_address`
- `ats_mcp_server_url`
- `mcp_oauth_interactive_client_id`

Then create four server-side rules in iCloud Mail. Apple documents that iCloud
Mail rules can automatically forward matching mail and that a new or changed
rule can take up to 15 minutes to take effect: [Set up rules in iCloud
Mail](https://support.apple.com/guide/icloud/set-up-rules-mm6b1a3f8a/icloud).

For each address below, create an **If From is** rule whose action is
**Forward to** the `ats_verification_inbox_address` output:

- `no-reply@greenhouse.io`
- `no-reply@us.greenhouse-mail.io`
- `no-reply@eu.greenhouse-mail.io`
- `no-reply@anz.greenhouse.io`

Do not create a catch-all forwarding rule, do not forward all iCloud mail, and
do not add iCloud credentials to Grok Bot. The Lambda separately enforces the
security-code subject and body shape, so other Greenhouse mail that happens to
be forwarded is discarded without a DynamoDB record and its raw object expires
after one day.

### Connect Pipeline Scout in Grok Bot

Grok Bot uses the same hosted plugin and MCP authentication layer as the
Cursor account used to sign in; this is not a Grok CLI configuration. Cursor's
static OAuth flow is required because Cognito does not implement dynamic client
registration. The development Cognito client registers both fixed Cursor
redirects documented at [Cursor MCP](https://cursor.com/docs/mcp):

- `https://www.cursor.com/agents/mcp/oauth/callback`
- `http://localhost:8787/callback`

Add a user-scoped custom MCP from Cursor **Customize -> MCPs -> New -> User**,
or install the equivalent configuration through the team's plugin catalog:

```json
{
  "mcpServers": {
    "ats-verification": {
      "type": "http",
      "url": "<ats_mcp_server_url>",
      "auth": {
        "CLIENT_ID": "<mcp_oauth_interactive_client_id>",
        "scopes": ["openid", "email", "portfolio-mcp/ats"]
      }
    }
  }
}
```

Complete the browser authorization, restart Grok Bot, then confirm the
connector is installed and connected under **Plugins -> Your plugins**. Grok
Bot and Cursor share the MCP authorization, and Cursor's connector backend
holds the OAuth tokens rather than placing them on the Bot's computer. See the
official [Grok Bot plugin guide](https://cursor.com/help/grok-bot/connect-plugins)
and [Grok Bot security model](https://cursor.com/docs/grok-bot/work).

The connector is deliberately not general email access. It exposes exactly one
read-only tool, `get_latest_verification_code`, which returns only a trusted,
unexpired code and metadata. It accepts `provider=greenhouse` and an optional
`max_age_seconds` from 30 through 900. Raw messages, subjects, bodies, iCloud
credentials, and mail-send capability remain unavailable to every Bot.

## Acceptance test

Complete this test before relying on the path during an application:

1. Wait 15 minutes after the iCloud rules are saved.
2. Open a real Greenhouse application and intentionally reach the security-code
   screen. Keep the application staged; this feature does not authorize an
   agent to submit it.
3. Confirm the message still arrives in iCloud and a raw object appears in the
   ATS bucket, not the receipt bucket.
4. Confirm the ingest Lambda reports a `stored` outcome, its DLQ remains empty,
   and the ATS table contains a record with a one-hour expiry. Do not paste the
   code into logs, GitHub, or screenshots.
5. Call `get_latest_verification_code` from Grok Bot with
   `provider=greenhouse`. It must return the same case-sensitive code and a
   plausible age below 600 seconds.
6. Call with `max_age_seconds=30` after the code is older than 30 seconds. It
   must return `found=false`.
7. Send an ordinary message with a “security code” subject to the ATS address.
   It must create a raw object but no table item because the sender is not an
   authenticated Greenhouse address.

If step 4 produces `ignored_untrusted`, inspect the restricted raw object's
SES-added authentication and scan headers. iCloud forwarding must preserve a
Greenhouse signature well enough for SES to report aligned DMARC `PASS`; do not
weaken the gate to make a failed sample pass. Keep manual code entry as the
fallback and capture only sanitized header evidence for a follow-up.

## Adding another ATS

Greenhouse is the only enabled provider in this release. Ashby can send from
its default address or an employer's custom domain, and other ATS platforms
have similar variation. Add a provider only after capturing a real forwarded
sample and confirming:

1. the official sender inventory;
2. the SES DMARC result after iCloud forwarding;
3. a stable, provider-specific subject and code shape;
4. a focused test fixture with no real code or personal content; and
5. a narrow iCloud forwarding rule.

Do not add a whole-domain wildcard merely to make a new provider work.

## Rollback

Remove the four iCloud rules first to stop new delivery. Then disable
`portfolio:ats_verification_inbox_enabled` in a follow-up change and use the
approved stack deployment path. Existing raw objects enter expiry after one
day and code records are logically unusable after one hour; the MCP route
disappears when the component is disabled. The development bucket is marked
for destruction with the component so versioned raw objects are not orphaned.

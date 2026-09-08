# Portfolio ATS Verification (Dev)

This Cursor plugin exposes the development ATS verification MCP to Cursor and
Grok Bot through Cursor's account plugin layer. It is intentionally limited to
the `portfolio-mcp/ats` OAuth scope and a single read-only tool,
`get_latest_verification_code`.

The checked-in defaults are public identifiers for the Portfolio development
stack, not credentials. Never replace them with production values or add an
OAuth client secret. See
[`infra/ATS_VERIFICATION_INBOX.md`](../../infra/ATS_VERIFICATION_INBOX.md) for
the trust boundary, retention policy, installation flow, and acceptance test.

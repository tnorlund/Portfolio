# Claude Routine configuration

These files are reviewable source-of-truth instructions for account-owned
Claude Routines. OAuth connector grants live in the Claude account and must
not be serialized into this repository.

## Receipt label fixer

1. Deploy the development stack so
   `https://mcp-dev.tylernorlund.com/receipt/mcp` resolves to the OAuth gateway.
2. In Claude **Settings → Connectors**, add `receipt-tools-dev` with that URL.
3. In Advanced settings, use the development stack output
   `mcp_oauth_interactive_client_id`. Do not configure a client secret.
4. Click **Connect** and finish the Cognito login once.
5. Open the `receipt-label-fixer` Routine and attach the connected
   `receipt-tools-dev` connector in its **Connectors** tab.
6. Copy the prompt from `receipt_label_fixer.json` into the Routine and use
   **Run now** to verify one run before enabling its daily schedule.

The Routine uses native `list_receipt_health_issues`, `update_word_label`, and
`update_receipt_health_issue` MCP tools. It does not need an MCP URL, OAuth
client secret, AWS credentials, or Secrets Manager access in its environment.

Run `python scripts/sync_scheduled_agents.py` to validate the checked-in
configuration. Pass `--apply` to print the account-level update steps; the
script intentionally does not attempt to recreate OAuth connector grants.

After deploying, run the read-only boundary check from the repository root:

```bash
python scripts/smoke_test_mcp_auth.py --stack dev
```

It verifies that anonymous requests receive `401`, obtains a short-lived
machine token without printing credentials, and confirms the native Routine
tools are present.

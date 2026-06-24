#!/usr/bin/env bash
# Smoke test: prove a HEADLESS `claude -p` on the Mac Mini can reach the
# receipt-tools MCP and make a write. This gates the parallel cleanup run — if
# the MCP isn't reachable non-interactively, the per-merchant cleanup step
# silently can't apply label flips.
#
# It does ONE read and ONE no-op write (re-setting an already-INVALID Costco
# warehouse-number label to INVALID), so it changes nothing.
#
# Prereqs on the Mac Mini (one-time):
#   - claude logged in (`claude /login`) OR ANTHROPIC_API_KEY exported
#   - receipt-tools MCP registered:
#       claude mcp add receipt-tools -s user -e PORTFOLIO_ENV=dev -- \
#         ~/.coreml-venv/bin/python ~/Portfolio/scripts/receipt_mcp_server.py
#   - AWS creds present (~/.aws/credentials)
#
# Usage:  ./scripts/mac_mini_mcp_smoke_test.sh
set -euo pipefail

REMOTE="${REMOTE:-tnorlund@192.168.0.147}"
REMOTE_PROJECT="${REMOTE_PROJECT:-Portfolio}"   # path under \$HOME on the Mac Mini

# Prompt is sent as a FILE (not embedded in the ssh string) to avoid quoting
# breakage from parens/pipes/newlines and the remote login shell (zsh).
PROMPT_FILE="$(mktemp)"
cat > "$PROMPT_FILE" <<'EOF'
You have the receipt-tools MCP available. Do two steps and report each.
Step 1: call get_receipts_by_merchant with merchant_name "Costco Wholesale". It passes if it returns a count.
Step 2: call update_word_label with image_id ed28a4ce-2258-4745-87ba-2fc662c94abf, receipt_id 2, line_id 32, word_id 3, label WEBSITE, new_status INVALID, reasoning "headless MCP smoke test no-op". It passes if the result has success true.
Finally output exactly one line: SMOKE_TEST read=PASS_OR_FAIL write=PASS_OR_FAIL
If the receipt-tools MCP tools are not available at all, output exactly: SMOKE_TEST read=NO_MCP write=NO_MCP
EOF
scp -q "$PROMPT_FILE" "$REMOTE:/tmp/mcp_smoke_prompt.txt"
rm -f "$PROMPT_FILE"

echo ">> Running headless MCP smoke test on $REMOTE ..."
# Do NOT pass --bare (it disables MCP discovery). claude lives at ~/.local/bin
# (not on the non-interactive PATH), so prepend it. < /dev/null keeps -p from
# blocking on stdin.
ssh "$REMOTE" '
  export PATH="$HOME/.local/bin:$PATH" RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1
  cd "$HOME/'"$REMOTE_PROJECT"'"
  claude -p "$(cat /tmp/mcp_smoke_prompt.txt)" \
    --permission-mode bypassPermissions --output-format text < /dev/null
' 2>&1 | tee /tmp/mcp_smoke_test.out

echo
if grep -q "SMOKE_TEST read=PASS write=PASS" /tmp/mcp_smoke_test.out; then
  echo "✅ MCP reachable and writable in headless mode — safe to launch the parallel batch."
elif grep -qi "Not logged in\|/login\|Invalid API key" /tmp/mcp_smoke_test.out; then
  echo "❌ claude is not authenticated on the Mac Mini. Run 'claude /login' there once,"
  echo "   or export ANTHROPIC_API_KEY for the headless jobs, then re-run."
  exit 1
elif grep -q "NO_MCP" /tmp/mcp_smoke_test.out; then
  echo "❌ receipt-tools MCP NOT available to headless claude. Register it (see header)."
  exit 1
else
  echo "⚠️  Inconclusive — inspect /tmp/mcp_smoke_test.out (read PASS but write FAIL =>"
  echo "    AWS creds / DynamoDB permissions issue on the Mac Mini)."
  exit 1
fi

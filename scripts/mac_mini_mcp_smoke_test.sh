#!/usr/bin/env bash
# Smoke test: prove a HEADLESS `claude -p` on the Mac Mini can reach the
# receipt-tools MCP and make a write. This gates the parallel cleanup run — if
# the MCP isn't reachable non-interactively, the per-merchant cleanup step
# silently can't apply label flips.
#
# It does ONE read and ONE no-op write (re-setting an already-INVALID Costco
# warehouse-number label to INVALID), so it changes nothing.
#
# Usage:  ./scripts/mac_mini_mcp_smoke_test.sh
set -euo pipefail

REMOTE="${REMOTE:-tnorlund@192.168.0.147}"
PROJECT="${PROJECT:-\$HOME/Portfolio}"   # repo path ON the Mac Mini

read -r -d '' PROMPT <<'EOF' || true
You have the `receipt-tools` MCP available. Do exactly two things and report each:
1) Call get_receipts_by_merchant with merchant_name "Costco Wholesale". It passes if it returns a count.
2) Call update_word_label with image_id ed28a4ce-2258-4745-87ba-2fc662c94abf, receipt_id 2,
   line_id 32, word_id 3, label WEBSITE, new_status INVALID,
   reasoning "headless MCP smoke test (no-op re-set)". It passes if the result has success:true.
Then output a single final line EXACTLY in this form:
SMOKE_TEST read=<PASS|FAIL> write=<PASS|FAIL>
If the receipt-tools MCP tools are not available at all, output:
SMOKE_TEST read=NO_MCP write=NO_MCP
EOF

echo ">> Running headless MCP smoke test on $REMOTE ..."
# NOTE: do NOT pass --bare here; --bare disables MCP server discovery.
# The Mac Mini must have AWS creds + the receipt-tools MCP configured
# (~/.claude.json or the project .mcp.json) for this to pass.
ssh "$REMOTE" bash -lc "
  cd $PROJECT
  export RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1
  claude -p \"$PROMPT\" \
    --permission-mode bypassPermissions \
    --output-format text
" | tee /tmp/mcp_smoke_test.out

echo
if grep -q "SMOKE_TEST read=PASS write=PASS" /tmp/mcp_smoke_test.out; then
  echo "✅ MCP reachable and writable in headless mode — safe to launch the parallel batch."
elif grep -q "NO_MCP" /tmp/mcp_smoke_test.out; then
  echo "❌ receipt-tools MCP NOT available to headless claude. Configure it in"
  echo "   the Mac Mini's ~/.claude.json (or project .mcp.json) before launching."
  exit 1
else
  echo "⚠️  Inconclusive — inspect /tmp/mcp_smoke_test.out (read may PASS but write FAIL =>"
  echo "    AWS creds / DynamoDB permissions issue on the Mac Mini)."
  exit 1
fi

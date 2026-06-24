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
#   - Auth for HEADLESS claude — plain SSH can't read the macOS login Keychain,
#     so a bare `claude /login` does NOT work over SSH. Pick one:
#       * SSH-driven, SUBSCRIPTION (recommended): put a long-lived OAuth token in
#         ~/.claude_batch_env (sourced automatically). Generate once on any
#         authed machine:  echo "export CLAUDE_CODE_OAUTH_TOKEN=$(claude setup-token)" \
#         > ~/.claude_batch_env   (Pro/Max, NOT API billing; account-scoped, so
#         you can generate it on the MacBook and copy the file to the box).
#       * GUI session, SUBSCRIPTION: run this with LOCAL=1 in the Mac Mini's own
#         Terminal (unlocked Keychain) — no token needed.
#       * ANTHROPIC_API_KEY in ~/.claude_batch_env (per-token API billing).
#   - receipt-tools MCP registered:
#       claude mcp add receipt-tools -s user -e PORTFOLIO_ENV=dev -- \
#         ~/.coreml-venv/bin/python ~/Portfolio/scripts/receipt_mcp_server.py
#   - AWS creds present (~/.aws/credentials)
#
# Usage:  ./scripts/mac_mini_mcp_smoke_test.sh          # SSH-driven (token)
#         LOCAL=1 ./scripts/mac_mini_mcp_smoke_test.sh  # on the box (subscription)
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
LOCAL="${LOCAL:-0}"
# Do NOT pass --bare (it disables MCP discovery). claude lives at ~/.local/bin.
if [ "$LOCAL" = "1" ]; then
  # Run IN the Mac Mini's GUI session → unlocked Keychain → SUBSCRIPTION auth,
  # no API key. (Run this script on the Mac Mini itself, not from the MacBook.)
  echo ">> Running MCP smoke test LOCALLY (GUI session / subscription) ..."
  export PATH="$HOME/.local/bin:$PATH" RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1
  claude -p "$(cat "$PROMPT_FILE")" \
    --permission-mode bypassPermissions --output-format text < /dev/null \
    2>&1 | tee /tmp/mcp_smoke_test.out
  rm -f "$PROMPT_FILE"
else
  scp -q "$PROMPT_FILE" "$REMOTE:/tmp/mcp_smoke_prompt.txt"
  rm -f "$PROMPT_FILE"
  echo ">> Running headless MCP smoke test on $REMOTE ..."
  # Over SSH the login Keychain is locked, so source ~/.claude_batch_env for a
  # CLAUDE_CODE_OAUTH_TOKEN (subscription, from `claude setup-token`) — or an
  # ANTHROPIC_API_KEY. Kept off the process list.
  ssh "$REMOTE" '
    export PATH="$HOME/.local/bin:$PATH" RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1
    [ -f "$HOME/.claude_batch_env" ] && . "$HOME/.claude_batch_env"
    cd "$HOME/'"$REMOTE_PROJECT"'"
    claude -p "$(cat /tmp/mcp_smoke_prompt.txt)" \
      --permission-mode bypassPermissions --output-format text < /dev/null
  ' 2>&1 | tee /tmp/mcp_smoke_test.out
fi

echo
if grep -q "SMOKE_TEST read=PASS write=PASS" /tmp/mcp_smoke_test.out; then
  echo "✅ MCP reachable and writable in headless mode — safe to launch the parallel batch."
elif grep -qi "Not logged in\|/login\|Invalid API key\|authentication" /tmp/mcp_smoke_test.out; then
  echo "❌ headless claude is not authenticated. SSH can't read the macOS Keychain,"
  echo "   so put a SUBSCRIPTION token in ~/.claude_batch_env (run once on any authed box):"
  echo "     echo \"export CLAUDE_CODE_OAUTH_TOKEN=\$(claude setup-token)\" > ~/.claude_batch_env"
  echo "   — or run this with LOCAL=1 in the Mac Mini's GUI Terminal (uses the Keychain)."
  exit 1
elif grep -q "NO_MCP" /tmp/mcp_smoke_test.out; then
  echo "❌ receipt-tools MCP NOT available to headless claude. Register it (see header)."
  exit 1
else
  echo "⚠️  Inconclusive — inspect /tmp/mcp_smoke_test.out (read PASS but write FAIL =>"
  echo "    AWS creds / DynamoDB permissions issue on the Mac Mini)."
  exit 1
fi

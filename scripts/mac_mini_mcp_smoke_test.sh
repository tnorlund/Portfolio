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
#   - Auth for HEADLESS claude: plain SSH can't read the macOS login Keychain.
#     Use subscription/OAuth, not API-key billing:
#       echo "export CLAUDE_CODE_OAUTH_TOKEN=$(claude setup-token)" > ~/.claude_batch_env
#     Or run with LOCAL=1 in the Mac Mini's GUI Terminal to use the unlocked
#     Keychain. This script refuses ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN.
#   - AWS creds present (~/.aws/credentials)
#
# Usage:  ./scripts/mac_mini_mcp_smoke_test.sh          # SSH-driven (token)
#         LOCAL=1 ./scripts/mac_mini_mcp_smoke_test.sh  # on the box (subscription)
set -euo pipefail

REMOTE="${REMOTE:-tnorlund@192.168.0.147}"
REMOTE_PROJECT="${REMOTE_PROJECT:-Portfolio}"   # path under \$HOME on the Mac Mini
DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME:-ReceiptsTable-dc5be22}"
CLAUDE_MODEL="${CLAUDE_MODEL:-haiku}"
MCP_TIMEOUT_MS="${MCP_TIMEOUT_MS:-180000}"
PYTHON_BIN="${PYTHON_BIN:-$HOME/.coreml-venv/bin/python}"

write_mcp_config() {
  local config_file="$1"
  local project_dir="$2"
  local python_bin="$3"

  cat > "$config_file" <<EOF
{
  "mcpServers": {
    "receipt-tools-local": {
      "type": "stdio",
      "alwaysLoad": true,
      "timeout": $MCP_TIMEOUT_MS,
      "command": "$python_bin",
      "args": [
        "$project_dir/scripts/receipt_mcp_server.py"
      ],
      "env": {
        "PORTFOLIO_ENV": "dev",
        "DYNAMODB_TABLE_NAME": "$DYNAMODB_TABLE_NAME",
        "RECEIPT_AGENT_DISABLE_PAID_LLM": "1",
        "DISABLE_PAID_LLM": "1"
      }
    }
  }
}
EOF
}

reject_api_billing_env() {
  if [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    echo "❌ Refusing to run with Anthropic API auth env vars set."
    echo "   Use CLAUDE_CODE_OAUTH_TOKEN from 'claude setup-token' or LOCAL=1 Keychain auth."
    exit 1
  fi
}

# Prompt is sent as a FILE (not embedded in the ssh string) to avoid quoting
# breakage from parens/pipes/newlines and the remote login shell (zsh).
PROMPT_FILE="$(mktemp)"
cat > "$PROMPT_FILE" <<'EOF'
You have the receipt-tools MCP available. Do three steps and report each.
Step 1: call get_receipts_by_merchant with merchant_name "Costco Wholesale". It passes if it returns a count.
Step 2: call list_synthetic_receipt_visual_review_targets. It passes if target_count is greater than zero and at least one target has local_image_exists true.
Step 3: first call list_words_by_label with label WEBSITE, status_filter INVALID, sample_size 1000. Verify the exact target image_id ed28a4ce-2258-4745-87ba-2fc662c94abf, receipt_id 2, line_id 32, word_id 3 appears with validation_status INVALID. If it does not, do NOT call update_word_label and mark write FAIL. Only when that exact record is already INVALID, call update_word_label with image_id ed28a4ce-2258-4745-87ba-2fc662c94abf, receipt_id 2, line_id 32, word_id 3, label WEBSITE, new_status INVALID, reasoning "headless MCP smoke test guarded no-op". It passes if the result has success true.
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
  [ -f "$HOME/.claude_batch_env" ] && . "$HOME/.claude_batch_env"
  reject_api_billing_env
  MCP_CONFIG_FILE="$(mktemp)"
  write_mcp_config "$MCP_CONFIG_FILE" "$(pwd)" "$PYTHON_BIN"
  env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
    CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 \
    MCP_CONNECTION_NONBLOCKING=0 \
    MCP_CONNECT_TIMEOUT_MS="$MCP_TIMEOUT_MS" \
    MCP_TIMEOUT="$MCP_TIMEOUT_MS" \
    MCP_TOOL_TIMEOUT="$MCP_TIMEOUT_MS" \
    MAX_THINKING_TOKENS=0 \
    RECEIPT_AGENT_DISABLE_PAID_LLM=1 \
    DISABLE_PAID_LLM=1 \
    claude -p "$(cat "$PROMPT_FILE")" \
    --system-prompt "You are a noninteractive Claude Code MCP smoke-test harness. Use MCP tools when requested. Do not browse. Do not run shell commands. Return only the requested final line." \
    --model "$CLAUDE_MODEL" --effort low \
    --mcp-config "$MCP_CONFIG_FILE" --strict-mcp-config \
    --permission-mode bypassPermissions --tools "" --max-turns 8 \
    --disable-slash-commands --output-format text --no-session-persistence < /dev/null \
    2>&1 | tee /tmp/mcp_smoke_test.out
  rm -f "$MCP_CONFIG_FILE"
  rm -f "$PROMPT_FILE"
else
  scp -q "$PROMPT_FILE" "$REMOTE:/tmp/mcp_smoke_prompt.txt"
  rm -f "$PROMPT_FILE"
  echo ">> Running headless MCP smoke test on $REMOTE ..."
  # Over SSH the login Keychain is locked, so source ~/.claude_batch_env for a
  # CLAUDE_CODE_OAUTH_TOKEN (subscription, from `claude setup-token`). Kept off
  # the process list. API auth is refused.
  ssh "$REMOTE" '
    set -euo pipefail
    export PATH="$HOME/.local/bin:$PATH" RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1
    [ -f "$HOME/.claude_batch_env" ] && . "$HOME/.claude_batch_env"
    if [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
      echo "❌ Refusing to run with Anthropic API auth env vars set."
      echo "   Use CLAUDE_CODE_OAUTH_TOKEN from '\''claude setup-token'\''."
      exit 1
    fi
    cd "$HOME/'"$REMOTE_PROJECT"'"
    MCP_TIMEOUT_MS="'"$MCP_TIMEOUT_MS"'"
    MCP_CONFIG_FILE="$(mktemp)"
    trap "rm -f /tmp/mcp_smoke_prompt.txt \"$MCP_CONFIG_FILE\"" EXIT
    cat > "$MCP_CONFIG_FILE" <<EOF
{
  "mcpServers": {
    "receipt-tools-local": {
      "type": "stdio",
      "alwaysLoad": true,
      "timeout": '"$MCP_TIMEOUT_MS"',
      "command": "$HOME/.coreml-venv/bin/python",
      "args": [
        "$PWD/scripts/receipt_mcp_server.py"
      ],
      "env": {
        "PORTFOLIO_ENV": "dev",
        "DYNAMODB_TABLE_NAME": "'"$DYNAMODB_TABLE_NAME"'",
        "RECEIPT_AGENT_DISABLE_PAID_LLM": "1",
        "DISABLE_PAID_LLM": "1"
      }
    }
  }
}
EOF
    env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
      CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 \
      MCP_CONNECTION_NONBLOCKING=0 \
      MCP_CONNECT_TIMEOUT_MS="$MCP_TIMEOUT_MS" \
      MCP_TIMEOUT="$MCP_TIMEOUT_MS" \
      MCP_TOOL_TIMEOUT="$MCP_TIMEOUT_MS" \
      MAX_THINKING_TOKENS=0 \
      RECEIPT_AGENT_DISABLE_PAID_LLM=1 \
      DISABLE_PAID_LLM=1 \
      claude -p "$(cat /tmp/mcp_smoke_prompt.txt)" \
      --system-prompt "You are a noninteractive Claude Code MCP smoke-test harness. Use MCP tools when requested. Do not browse. Do not run shell commands. Return only the requested final line." \
      --model "'"$CLAUDE_MODEL"'" --effort low \
      --mcp-config "$MCP_CONFIG_FILE" --strict-mcp-config \
      --permission-mode bypassPermissions --tools "" --max-turns 8 \
      --disable-slash-commands --output-format text --no-session-persistence < /dev/null
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
  echo "❌ receipt-tools MCP NOT available to headless claude. Check the strict MCP config path,"
  echo "   Python interpreter, and scripts/receipt_mcp_server.py startup logs."
  exit 1
else
  echo "⚠️  Inconclusive — inspect /tmp/mcp_smoke_test.out (read PASS but write FAIL =>"
  echo "    AWS creds / DynamoDB permissions issue on the Mac Mini)."
  exit 1
fi

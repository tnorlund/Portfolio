#!/usr/bin/env bash
# synthesis_loop/judge_round.sh — the EYES. Headless `claude -p` reviews the
# round's renders and records a visual-realism verdict per candidate via MCP.
#
# This is lifted from the PROVEN scripts/mac_mini_mcp_smoke_test.sh (LOCAL path):
# stdio MCP, MCP_CONNECTION_NONBLOCKING=0 (so the deferred tool list is not frozen
# before the server connects), API-key refused, subscription/OAuth only, cheap
# haiku judge. Run on the mini's GUI session (unlocked Keychain) OR with a
# CLAUDE_CODE_OAUTH_TOKEN in ~/.claude_batch_env for SSH/headless.
#
# Inputs (env):  ROUND, RENDER_DIR, OUT_JSON
set -euo pipefail

ROUND="${ROUND:?}"; RENDER_DIR="${RENDER_DIR:?}"; OUT_JSON="${OUT_JSON:?}"
DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME:-ReceiptsTable-dc5be22}"
CLAUDE_MODEL="${CLAUDE_MODEL:-haiku}"        # cheap judge; bump to sonnet for harder calls
MCP_TIMEOUT_MS="${MCP_TIMEOUT_MS:-180000}"
PYTHON_BIN="${PYTHON_BIN:-$HOME/.coreml-venv/bin/python}"
PROJECT_DIR="${PROJECT_DIR:-$PWD}"

export PATH="$HOME/.local/bin:$PATH" RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1
[ -f "$HOME/.claude_batch_env" ] && . "$HOME/.claude_batch_env"
if [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  echo "Refusing: Anthropic API auth env set. Use CLAUDE_CODE_OAUTH_TOKEN (subscription)."; exit 1
fi

MCP_CONFIG_FILE="$(mktemp)"
trap 'rm -f "$MCP_CONFIG_FILE" "$PROMPT_FILE"' EXIT
cat > "$MCP_CONFIG_FILE" <<EOF
{
  "mcpServers": {
    "receipt-tools-local": {
      "type": "stdio",
      "alwaysLoad": true,
      "timeout": $MCP_TIMEOUT_MS,
      "command": "$PYTHON_BIN",
      "args": ["$PROJECT_DIR/scripts/receipt_mcp_server.py"],
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

PROMPT_FILE="$(mktemp)"
cat > "$PROMPT_FILE" <<EOF
You are the visual-realism JUDGE for synthetic receipts. You have the receipt-tools MCP.
Round under review: $ROUND. Newly rendered candidates are in: $RENDER_DIR
(each synthetic render sits beside the REAL receipt it was derived from).

For each candidate:
1. Call list_synthetic_receipt_visual_review_targets to get the targets for this round.
2. Look at the synthetic render next to its real base. Judge how hard it is to tell apart.
3. Call record_synthetic_receipt_visual_review with: a realism score in [0,1], a status
   (accepted / needs_work / rejected), and SPECIFIC critiques that name the defect and the
   renderer knob or code it points to (e.g. "body glyph weight too heavy", "paper noise too
   uniform -> raise --noise variance", "row pitch 2px too tight"). Concrete, actionable notes —
   the next Codex round acts on exactly these.
Finally call summarize_synthetic_receipt_visual_reviews and output its JSON as the LAST line.
Do not run shell commands. Do not browse. Subscription auth only.
EOF

env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
  CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 \
  MCP_CONNECTION_NONBLOCKING=0 \
  MCP_CONNECT_TIMEOUT_MS="$MCP_TIMEOUT_MS" MCP_TIMEOUT="$MCP_TIMEOUT_MS" MCP_TOOL_TIMEOUT="$MCP_TIMEOUT_MS" \
  RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1 \
  claude -p "$(cat "$PROMPT_FILE")" \
    --system-prompt "You are a noninteractive synthetic-receipt visual-realism judge. Use MCP tools. Return only the requested JSON on the final line." \
    --model "$CLAUDE_MODEL" --effort low \
    --mcp-config "$MCP_CONFIG_FILE" --strict-mcp-config \
    --permission-mode bypassPermissions --tools "" --max-turns 24 \
    --disable-slash-commands --output-format text --no-session-persistence < /dev/null \
  | tee "$OUT_JSON.raw"

# keep only the final JSON line as the round's machine-readable review
tail -1 "$OUT_JSON.raw" > "$OUT_JSON" || true
echo "judge round $ROUND -> $OUT_JSON"

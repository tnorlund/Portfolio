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
CLAUDE_MODEL="${CLAUDE_MODEL:-sonnet}"       # vision judge: sonnet discriminates thermal-paper subtleties; haiku too weak
CLAUDE_EFFORT="${CLAUDE_EFFORT:-medium}"     # some reasoning helps the two-axis judgment
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
You are the JUDGE for synthetic receipts. You have full tools (Read, Glob, Grep, Bash) AND the
receipt-tools MCP. Round under review: $ROUND. Newly rendered candidates are in: $RENDER_DIR

Each candidate has a side-by-side composite "<id>.real_vs_synthetic.png" (REAL on one side, SYNTHETIC
on the other, labeled) plus "<id>.synthetic.png". You MUST actually look at the pixels.

For each candidate:
1. Glob "$RENDER_DIR" for *.real_vs_synthetic.png and Read each one so you can SEE it.
2. Call list_synthetic_receipt_visual_review_targets to map renders to review targets. If a structured
   bundle / candidate JSON path is given, Read it so you can check the STRUCTURE (item lines, that
   subtotal + tax = total, label sanity), not just the look.
3. Score TWO axes in [0,1] and explain each:
   - texture_realism: paper noise, blur, thermal look, glyph weight — would this pass as a photo of a
     real receipt? (gates the gallery)
   - structural_plausibility: row pitch, box/line geometry, plausible items for this merchant, arithmetic
     that adds up — is the underlying receipt structure believable? (this is what flows into training)
4. Call record_synthetic_receipt_visual_review with both scores, an overall status
   (accepted / needs_work / rejected), and SPECIFIC, actionable critiques that name the defect AND the
   knob or code it points to — e.g. "paper noise too uniform -> raise --noise variance", "body glyph
   weight too heavy -> rendering/glyph_renderer.py stroke", "row pitch 2px too tight", "tax 0.0 but items
   taxable". The next Codex round acts on exactly these words, so be precise.
Finally call summarize_synthetic_receipt_visual_reviews and output its JSON as the LAST line.
You may read files and inspect freely. Do NOT modify code, commit, push, or deploy. Subscription auth only.
EOF

env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
  CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 \
  MCP_CONNECTION_NONBLOCKING=0 \
  MCP_CONNECT_TIMEOUT_MS="$MCP_TIMEOUT_MS" MCP_TIMEOUT="$MCP_TIMEOUT_MS" MCP_TOOL_TIMEOUT="$MCP_TIMEOUT_MS" \
  RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1 \
  claude -p "$(cat "$PROMPT_FILE")" \
    --system-prompt "You are a noninteractive synthetic-receipt judge. Look at the render PNGs with Read, use MCP tools to record verdicts. Return only the requested JSON on the final line." \
    --model "$CLAUDE_MODEL" --effort "$CLAUDE_EFFORT" \
    --mcp-config "$MCP_CONFIG_FILE" --strict-mcp-config \
    --add-dir "$RENDER_DIR" \
    --permission-mode bypassPermissions --max-turns 40 \
    --disable-slash-commands --output-format json --no-session-persistence < /dev/null \
  > "$OUT_JSON.raw"

# --output-format json always emits a final envelope; .result holds the judge's text.
# Pull the last {...} JSON block out of it (the judge ends with a JSON summary). The
# AUTHORITATIVE per-candidate scores live in Dynamo via record_synthetic_receipt_visual_review;
# this stdout summary is a convenience/fallback. (Dry-run on this MacBook confirmed text format
# could come back empty, while json+.result is reliable.)
"$PYTHON_BIN" - "$OUT_JSON.raw" "$OUT_JSON" <<'PY' || true
import json, re, sys
raw = open(sys.argv[1]).read()
try:
    result = json.loads(raw).get("result", "")
except Exception:
    result = raw
blocks = re.findall(r"\{.*\}|\[.*\]", result, re.S)
open(sys.argv[2], "w").write(blocks[-1] if blocks else result)
PY
echo "judge round $ROUND -> $OUT_JSON"

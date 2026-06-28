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
CLAUDE_MODEL="${CLAUDE_MODEL:-opus}"         # review steps use opus (best visual + structural discrimination)
CLAUDE_EFFORT="${CLAUDE_EFFORT:-high}"       # opus + high effort for the two-axis realism/structure judgment
MCP_TIMEOUT_MS="${MCP_TIMEOUT_MS:-180000}"
PYTHON_BIN="${PYTHON_BIN:-$([ -x "$HOME/.synth-venv/bin/python" ] && echo "$HOME/.synth-venv/bin/python" || echo "$HOME/.coreml-venv/bin/python")}"
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
        "PYTHONPATH": "$PROJECT_DIR/receipt_dynamo:$PROJECT_DIR/receipt_agent:$PROJECT_DIR/receipt_upload",
        "RECEIPT_AGENT_DISABLE_PAID_LLM": "1",
        "DISABLE_PAID_LLM": "1"
      }
    }
  }
}
EOF

PROMPT_FILE="$(mktemp)"
cat > "$PROMPT_FILE" <<EOF
You are the LEAD JUDGE for synthetic receipts. You have full tools (Read, Glob, Grep, Bash, and the
Agent/Workflow tools) AND the receipt-tools MCP. Round under review: $ROUND. Candidates are in: $RENDER_DIR
Each candidate has a side-by-side composite "<id>.real_vs_synthetic.png" (REAL vs SYNTHETIC, labeled) plus
"<id>.synthetic.png". You MUST look at the actual pixels.

RUN A REVIEW PANEL — don't judge in a single pass. For richer, more reliable verdicts, evaluate each
candidate through these independent LENSES, and **if the Workflow/Agent tools are available, fan out one
subagent per lens (in parallel) and synthesize their findings**; otherwise work the lenses yourself in
sequence. Each lens reviewer must Read the composite PNG and the structured sidecar JSON for itself:
  L1 texture/photographic realism — paper grain, thermal print, blur, ink feathering, glyph weight.
  L2 geometry — row pitch, column alignment, box placement vs the real receipt beside it.
  L3 arithmetic/content integrity — item lines, that subtotal+tax=total, label sanity, price columns.
  L4 merchant authenticity — header/footer/format conventions a real receipt for THIS merchant would have.
  L5 OCR contamination — base-receipt OCR garbage leaked into the synthetic ("$2b0", truncated words, stray
     serif glyphs).
Then ADVERSARIALLY cross-check: would a skeptic instantly tell this is synthetic? Note where lenses disagree.

For each candidate, synthesize the panel into:
  - texture_realism in [0,1]  (gates the gallery)
  - structural_plausibility in [0,1]  (this flows into training)
  - status: accepted / needs_work / rejected
  - 3-6 SPECIFIC critiques, each naming the defect AND the knob or code it points to — e.g.
    "paper noise too uniform -> raise --noise variance", "glyph weight too heavy -> rendering/glyph_renderer.py
    stroke", "row pitch 2px tight", "subtotal 27.58 + tax 1.50 != total 30.58". The next Codex round acts on
    these exact words.
Record each via record_synthetic_receipt_visual_review. Finally call summarize_synthetic_receipt_visual_reviews
and output its JSON as the LAST line. Read/inspect freely. Do NOT modify code, commit, push, or deploy.
EOF

env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
  CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 \
  MCP_CONNECTION_NONBLOCKING=0 \
  MCP_CONNECT_TIMEOUT_MS="$MCP_TIMEOUT_MS" MCP_TIMEOUT="$MCP_TIMEOUT_MS" MCP_TOOL_TIMEOUT="$MCP_TIMEOUT_MS" \
  RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1 \
  claude -p "$(cat "$PROMPT_FILE")" \
    --system-prompt "You are the lead of a noninteractive synthetic-receipt review panel. Look at the render PNGs with Read; use the Workflow/Agent tools to run per-lens reviewers in parallel when available; record verdicts via MCP. Return only the requested JSON on the final line." \
    --model "$CLAUDE_MODEL" --effort "$CLAUDE_EFFORT" \
    --mcp-config "$MCP_CONFIG_FILE" --strict-mcp-config \
    --add-dir "$RENDER_DIR" \
    --permission-mode bypassPermissions --max-turns 80 \
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

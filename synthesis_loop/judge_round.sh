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
RUN_ID="${RUN_ID:-adhoc}"
JUDGE_MODE="${JUDGE_MODE:-lean}"             # lean = one fast opus pass per candidate; panel = full 5-lens fan-out
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

# Panel (deep) vs lean (fast) review instructions
if [ "$JUDGE_MODE" = "panel" ]; then
  METHOD="RUN A 5-LENS PANEL: if the Workflow/Agent tools are available, fan out one subagent per lens IN PARALLEL and synthesize; else work the lenses in sequence. Lenses: L1 texture/photographic realism (paper grain, thermal print, blur, ink feathering, glyph weight); L2 geometry (row pitch, column alignment, box placement vs the real receipt); L3 arithmetic/content (item lines, subtotal+tax=total, label sanity, price columns); L4 merchant authenticity (header/footer/format a real receipt for THIS merchant has); L5 OCR contamination (base-OCR garbage leaked in: \"\$2b0\", truncated words, stray serif glyphs). Then adversarially cross-check: would a skeptic instantly tell it's synthetic?"
else
  METHOD="Do ONE focused pass per candidate (no subagents, be fast): Read the composite PNG and the sidecar JSON, then judge texture realism (paper/thermal/ink/glyph) and structural plausibility (geometry, arithmetic, merchant authenticity, OCR cleanliness)."
fi

PROMPT_FILE="$(mktemp)"
cat > "$PROMPT_FILE" <<EOF
You are the JUDGE for synthetic receipts (run_id=$RUN_ID, round=$ROUND). You have full tools (Read, Glob,
Grep, Bash) AND the receipt-tools MCP. Candidates are in: $RENDER_DIR — each has "<id>.synthetic.png" and,
when available, a side-by-side "<id>.real_vs_synthetic.png" (REAL vs SYNTHETIC). You MUST look at the pixels:
Glob the dir, Read each PNG, and Read the matching sidecar JSON.

METHOD: $METHOD

Score EACH candidate on two axes in [0,1]: texture_realism (would it pass as a photo of a real thermal
receipt?) and structural_plausibility (believable geometry/arithmetic/merchant content?). Give 2-5 SPECIFIC
critiques per candidate, each naming the defect AND the knob/code it points to (e.g. "paper noise too uniform
-> raise noise", "glyph weight too heavy -> rendering/glyph_renderer.py", "row pitch tight"). Record each via
record_synthetic_receipt_visual_review (reasoning should start "run=$RUN_ID round=$ROUND").

Then output ONLY this JSON object as the FINAL line, built from YOUR OWN per-candidate scores (do NOT call or
use summarize — that aggregate is polluted by old reviews):
{"run_id":"$RUN_ID","round":$ROUND,"candidates":[{"candidate_id":"<id>","operation":"<op>","texture_realism":<0..1>,"structural_plausibility":<0..1>,"status":"accepted|needs_work|rejected"}],"top_fixes":["<most impactful fix>","<next>"]}

Read/inspect freely. Do NOT modify code, commit, push, or deploy.
EOF

env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
  CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 \
  MCP_CONNECTION_NONBLOCKING=0 \
  MCP_CONNECT_TIMEOUT_MS="$MCP_TIMEOUT_MS" MCP_TIMEOUT="$MCP_TIMEOUT_MS" MCP_TOOL_TIMEOUT="$MCP_TIMEOUT_MS" \
  RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1 \
  claude -p "$(cat "$PROMPT_FILE")" \
    --system-prompt "You are a noninteractive synthetic-receipt judge. Read the render PNGs to see them; score each candidate's texture_realism + structural_plausibility from YOUR OWN inspection (never from the polluted summarize aggregate); record via MCP. Output only the contract JSON object on the final line." \
    --model "$CLAUDE_MODEL" --effort "$CLAUDE_EFFORT" \
    --mcp-config "$MCP_CONFIG_FILE" --strict-mcp-config \
    --add-dir "$RENDER_DIR" \
    --permission-mode bypassPermissions --max-turns 80 \
    --disable-slash-commands --output-format json --no-session-persistence < /dev/null \
  > "$OUT_JSON.raw"

# --output-format json emits a final envelope; .result holds the judge's text. Extract the
# contract object (the one with "candidates") — this is the loop's HONEST signal, not summarize.
"$PYTHON_BIN" - "$OUT_JSON.raw" "$OUT_JSON" "$RUN_ID" "$ROUND" <<'PY' || true
import json, re, sys
raw = open(sys.argv[1]).read()
try:
    result = json.loads(raw).get("result", "")
except Exception:
    result = raw
chosen = None
for m in re.findall(r"\{.*?\"candidates\".*?\}(?=\s*$|\s*\n)", result, re.S):
    chosen = m  # last object that has a candidates key
if chosen is None:
    # fall back: any last balanced-ish object containing "candidates", else empty contract
    objs = [o for o in re.findall(r"\{.*\}", result, re.S) if '"candidates"' in o]
    chosen = objs[-1] if objs else json.dumps(
        {"run_id": sys.argv[3], "round": int(sys.argv[4]), "candidates": [], "top_fixes": []})
# validate it parses; if not, write an empty (failed-round) contract so scoring treats it honestly
try:
    json.loads(chosen)
except Exception:
    chosen = json.dumps({"run_id": sys.argv[3], "round": int(sys.argv[4]), "candidates": [], "top_fixes": []})
open(sys.argv[2], "w").write(chosen)
PY
echo "judge round $ROUND ($JUDGE_MODE) -> $OUT_JSON"

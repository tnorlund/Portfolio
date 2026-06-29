#!/usr/bin/env bash
# synthesis_loop/line_review.sh — the LINE-LEVEL EYES. A headless `claude -p` opus
# pass that reviews ONE rendered synthetic receipt LINE BY LINE (top to bottom) and
# emits a granular per-line critique. The structural verifier (verify_candidates.py)
# checks geometry/arithmetic from the JSON but is BLIND to visual defects; this pass
# Reads the actual pixels and catches them (mis-alignment, touching words, garbled
# glyphs, two amounts on one line, etc.).
#
# Hardening is lifted verbatim from the PROVEN judge_round.sh: API-key refusal,
# subscription/OAuth-only auth via ~/.claude_batch_env, the env -u + terminal-title +
# MCP_CONNECTION_NONBLOCKING hardening on the `claude -p` call, Read enabled via
# --add-dir, --permission-mode bypassPermissions, --output-format json,
# --no-session-persistence, --model opus, and the python contract extractor.
#
# Unlike judge_round.sh this is a PURE VISUAL review (Read the PNG + the candidate
# JSON), so it drops the receipt-tools MCP entirely (no --mcp-config / no
# receipt_mcp_server) — simpler and faster.
#
# Inputs (env):
#   RENDER_DIR  (req) dir holding "<id>.synthetic.png" and/or "<id>.real_vs_synthetic.png"
#   OUT_JSON    (req) where to write the extracted line-review contract object
#   CAND_JSON   (opt) candidate JSON (tokens / bboxes / ner_tags) = the INTENDED line text
#   MERCHANT    (opt) merchant name for authenticity judgement (default below)
#   CLAUDE_MODEL=opus  CLAUDE_EFFORT=medium  PYTHON_BIN  PROJECT_DIR
set -euo pipefail

RENDER_DIR="${RENDER_DIR:?set RENDER_DIR}"; OUT_JSON="${OUT_JSON:?set OUT_JSON}"
CAND_JSON="${CAND_JSON:-}"
MERCHANT="${MERCHANT:-the merchant}"
CLAUDE_MODEL="${CLAUDE_MODEL:-opus}"         # opus = best visual discrimination
CLAUDE_EFFORT="${CLAUDE_EFFORT:-medium}"     # medium: line-by-line read, not deep reasoning
MAX_TURNS="${MAX_TURNS:-20}"                 # wall-clock guard
PYTHON_BIN="${PYTHON_BIN:-$([ -x "$HOME/.synth-venv/bin/python" ] && echo "$HOME/.synth-venv/bin/python" || echo "$HOME/.coreml-venv/bin/python")}"
PROJECT_DIR="${PROJECT_DIR:-$PWD}"

export PATH="$HOME/.local/bin:$PATH" RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1
[ -f "$HOME/.claude_batch_env" ] && . "$HOME/.claude_batch_env"
if [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  echo "Refusing: Anthropic API auth env set. Use CLAUDE_CODE_OAUTH_TOKEN (subscription)."; exit 1
fi

PROMPT_FILE="$(mktemp)"
trap 'rm -f "$PROMPT_FILE"' EXIT

# Tell the model where the intended line text lives (only if a candidate JSON is given).
if [ -n "$CAND_JSON" ] && [ -f "$CAND_JSON" ]; then
  CAND_HINT="The candidate JSON with the INTENDED line text (tokens / bboxes / ner_tags) is at:
  $CAND_JSON
Read it FIRST to learn what each line is SUPPOSED to say, then compare it against the pixels."
  CAND_ID="$(basename "$CAND_JSON")"
else
  CAND_HINT="No candidate JSON was provided — read the line text directly off the rendered pixels."
  CAND_ID="unknown"
fi

PROMPT_FILE_BODY="You are the LINE-LEVEL visual reviewer for a synthetic receipt rendered to look like a real
$MERCHANT thermal receipt. You have the Read and Glob tools. The render(s) are in: $RENDER_DIR

STEP 1 — Glob $RENDER_DIR and Read the *.real_vs_synthetic.png if one exists (REAL on the left,
SYNTHETIC on the right — use the real one as the ground-truth look); otherwise Read the *.synthetic.png.
You MUST actually look at the pixels.
STEP 2 — $CAND_HINT

STEP 3 — Walk the SYNTHETIC receipt LINE BY LINE, top to bottom. Number lines starting at 1 from the
very top (logo/header) down through the footer. For EACH visible line report:
  - text: the line's text as rendered (best transcription of what you SEE)
  - ok: true if this line looks realistic for a real $MERCHANT receipt, false if anything is off
  - issue: a SPECIFIC, located defect or \"\" if none. Examples of the granularity wanted:
      \"price not right-aligned with the column above\", \"two amounts crammed on one line\",
      \"logo subtitle is garbled / unreadable glyphs\", \"words touching with no space\",
      \"font weight too heavy vs the rest\", \"line baseline tilted\", \"OCR garbage leaked in (e.g. \$2b0)\",
      \"item description overruns the price column\", \"row pitch too tight vs neighbors\".
Judge spacing, alignment, font/glyph weight, column discipline, and plausibility of the content for a
REAL $MERCHANT receipt. Be concrete and name WHERE on the line the problem is.

STEP 4 — Output ONLY this JSON object as the FINAL line of your reply (no prose after it):
{\"merchant\":\"$MERCHANT\",\"candidate\":\"$CAND_ID\",\"lines\":[{\"n\":1,\"text\":\"...\",\"ok\":true,\"issue\":\"\"}],\"worst_lines\":[\"line N: short reason\",\"line M: short reason\"],\"summary\":\"one-sentence overall verdict\"}

worst_lines = the 2-5 lines a skeptic would flag first. Do NOT modify, write, commit, or deploy anything —
read only."

printf '%s\n' "$PROMPT_FILE_BODY" > "$PROMPT_FILE"

# --add-dir the render dir so Read can open the PNGs; also add the candidate JSON's dir
# if it lives outside the render dir, so the model can Read the intended line text.
ADD_DIRS=(--add-dir "$RENDER_DIR")
if [ -n "$CAND_JSON" ] && [ -f "$CAND_JSON" ]; then
  CAND_DIR="$(cd "$(dirname "$CAND_JSON")" && pwd)"
  if [ "$CAND_DIR" != "$(cd "$RENDER_DIR" && pwd)" ]; then
    ADD_DIRS+=(--add-dir "$CAND_DIR")
  fi
fi

env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
  CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 \
  MCP_CONNECTION_NONBLOCKING=0 \
  RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1 \
  claude -p "$(cat "$PROMPT_FILE")" \
    --system-prompt "You are a noninteractive line-level visual reviewer for synthetic receipts. Read the render PNG(s) to actually see them, then critique the synthetic receipt one line at a time. Output only the contract JSON object on the final line." \
    --model "$CLAUDE_MODEL" --effort "$CLAUDE_EFFORT" \
    "${ADD_DIRS[@]}" \
    --permission-mode bypassPermissions --max-turns "$MAX_TURNS" \
    --disable-slash-commands --output-format json --no-session-persistence < /dev/null \
  > "$OUT_JSON.raw"

# --output-format json emits a final envelope; .result holds the reviewer's text.
# Extract the last contract object containing "lines"; on any failure write {"lines":[]}.
"$PYTHON_BIN" - "$OUT_JSON.raw" "$OUT_JSON" "$MERCHANT" "$CAND_ID" <<'PY' || true
import json, re, sys
raw = open(sys.argv[1]).read()
try:
    result = json.loads(raw).get("result", "")
except Exception:
    result = raw
empty = json.dumps({"merchant": sys.argv[3], "candidate": sys.argv[4], "lines": [],
                    "worst_lines": [], "summary": ""})
chosen = None
# Preferred: walk the text with a JSON decoder, keep the LAST object that has a "lines" key.
dec = json.JSONDecoder()
i = 0
while i < len(result):
    j = result.find("{", i)
    if j < 0:
        break
    try:
        obj, end = dec.raw_decode(result, j)
    except ValueError:
        i = j + 1
        continue
    if isinstance(obj, dict) and "lines" in obj:
        chosen = json.dumps(obj)
    i = end
if chosen is None:
    # regex fallback: last brace-blob that mentions "lines"
    objs = [o for o in re.findall(r"\{.*?\"lines\".*?\}(?=\s*$|\s*\n)", result, re.S) if '"lines"' in o]
    chosen = objs[-1] if objs else empty
try:
    json.loads(chosen)
except Exception:
    chosen = empty
open(sys.argv[2], "w").write(chosen)
PY
echo "line_review ($MERCHANT) -> $OUT_JSON"

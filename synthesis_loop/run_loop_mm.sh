#!/usr/bin/env bash
# synthesis_loop/run_loop_mm.sh — autonomous multi-merchant synthesis hill-climb.
#
# Honest score this time: the OBJECTIVE verifier across ALL merchants (can't be gamed like the
# old opus-judge loop). Each round:
#   1. BRAIN  (codex)  : fix ONE issue in the SHARED reconcile module / generic path, applied to
#                        ALL merchants (NEVER merchant-specific — that was the old mistake).
#   2. REGEN  (shell)  : regenerate + render EVERY merchant via bundle mode (needs Dynamo/network).
#   3. SCORE  (shell)  : verifier across all merchants -> overall_mean + per-merchant failing checks.
#   4. VISION (shell)  : opus LINE-LEVEL review of one rotating merchant's render -> per-line critiques.
#   5. RATCHET         : keep the code change iff overall_mean improved, else revert it.
#   6. feedback.json (failing checks + line critiques) drives the next BRAIN round.
# Heartbeat = plain shell; codex (brain) and claude (vision) are SIBLINGS, never nested (no sandbox hang).
set -uo pipefail

_self="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO="${REPO:-$(cd "$_self/.." && pwd)}"
BRANCH="${BRANCH:-feat/synthesis-hill-climb}"
MAX_ROUNDS="${MAX_ROUNDS:-50}"; SLEEP_SECS="${SLEEP_SECS:-30}"; NO_IMPROVE_STOP="${NO_IMPROVE_STOP:-4}"
REVIEW_EVERY="${REVIEW_EVERY:-5}"; PR_NUMBER="${PR_NUMBER:-1022}"; REPO_SLUG="${REPO_SLUG:-tnorlund/Portfolio}"
PYTHON_BIN="${PYTHON_BIN:-$([ -x "$HOME/.synth-venv/bin/python" ] && echo "$HOME/.synth-venv/bin/python" || echo "$HOME/.coreml-venv/bin/python")}"
CODEX_BIN="${CODEX_BIN:-$([ -x /Applications/Codex.app/Contents/Resources/codex ] && echo /Applications/Codex.app/Contents/Resources/codex || echo codex)}"
GH_BIN="${GH_BIN:-$(command -v gh || echo /opt/homebrew/bin/gh)}"
CODEX_PROFILE="${CODEX_PROFILE:-synthesis-loop}"
MM="${MM:-/tmp/mm}"; STATE="$REPO/synthesis_loop/state"; HERE="$REPO/synthesis_loop"
RUN_ID="${RUN_ID:-$("$PYTHON_BIN" -c 'import uuid;print(uuid.uuid4().hex[:12])')}"
# merchants rotated for the (slower) line-level vision review, one per round
MERCHANTS=(costco_wholesale the_home_depot target gelsons_westlake_village vons smiths amazon_fresh sprouts_farmers_market)

cd "$REPO"
export PYTHONPATH="$REPO/receipt_dynamo:$REPO/receipt_agent:$REPO/receipt_upload${PYTHONPATH:+:$PYTHONPATH}"
export DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME:-ReceiptsTable-dc5be22}" PORTFOLIO_ENV="${PORTFOLIO_ENV:-dev}"
mkdir -p "$STATE"
git rev-parse --abbrev-ref HEAD | grep -qx "$BRANCH" || { echo "Refusing: not on $BRANCH"; exit 1; }
case "$BRANCH" in main|integration/*) echo "Refusing: protected"; exit 1;; esac
rm -f "$STATE/MM_STATUS.md"
echo "RUN_ID=$RUN_ID  python=$PYTHON_BIN  codex=$CODEX_BIN  (multi-merchant, honest verifier + line vision)"

best=-1; no_improve=0
for ((round=1; round<=MAX_ROUNDS; round++)); do
  echo "================ ROUND $round ================"
  fb="$STATE/feedback.json"; [ -f "$fb" ] || echo '{}' > "$fb"
  prev_head="$(git rev-parse HEAD)"

  # 1. BRAIN — fix ONE issue in the SHARED/generic path, applied to ALL merchants.
  "$CODEX_BIN" exec --profile "$CODEX_PROFILE" --cd "$REPO" \
    "Round $round of the multi-merchant synthesis hill-climb. Read synthesis_loop/AGENTS_MM.md for the contract \
and synthesis_loop/state/feedback.json (per-merchant failing verifier checks + opus line-level critiques from \
last round). Fix the SINGLE highest-impact issue. CRITICAL RULES: (a) your fix MUST be GENERALIZABLE and applied \
to ALL merchants — edit the shared module \
receipt_agent/receipt_agent/agents/label_evaluator/synthesis_reconcile.py (or the generic \
merchant_synthesis.py path), NEVER a single-merchant file like sprouts_parameterization.py; (b) it must not \
regress any currently-passing verifier check (bbox/no_overlap/word_spacing/single_header/single_payment_block/ \
no_garble/arithmetic/labels_valid). Then commit with a message naming the check + merchants you targeted. Do \
NOT render, push, call DynamoDB/AWS, spawn claude, or merge/deploy." \
    || echo "codex round $round failed; continuing"

  # 2. REGEN all merchants (shell has network for the Dynamo glyph atlas + synthesis)
  OUT="$MM" RENDER_LIMIT=2 bash "$HERE/regen_merchants.sh" 2>&1 | tail -10

  # 3. SCORE all merchants -> overall_mean + per-merchant failing checks -> feedback
  score_out="$("$PYTHON_BIN" "$HERE/score_all_merchants.py" "$MM" "$STATE/scores.json" 2>&1 || true)"
  echo "$score_out"
  overall="$("$PYTHON_BIN" -c "import json;print(json.load(open('$STATE/scores.json')).get('overall_mean') or -1)" 2>/dev/null || echo -1)"

  # 4. VISION — opus line-level review of one rotating merchant's render
  ms="${MERCHANTS[$(( (round-1) % ${#MERCHANTS[@]} ))]}"
  if [ -d "$MM/$ms/render" ]; then
    cj="$(ls -1 "$MM/$ms"/render/*.synthetic.png 2>/dev/null | head -1)"; cj="${cj%.synthetic.png}"
    RENDER_DIR="$MM/$ms/render" CAND_JSON="${cj}.json" MERCHANT="$ms" \
      OUT_JSON="$STATE/line_review-$ms.json" bash "$HERE/line_review.sh" >/dev/null 2>&1 || echo "line review ($ms) failed"
  fi
  # merge scores + the line review into feedback.json for next round's brain
  "$PYTHON_BIN" - "$STATE/scores.json" "$STATE/line_review-$ms.json" "$STATE/feedback.json" "$round" <<'PY' 2>/dev/null || true
import json,sys
scores=json.load(open(sys.argv[1])) if __import__("os").path.exists(sys.argv[1]) else {}
lr={}
try: lr=json.load(open(sys.argv[2]))
except Exception: pass
fb={"round":int(sys.argv[4]),"overall_mean":scores.get("overall_mean"),
    "per_merchant":scores.get("merchants",{}),
    "line_review":{"merchant":lr.get("merchant"),"worst_lines":lr.get("worst_lines"),
                   "bad_lines":[l for l in (lr.get("lines") or []) if l.get("ok") is False][:12]}}
json.dump(fb,open(sys.argv[3],"w"),indent=2)
PY

  # 5. RATCHET on overall_mean across all merchants
  improved=$("$PYTHON_BIN" -c "print(1 if float('$overall') > float('$best') else 0)" 2>/dev/null || echo 0)
  if [ "$improved" = "1" ]; then
    best="$overall"; no_improve=0; verdict="IMPROVED"
  else
    no_improve=$((no_improve+1)); verdict="NOIMP (revert code)"
    git reset --hard "$prev_head" >/dev/null 2>&1   # the code change didn't help -> drop it
  fi
  printf -- "- round %s: overall_mean=%s best=%s  %s\n" "$round" "$overall" "$best" "$verdict" | tee -a "$STATE/MM_STATUS.md"

  # 6. commit state + push
  git add -A "$STATE/MM_STATUS.md" "$STATE/scores.json" "$STATE/feedback.json" "$STATE"/line_review-*.json 2>/dev/null || true
  git commit -m "loop-mm: round $round (run=$RUN_ID) overall_mean=$overall $verdict" 2>/dev/null \
    && git push origin "$BRANCH" 2>&1 | tail -1 || true
  if [ "$REVIEW_EVERY" -gt 0 ] && [ $((round % REVIEW_EVERY)) -eq 0 ]; then
    "$GH_BIN" pr comment "$PR_NUMBER" --body "@codex review — multi-merchant loop round $round, overall_mean $overall." 2>&1 | tail -1 || true
  fi
  [ "$no_improve" -ge "$NO_IMPROVE_STOP" ] && { echo "plateaued — stopping"; break; }
  sleep "$SLEEP_SECS"
done
echo "loop-mm done after round $round (run $RUN_ID, best overall_mean=$best)"

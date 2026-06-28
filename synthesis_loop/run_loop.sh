#!/usr/bin/env bash
# synthesis_loop/run_loop.sh — the HEARTBEAT of the hill-climb.
#
# Runs on the Mac mini inside `screen` (see bootstrap_mini.sh). Plain bash, NOT
# sandboxed and NOT nested under any agent — this is what makes "codex drives,
# claude judges" not hang: codex and claude are spawned as SIBLINGS here, never
# one inside the other's sandbox.
#
# One round:           codex (brain) -> git push -> claude (eyes) -> log -> sleep
#   - codex exec   : reads last review, edits params/code, re-renders, commits      (no network needed; OpenAI calls are codex-internal)
#   - git push     : done HERE in the shell (network), never inside codex's sandbox
#   - judge_round  : headless claude -p scores the new renders -> Dynamo + reviews/  (sibling process, full network)
#
# Usage:  ./synthesis_loop/run_loop.sh            # default: run until MAX_ROUNDS or 3 no-improve rounds
#         MAX_ROUNDS=20 SLEEP_SECS=120 ./synthesis_loop/run_loop.sh
set -euo pipefail

# ---- config (override via env) ------------------------------------------------
REPO="${REPO:-$HOME/Portfolio}"
BRANCH="${BRANCH:-feat/synthesis-hill-climb}"
MAX_ROUNDS="${MAX_ROUNDS:-50}"
SLEEP_SECS="${SLEEP_SECS:-90}"
NO_IMPROVE_STOP="${NO_IMPROVE_STOP:-3}"
REVIEW_EVERY="${REVIEW_EVERY:-5}"                  # request + respond-to a Codex bot review every N rounds
PR_NUMBER="${PR_NUMBER:-1022}"
REPO_SLUG="${REPO_SLUG:-tnorlund/Portfolio}"
GH_BIN="${GH_BIN:-$(command -v gh || echo /opt/homebrew/bin/gh)}"
CODEX_REVIEW_BOT="${CODEX_REVIEW_BOT:-chatgpt-codex-connector[bot]}"  # the GitHub cloud Codex code-review bot (NOT the local judge)
# .synth-venv has the receipt_agent langchain deps that rendering needs; coreml-venv does not.
PYTHON_BIN="${PYTHON_BIN:-$([ -x "$HOME/.synth-venv/bin/python" ] && echo "$HOME/.synth-venv/bin/python" || echo "$HOME/.coreml-venv/bin/python")}"
CODEX_PROFILE="${CODEX_PROFILE:-synthesis-loop}"   # defined in ~/.codex/config.toml (see codex-profile.toml)
# On the mini, /opt/homebrew/bin/codex is a broken node shim; the app binary is self-contained.
CODEX_BIN="${CODEX_BIN:-$([ -x /Applications/Codex.app/Contents/Resources/codex ] && echo /Applications/Codex.app/Contents/Resources/codex || echo codex)}"
DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME:-ReceiptsTable-dc5be22}"
STATE="$REPO/synthesis_loop/state"
HERE="$REPO/synthesis_loop"

cd "$REPO"
# render imports need the worktree packages ahead of any stale pip-installed receipt_dynamo;
# codex inherits this env, so the render commands it runs resolve correctly.
export PYTHONPATH="$REPO/receipt_dynamo:$REPO/receipt_agent:$REPO/receipt_upload${PYTHONPATH:+:$PYTHONPATH}"
export DYNAMODB_TABLE_NAME PORTFOLIO_ENV="${PORTFOLIO_ENV:-dev}" RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1
mkdir -p "$STATE/reviews" "$STATE/renders"

# ---- safety: never run on a protected branch ----------------------------------
git rev-parse --abbrev-ref HEAD | grep -qx "$BRANCH" || {
  echo "Refusing to run: not on $BRANCH (on $(git rev-parse --abbrev-ref HEAD))"; exit 1; }
case "$BRANCH" in main|integration/*) echo "Refusing: $BRANCH is protected"; exit 1;; esac

# ---- GitHub Codex code-review bot cycle (DISTINCT from the local Claude image judge) ----
# Every REVIEW_EVERY rounds: (i) respond to the PREVIOUS bot review by having codex apply its
# inline P1/P2 findings, then push + reply on the PR; (ii) request a fresh review.
# Network (gh fetch/reply, git push) is done HERE in the shell; codex only edits+commits (its
# sandbox has no network), so it never hangs and never touches gh.
review_cycle() {
  local round="$1"
  if [ -f "$STATE/pending_review.flag" ]; then
    echo ">> responding to the Codex bot review on PR #$PR_NUMBER"
    "$GH_BIN" api "repos/$REPO_SLUG/pulls/$PR_NUMBER/comments" --paginate \
      --jq "[.[]|select(.user.login==\"$CODEX_REVIEW_BOT\")|{id,path,line:(.line//.original_line),body}]" \
      > "$STATE/codex_review_comments.json" 2>/dev/null || echo "[]" > "$STATE/codex_review_comments.json"
    local n; n=$("$PYTHON_BIN" -c "import json;print(len(json.load(open('$STATE/codex_review_comments.json'))))" 2>/dev/null || echo 0)
    if [ "${n:-0}" -gt 0 ]; then
      "$CODEX_BIN" exec --profile "$CODEX_PROFILE" --cd "$REPO" \
        "Read synthesis_loop/state/codex_review_comments.json — inline findings from the GitHub Codex review bot \
($CODEX_REVIEW_BOT) on PR #$PR_NUMBER, each with path/line/body and a P1/P2/P3 badge. Apply minimal fixes for \
the P1/P2 findings that touch this branch's synthesis_loop/ or renderer/synthesis changes; for anything out of \
scope or already handled, note that instead of editing. Run the render-verify guard if you touch renderer code. \
Commit referencing the findings addressed. Do NOT push, do NOT call gh, do NOT spawn claude, do NOT merge/deploy." \
        || echo "codex review-response failed; continuing"
      git push origin "$BRANCH" 2>&1 | tail -1 || true
      "$GH_BIN" pr comment "$PR_NUMBER" \
        --body "🤖 Synthesis loop addressed the Codex review ($n inline findings) in \`$(git rev-parse --short HEAD)\` — see the commit for per-finding handling. (automated; distinct from the local image judge)" \
        2>&1 | tail -1 || echo "gh reply failed"
    else
      echo "no actionable Codex bot findings this cycle"
    fi
    rm -f "$STATE/pending_review.flag"
  fi
  echo ">> requesting a fresh Codex bot review"
  "$GH_BIN" pr comment "$PR_NUMBER" --body "@codex review — synthesis hill-climb rounds up to $round pushed." 2>&1 | tail -1 || true
  touch "$STATE/pending_review.flag"
}

no_improve=0
for ((round=1; round<=MAX_ROUNDS; round++)); do
  echo "================ ROUND $round ================"
  prev_review="$(ls -t "$STATE/reviews"/round-*.json 2>/dev/null | head -1 || true)"

  # --- merchant rotation: active merchant = merchants[round % len] ---
  MERCHANT="$("$PYTHON_BIN" - "$STATE/params.json" "$round" <<'PY'
import json,sys
p=json.load(open(sys.argv[1])); rnd=int(sys.argv[2])
ms=p.get("merchants") or [p.get("merchant","Sprouts")]
print(ms[(rnd-1)%len(ms)])
PY
)"
  echo "merchant this round: $MERCHANT"

  # --- 1. BRAIN: codex edits params/code to address the last critique, then COMMITS (no render, no network) ---
  #   The render needs DynamoDB (network) which codex's sandbox blocks, so codex does NOT render — the shell
  #   does (step 1b). Codex only reasons + edits + commits; its own model calls reach OpenAI regardless.
  prev_head="$(git rev-parse HEAD)"
  "$CODEX_BIN" exec \
    --profile "$CODEX_PROFILE" \
    --cd "$REPO" \
    "Round $round of the synthesis hill-climb, merchant=$MERCHANT. Read synthesis_loop/AGENTS.md for the \
contract. Read the latest Claude review at ${prev_review:-'(none yet — first round; keep the baseline params)'}. \
Address the single highest-impact critique (texture OR structural) for merchant $MERCHANT by editing \
synthesis_loop/state/params.json and/or the renderer/synthesis code under \
receipt_agent/.../label_evaluator/rendering/. Then commit with a message naming the critique you targeted. \
Do NOT render (the loop renders for you), do NOT push, do NOT call DynamoDB/AWS, do NOT spawn claude, do NOT \
merge or deploy." \
    || { echo "codex round $round failed; continuing"; }

  # --- 1b. RENDER in the shell (has network for the Dynamo glyph atlas) + render-verify guard ---
  RENDER_DIR="$STATE/renders/round-$round"; rm -rf "$RENDER_DIR"; mkdir -p "$RENDER_DIR" "$STATE/gallery"
  render_round() {
    "$PYTHON_BIN" "$HERE/render_from_params.py" --params "$STATE/params.json" --merchant "$MERCHANT" \
      --out-dir "$RENDER_DIR" --repo "$REPO"
  }
  if ! render_round || ! ls "$RENDER_DIR"/*.png >/dev/null 2>&1; then
    echo "render produced no PNGs — reverting codex's round commit and retrying with prior code"
    git reset --hard "$prev_head"
    render_round || echo "baseline render still failing — judge will see an empty round"
  fi
  cp "$(ls -1 "$RENDER_DIR"/*real_vs_synthetic.png "$RENDER_DIR"/*.png 2>/dev/null | head -1)" \
     "$STATE/gallery/round-$round.png" 2>/dev/null || true
  git add -A "$STATE/gallery" "$STATE/params.json" 2>/dev/null
  git commit -m "loop: round $round renders (merchant=$MERCHANT)" 2>/dev/null || true

  # --- 2. PUSH: in the shell (network), never inside codex ---
  git push origin "$BRANCH" 2>&1 | tail -2 || echo "push failed (will retry next round)"

  # --- 3. EYES: headless claude judges the new renders (sibling process) ---
  ROUND="$round" RENDER_DIR="$RENDER_DIR" OUT_JSON="$STATE/reviews/round-$round.json" \
    bash "$HERE/judge_round.sh" || echo "judge round $round failed; continuing"

  # --- 4. LOG + hill-climb bookkeeping (best.json / STATUS.md) ---
  "$PYTHON_BIN" "$HERE/score_round.py" --round "$round" --state "$STATE" || true
  git add -A "$STATE/STATUS.md" "$STATE/best.json" "$STATE/gallery" "$STATE/reviews/round-$round.json" 2>/dev/null || true
  git commit -m "loop: round $round status (merchant=$MERCHANT)" 2>/dev/null && git push origin "$BRANCH" 2>&1 | tail -1 || true

  # --- 4b. GitHub Codex bot review cycle every REVIEW_EVERY rounds (respond-to-prev, then request) ---
  if [ "$REVIEW_EVERY" -gt 0 ] && [ $((round % REVIEW_EVERY)) -eq 0 ]; then
    review_cycle "$round"
  fi

  # --- 5. stop if we've plateaued ---
  if "$PYTHON_BIN" "$HERE/score_round.py" --round "$round" --state "$STATE" --check-improved >/dev/null 2>&1; then
    no_improve=0
  else
    no_improve=$((no_improve+1))
    echo "no improvement ($no_improve/$NO_IMPROVE_STOP)"
    [ "$no_improve" -ge "$NO_IMPROVE_STOP" ] && { echo "plateaued — stopping"; break; }
  fi

  sleep "$SLEEP_SECS"
done
echo "loop done after round $round"

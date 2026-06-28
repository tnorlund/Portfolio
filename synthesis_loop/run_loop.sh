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
# default REPO = the repo this script lives in (the worktree), not a hardcoded ~/Portfolio,
# so it works from whichever checkout you launch it in.
_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO="${REPO:-$(cd "$_self_dir/.." && pwd)}"
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

# fresh run identity → scoring is scoped to THIS run (no pollution from old/manual reviews)
RUN_ID="${RUN_ID:-$("$PYTHON_BIN" -c 'import uuid;print(uuid.uuid4().hex[:12])')}"
PANEL_EVERY="${PANEL_EVERY:-5}"          # deep opus 5-lens panel every N rounds; lean opus pass otherwise
rm -f "$STATE/STATUS.md" "$STATE/best.json" "$STATE/last_render.hash"
echo "RUN_ID=$RUN_ID  python=$PYTHON_BIN  codex=$CODEX_BIN  panel_every=$PANEL_EVERY"

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

# hash of a round's PNGs — used to detect no-op rounds (renders byte-identical to last round)
render_hash() { ls -1 "$1"/*.png 2>/dev/null | sort | xargs cat 2>/dev/null | shasum | awk '{print $1}'; }

# deterministically nudge ONE realism knob so a render is guaranteed to differ when codex no-ops
perturb_params() {
  "$PYTHON_BIN" - "$STATE/params.json" "$1" <<'PY'
import json, sys
p = json.load(open(sys.argv[1])); r = int(sys.argv[2])
knob = ["noise", "blur", "paper_realism"][(r - 1) % 3]
step = 0.06 if (r // 3) % 2 == 0 else -0.06
v = round(min(1.0, max(0.0, float(p.get(knob, 0.4)) + step)), 3)
p[knob] = v; json.dump(p, open(sys.argv[1], "w"), indent=2)
print(f"perturbed {knob} -> {v}")
PY
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

  # --- 1. BRAIN: codex edits ONLY the layers the (cached) render actually uses, then COMMITS ---
  #   CRITICAL: cached render runs params.json + receipt_agent/.../rendering/ (glyph_atlas/glyph_renderer/
  #   glyph_ttf_fallback). It does NOT execute sprouts_parameterization/synthesis code, so editing that is a
  #   no-op on the pixels. The change MUST affect the render. Render+network is the shell's job (step 1b).
  prev_head="$(git rev-parse HEAD)"
  "$CODEX_BIN" exec \
    --profile "$CODEX_PROFILE" \
    --cd "$REPO" \
    "Round $round of the synthesis hill-climb, merchant=$MERCHANT. Read synthesis_loop/AGENTS.md (your contract) \
and the latest judge review at ${prev_review:-'(none yet — make one concrete texture improvement to the baseline)'}. \
Make the single highest-impact change to TEXTURE REALISM that will actually alter the rendered pixels. You may \
ONLY edit: synthesis_loop/state/params.json (noise/blur/paper_realism/seed/glyph knobs) and the glyph renderer \
code in receipt_agent/receipt_agent/agents/label_evaluator/rendering/ (glyph_atlas.py, glyph_renderer.py, \
glyph_ttf_fallback.py). Do NOT edit sprouts_parameterization.py or any synthesis/*.py — cached render never \
runs it, so it cannot change the image. Then commit naming the change. Do NOT render, push, call AWS, spawn \
claude, merge, or deploy." \
    || { echo "codex round $round failed; continuing"; }

  # --- 1b. RENDER in the shell (Dynamo glyph atlas needs network) + verify + NO-OP guard ---
  RENDER_DIR="$STATE/renders/round-$round"; rm -rf "$RENDER_DIR"; mkdir -p "$RENDER_DIR" "$STATE/gallery"
  render_round() {
    "$PYTHON_BIN" "$HERE/render_from_params.py" --params "$STATE/params.json" --merchant "$MERCHANT" \
      --out-dir "$RENDER_DIR" --repo "$REPO"
  }
  if ! render_round || ! ls "$RENDER_DIR"/*.png >/dev/null 2>&1; then
    echo "render produced no PNGs — reverting codex's round commit and retrying with prior code"
    git reset --hard "$prev_head"; rm -rf "$RENDER_DIR"; mkdir -p "$RENDER_DIR"; render_round || true
  fi
  # NO-OP guard: if this round's renders are byte-identical to last round, codex's edit didn't reach the
  # pixels — perturb a knob to guarantee a real change, then re-render. (No wasted rounds.)
  cur_hash="$(render_hash "$RENDER_DIR")"; prev_hash="$(cat "$STATE/last_render.hash" 2>/dev/null || true)"
  if [ -n "$cur_hash" ] && [ "$cur_hash" = "$prev_hash" ]; then
    echo "no-op round (renders identical to last) — perturbing a param knob"
    perturb_params "$round"; rm -rf "$RENDER_DIR"; mkdir -p "$RENDER_DIR"; render_round || true
    cur_hash="$(render_hash "$RENDER_DIR")"
  fi
  echo "${cur_hash:-none}" > "$STATE/last_render.hash"
  cp "$(ls -1 "$RENDER_DIR"/*real_vs_synthetic.png "$RENDER_DIR"/*.png 2>/dev/null | head -1)" \
     "$STATE/gallery/round-$round.png" 2>/dev/null || true
  git add -A "$STATE/gallery" "$STATE/params.json" 2>/dev/null
  git commit -m "loop: round $round renders (merchant=$MERCHANT)" 2>/dev/null || true

  # --- 2. PUSH: in the shell (network), never inside codex ---
  git push origin "$BRANCH" 2>&1 | tail -2 || echo "push failed (will retry next round)"

  # --- 3. EYES: headless opus judge — lean pass each round, deep 5-lens panel every PANEL_EVERY ---
  JM=lean; [ "$PANEL_EVERY" -gt 0 ] && [ $((round % PANEL_EVERY)) -eq 0 ] && JM=panel
  RUN_ID="$RUN_ID" JUDGE_MODE="$JM" ROUND="$round" RENDER_DIR="$RENDER_DIR" \
    OUT_JSON="$STATE/reviews/round-$round.json" \
    bash "$HERE/judge_round.sh" || echo "judge round $round failed; continuing"

  # --- 4. SCORE (this run only, honest) + commit/push the scoreboard ---
  "$PYTHON_BIN" "$HERE/score_round.py" --round "$round" --state "$STATE" --run-id "$RUN_ID" || true
  git add -A "$STATE/STATUS.md" "$STATE/best.json" "$STATE/gallery" "$STATE/reviews/round-$round.json" 2>/dev/null || true
  git commit -m "loop: round $round status (run=$RUN_ID, judge=$JM, merchant=$MERCHANT)" 2>/dev/null \
    && git push origin "$BRANCH" 2>&1 | tail -1 || true

  # --- 4b. GitHub Codex bot review cycle every REVIEW_EVERY rounds (respond-to-prev, then request) ---
  if [ "$REVIEW_EVERY" -gt 0 ] && [ $((round % REVIEW_EVERY)) -eq 0 ]; then
    review_cycle "$round"
  fi

  # --- 5. hill-climb ratchet: improved -> reset counter; regressed -> restore best params + count ---
  if "$PYTHON_BIN" "$HERE/score_round.py" --round "$round" --state "$STATE" --run-id "$RUN_ID" --check-improved >/dev/null 2>&1; then
    no_improve=0
  else
    no_improve=$((no_improve+1)); echo "no improvement ($no_improve/$NO_IMPROVE_STOP)"
    # roll params.json back to the best-so-far so the next round climbs from the peak, not the drift
    "$PYTHON_BIN" - "$STATE/best.json" "$STATE/params.json" <<'PY' 2>/dev/null || true
import json, sys
b = json.load(open(sys.argv[1]))
if b.get("params"):
    json.dump(b["params"], open(sys.argv[2], "w"), indent=2)
    print("restored params.json from best (round %s, score %.3f)" % (b.get("round"), b.get("score", 0)))
PY
    [ "$no_improve" -ge "$NO_IMPROVE_STOP" ] && { echo "plateaued — stopping"; break; }
  fi

  sleep "$SLEEP_SECS"
done
echo "loop done after round $round (run $RUN_ID)"

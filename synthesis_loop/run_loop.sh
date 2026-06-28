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
REVIEW_EVERY="${REVIEW_EVERY:-5}"                  # post @codex review on the PR every N rounds
PR_NUMBER="${PR_NUMBER:-1022}"
PYTHON_BIN="${PYTHON_BIN:-$HOME/.coreml-venv/bin/python}"
CODEX_PROFILE="${CODEX_PROFILE:-synthesis-loop}"   # defined in ~/.codex/config.toml (see codex-profile.toml)
STATE="$REPO/synthesis_loop/state"
HERE="$REPO/synthesis_loop"

cd "$REPO"
mkdir -p "$STATE/reviews" "$STATE/renders"

# ---- safety: never run on a protected branch ----------------------------------
git rev-parse --abbrev-ref HEAD | grep -qx "$BRANCH" || {
  echo "Refusing to run: not on $BRANCH (on $(git rev-parse --abbrev-ref HEAD))"; exit 1; }
case "$BRANCH" in main|integration/*) echo "Refusing: $BRANCH is protected"; exit 1;; esac

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

  # --- 1. BRAIN: codex reads the last critique, edits params/code, re-renders, commits ---
  #   --ask-for-approval never  : autonomous, no prompts
  #   --sandbox workspace-write : can edit files + run render scripts; no network needed
  #   (codex's OWN model calls go to OpenAI outside the sandbox and work fine)
  codex exec \
    --profile "$CODEX_PROFILE" \
    --cd "$REPO" \
    "Round $round of the synthesis hill-climb, merchant=$MERCHANT. Read synthesis_loop/AGENTS.md for the \
contract. Read the latest Claude review at ${prev_review:-'(none yet — first round; render a baseline)'}. \
Address the single highest-impact critique (texture OR structural) for merchant $MERCHANT by editing \
synthesis_loop/state/params.json and/or the renderer/synthesis code. After ANY code edit, run the render and \
confirm PNGs appear; if rendering breaks, revert the code change and fall back to a param tweak. Re-render into \
synthesis_loop/state/renders/round-$round/ via scripts/render_synthetic_receipts.py using params.json, copy the \
best render to synthesis_loop/state/gallery/round-$round.png, and commit with a message naming the critique you \
targeted. Do NOT push, do NOT spawn claude, do NOT merge or deploy." \
    || { echo "codex round $round failed; continuing"; }

  # --- 2. PUSH: in the shell (network), never inside codex ---
  git push origin "$BRANCH" 2>&1 | tail -2 || echo "push failed (will retry next round)"

  # --- 3. EYES: headless claude judges the new renders (sibling process) ---
  RENDER_DIR="$STATE/renders/round-$round"
  ROUND="$round" RENDER_DIR="$RENDER_DIR" OUT_JSON="$STATE/reviews/round-$round.json" \
    bash "$HERE/judge_round.sh" || echo "judge round $round failed; continuing"

  # --- 4. LOG + hill-climb bookkeeping (best.json / STATUS.md) ---
  "$PYTHON_BIN" "$HERE/score_round.py" --round "$round" --state "$STATE" || true
  git add -A "$STATE/STATUS.md" "$STATE/best.json" "$STATE/gallery" "$STATE/reviews/round-$round.json" 2>/dev/null || true
  git commit -m "loop: round $round status (merchant=$MERCHANT)" 2>/dev/null && git push origin "$BRANCH" 2>&1 | tail -1 || true

  # --- 4b. ask the Codex review bot to review a batch every REVIEW_EVERY rounds ---
  if [ "$REVIEW_EVERY" -gt 0 ] && [ $((round % REVIEW_EVERY)) -eq 0 ]; then
    gh pr comment "$PR_NUMBER" --body "@codex review — rounds up to $round pushed (merchant rotation: see STATUS.md)." \
      2>&1 | tail -1 || echo "could not post @codex review (gh auth?)"
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

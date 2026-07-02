#!/bin/bash
# Launch ONE autonomous Fable Claude Code session for the synth-receipt showcase
# frontend, as a detached `screen` that (a) survives ssh/laptop disconnect and
# (b) shows up by name ("fable-showcase") in the Claude phone app for steering.
#
# Reuses the portfolio-remote-control skill's PTY primer (the fiddly part:
# clearing the bypass prompt + bracket-pasting the mission). Single session, so
# it does NOT touch the merchant-intel/font-render/orchestration trio.
#
# Run on the mini:  bash scripts/launch_fable_showcase.sh
set -euo pipefail

CLAUDE_BIN="${CLAUDE_BIN:-/opt/homebrew/bin/claude}"
MODEL="${FABLE_MODEL:-claude-fable-5}"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-$HOME/Portfolio}"
BRANCH="feat/synth-receipt-showcase"
WORKTREE="${WORKTREE:-$PORTFOLIO_ROOT/.claude/worktrees/fable-showcase}"
SCREEN_NAME="claude-fable-showcase-rc"
RC_NAME="fable-showcase"
BASE_DIR="/tmp/claude-remote-screen/fable-showcase"
PRIMER="$WORKTREE/.claude/skills/portfolio-remote-control/scripts/prime_claude_screen.py"
CAFFEINATE_SECONDS="${CAFFEINATE_SECONDS:-86400}"

MISSION="Read docs/SYNTH_SHOWCASE_TASK.md in this worktree first -- it is the complete, self-contained brief (the story, the committed showcase/*.webp assets, exactly what UI to build, and the done-criteria). Build the frontend-only \"growing the training set\" visualization it describes, working autonomously. Setup: cd portfolio && npm ci. Implement the interactive piece using the committed assets (do NOT run Python/synthesis/AWS/glyph code). Keep 'npm run lint && npm test' and typecheck green and add a component test. When it works, commit, push, open a PR, then run the @codex review loop and address findings until clean. Begin with npm ci and reading the task doc now."

echo "== preflight =="
[[ -x "$CLAUDE_BIN" ]] || { echo "FATAL: no claude binary at $CLAUDE_BIN"; exit 1; }
command -v screen >/dev/null || { echo "FATAL: screen not installed"; exit 1; }
command -v node   >/dev/null || { echo "FATAL: node not installed"; exit 1; }

# Worktree on the right branch, up to date.
git -C "$PORTFOLIO_ROOT" fetch origin "$BRANCH"
if [[ ! -d "$WORKTREE" ]]; then
  echo "Creating worktree $WORKTREE on $BRANCH"
  git -C "$PORTFOLIO_ROOT" worktree add "$WORKTREE" "$BRANCH"
else
  git -C "$WORKTREE" checkout "$BRANCH"
  git -C "$WORKTREE" pull --ff-only origin "$BRANCH" || true
fi
[[ -f "$WORKTREE/docs/SYNTH_SHOWCASE_TASK.md" ]] || { echo "FATAL: task doc missing on branch"; exit 1; }
[[ -f "$PRIMER" ]] || { echo "FATAL: primer missing at $PRIMER"; exit 1; }

# Push auth -- an autonomous agent that can't push is useless.
if ! gh auth status -h github.com >/dev/null 2>&1; then
  echo "WARNING: gh not authenticated on the mini; the session won't be able to push/open a PR."
fi
git -C "$WORKTREE" push --dry-run origin "HEAD:refs/heads/$BRANCH" >/dev/null 2>&1 \
  || echo "WARNING: dry-run push failed; fix remote/auth before it tries to push."

echo "== launch =="
mkdir -p "$BASE_DIR"; rm -f "$BASE_DIR/screenlog.0"
screen -S "$SCREEN_NAME" -X quit >/dev/null 2>&1 || true
( cd "$BASE_DIR"
  screen -L -dmS "$SCREEN_NAME" /bin/bash -lc \
    "cd $(printf %q "$WORKTREE") && exec $(printf %q "$CLAUDE_BIN") --permission-mode bypassPermissions --remote-control $(printf %q "$RC_NAME") --model $(printf %q "$MODEL")"
)
sleep 2

echo "== prime (clears bypass prompt, pastes mission) =="
PORTFOLIO_RC_MISSION="$MISSION" python3 "$PRIMER" --screen "$SCREEN_NAME" --label fable-showcase --timeout 180 \
  || echo "Prime failed -- attach manually: TERM=xterm-256color screen -x $SCREEN_NAME"

echo "== keep the mini awake =="
pgrep -f 'caffeinate -dimsu' >/dev/null || nohup caffeinate -dimsu -t "$CAFFEINATE_SECONDS" >/dev/null 2>&1 &

echo
echo "Launched '$RC_NAME' ($MODEL) in screen '$SCREEN_NAME'."
echo "  phone:  open the Claude app -> it appears as 'fable-showcase'"
echo "  ssh:    TERM=xterm-256color screen -x $SCREEN_NAME    (Ctrl-A d to detach)"
echo "  log:    $BASE_DIR/screenlog.0"

#!/usr/bin/env bash
# synthesis_loop/bootstrap_mini.sh — one-time setup to run the loop on the Mac mini.
# Run this ON THE MINI (GUI Terminal preferred, so the login Keychain is unlocked for
# subscription Claude auth). Idempotent.
set -euo pipefail
REPO="${REPO:-$HOME/Portfolio}"
BRANCH="${BRANCH:-feat/synthesis-hill-climb}"

echo "== 1. tools on PATH for non-interactive shells =="
grep -q '.local/bin' ~/.zshenv 2>/dev/null || cat >> ~/.zshenv <<'EOF'
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
EOF
export PATH="$HOME/.local/bin:$PATH"
which codex claude python3 git screen caffeinate

echo "== 2. Claude subscription token for headless judge (NO api key) =="
# Needed so judge_round.sh works even over SSH (Keychain is locked there).
if [ ! -f ~/.claude_batch_env ]; then
  echo "   run once interactively:  echo \"export CLAUDE_CODE_OAUTH_TOKEN=\$(claude setup-token)\" > ~/.claude_batch_env"
  echo "   (skipping — GUI-session runs can use the unlocked Keychain instead)"
fi

echo "== 3. Codex profile (autonomous, no approval prompts) =="
python3 - "$REPO" <<'PY'
import sys, os, pathlib
repo = sys.argv[1]
cfg = pathlib.Path(os.path.expanduser("~/.codex/config.toml"))
block = pathlib.Path(repo, "synthesis_loop/codex-profile.toml").read_text()
cur = cfg.read_text() if cfg.exists() else ""
if "[profiles.synthesis-loop]" not in cur:
    cfg.write_text(cur.rstrip() + "\n\n" + block)
    print("   appended synthesis-loop profile")
else:
    print("   profile already present")
PY

echo "== 4. fetch the branch =="
cd "$REPO"
git fetch origin "$BRANCH"
git rev-parse --verify "$BRANCH" >/dev/null 2>&1 || git branch "$BRANCH" "origin/$BRANCH"
echo "   checkout with:  git -C $REPO switch $BRANCH   (do this in a clean worktree if your main checkout is busy)"

echo "== 5. keep awake + launch loop in screen =="
cat <<EOF

  # keep the mini awake for the session, then launch the loop detached:
  nohup caffeinate -dimsu >/dev/null 2>&1 &
  screen -L -dmS synth-loop bash -lc 'cd $REPO && exec ./synthesis_loop/run_loop.sh'

  # watch it:        screen -r synth-loop        (detach: ctrl-a d)
  # tail the log:    tail -f ~/screenlog.0
  # stop it:         screen -S synth-loop -X quit ; pkill caffeinate
EOF
echo "bootstrap complete."

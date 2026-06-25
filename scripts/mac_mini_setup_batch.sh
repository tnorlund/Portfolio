#!/usr/bin/env bash
# One-shot Mac Mini setup for the subscription (LOCAL=1) batch run. Run from the
# MacBook (git + scp only — no claude auth needed). It:
#   1. adds a git worktree for the feature branch at ~/synth-batch, so the OCR
#      worker on ~/Portfolio is left untouched;
#   2. removes a PLACEHOLDER ~/.claude_batch_env only (a real subscription token
#      or API key is left in place) so the GUI-session subscription can be used;
#   3. copies per-merchant grouped exports into ~/synth-batch/exports.
#
# Usage:
#   EXPORTS_SRC=/path/to/grouped ./scripts/mac_mini_setup_batch.sh
#   # (omit EXPORTS_SRC to set up the worktree only and copy exports later)
set -euo pipefail

REMOTE="${REMOTE:-tnorlund@192.168.0.147}"
BRANCH="${BRANCH:-fix/synthesis-geometry-loader}"
WORKTREE_NAME="${WORKTREE_NAME:-synth-batch}"
EXPORTS_SRC="${EXPORTS_SRC:-}"   # local dir holding grouped <merchant>.json files

# --- 1 + 2: worktree and placeholder-key removal (run as a file on the box to
# avoid nested-quoting breakage under the remote login shell) ---
TMP="$(mktemp)"
cat > "$TMP" <<REMOTE_EOF
#!/bin/bash
set -e
BR="$BRANCH"
WT="\$HOME/$WORKTREE_NAME"
echo "== worktree \$BR =="
cd "\$HOME/Portfolio"
git fetch origin "\$BR"
if git worktree list | grep -q "\$WT"; then
  git -C "\$WT" checkout "\$BR"
  git -C "\$WT" pull --ff-only origin "\$BR" || true
else
  if git show-ref --verify --quiet "refs/heads/\$BR"; then
    git worktree add -f "\$WT" "\$BR"
  else
    git worktree add --track -b "\$BR" "\$WT" "origin/\$BR"
  fi
fi
echo "   -> \$(git -C "\$WT" rev-parse --abbrev-ref HEAD) @ \$(git -C "\$WT" rev-parse --short HEAD)"
echo "== placeholder key =="
# Remove ONLY a placeholder; a real CLAUDE_CODE_OAUTH_TOKEN (subscription) or
# ANTHROPIC_API_KEY is kept so SSH-driven headless runs still authenticate.
if [ -f "\$HOME/.claude_batch_env" ] && grep -qE 'your-key|PASTE' "\$HOME/.claude_batch_env"; then
  rm -f "\$HOME/.claude_batch_env"
  echo "   removed placeholder (use LOCAL=1 in the GUI session, or add a real token)"
else
  echo "   none to remove (real token/key left in place if present)"
fi
mkdir -p "\$WT/exports"
echo "== exports dir ready: \$WT/exports =="
REMOTE_EOF

echo ">> worktree + key setup on $REMOTE ..."
scp -q "$TMP" "$REMOTE:/tmp/mac_mini_setup_batch.remote.sh"
rm -f "$TMP"
ssh "$REMOTE" 'bash /tmp/mac_mini_setup_batch.remote.sh'

# --- 3: copy grouped exports ---
echo ">> exports ..."
if [ -z "$EXPORTS_SRC" ]; then
  echo "   EXPORTS_SRC not set — skipped. Re-run with EXPORTS_SRC=/path/to/grouped,"
  echo "   or copy later:  scp /path/grouped/*.json $REMOTE:$WORKTREE_NAME/exports/"
else
  shopt -s nullglob
  files=("$EXPORTS_SRC"/*.json)
  shopt -u nullglob
  if [ "${#files[@]}" -eq 0 ]; then
    echo "   no *.json found in $EXPORTS_SRC" >&2; exit 1
  fi
  scp -q "${files[@]}" "$REMOTE:$WORKTREE_NAME/exports/"
  echo "   copied ${#files[@]} export(s) -> $REMOTE:~/$WORKTREE_NAME/exports/"
  ssh "$REMOTE" "ls -1 \$HOME/$WORKTREE_NAME/exports/*.json 2>/dev/null | sed 's#.*/##; s/^/   /'"
fi

echo
echo "Next — pick ONE auth path (both use your SUBSCRIPTION, not API billing):"
echo
echo "A) SSH-driven from the MacBook (token). Generate once on this authed machine:"
echo "     echo \"export CLAUDE_CODE_OAUTH_TOKEN=\$(claude setup-token)\" \\"
echo "       | ssh $REMOTE 'umask 077; cat > ~/.claude_batch_env'"
echo "   then run from the MacBook (no Screen Sharing needed):"
echo "     PROJECT=\$HOME/$WORKTREE_NAME EXPORTS_DIR=\$HOME/$WORKTREE_NAME/exports \\"
echo "       ./scripts/mac_mini_launch_merchants.sh \"Costco Wholesale\" \"Target\" \"Vons\""
echo
echo "B) In the Mac Mini's GUI Terminal (Screen Sharing) — unlocked Keychain, no token:"
echo "     cd ~/$WORKTREE_NAME"
echo "     LOCAL=1 ./scripts/mac_mini_mcp_smoke_test.sh        # verify subscription + MCP"
echo "     LOCAL=1 PROJECT=\$HOME/$WORKTREE_NAME EXPORTS_DIR=\$HOME/$WORKTREE_NAME/exports \\"
echo "       ./scripts/mac_mini_launch_merchants.sh \"Costco Wholesale\" \"Target\" \"Vons\""

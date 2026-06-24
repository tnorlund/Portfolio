#!/usr/bin/env bash
# One-shot Mac Mini setup for the subscription (LOCAL=1) batch run. Run from the
# MacBook (git + scp only — no claude auth needed). It:
#   1. adds a git worktree for the feature branch at ~/synth-batch, so the OCR
#      worker on ~/Portfolio is left untouched;
#   2. removes any placeholder ANTHROPIC_API_KEY file so the GUI-session
#      subscription is used instead of an API key;
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
  git worktree add "\$WT" "\$BR" 2>/dev/null || git worktree add --track -b "\$BR" "\$WT" "origin/\$BR"
fi
echo "   -> \$(git -C "\$WT" rev-parse --abbrev-ref HEAD) @ \$(git -C "\$WT" rev-parse --short HEAD)"
echo "== placeholder key =="
if [ -f "\$HOME/.claude_batch_env" ] && grep -q your-key "\$HOME/.claude_batch_env"; then
  rm -f "\$HOME/.claude_batch_env"
  echo "   removed placeholder -> subscription auth (GUI session) will be used"
else
  echo "   none to remove"
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
echo "Next — in the Mac Mini's GUI Terminal (Screen Sharing), using your SUBSCRIPTION:"
echo "  cd ~/$WORKTREE_NAME"
echo "  LOCAL=1 ./scripts/mac_mini_mcp_smoke_test.sh        # verify subscription + MCP"
echo "  LOCAL=1 PROJECT=\$HOME/$WORKTREE_NAME EXPORTS_DIR=\$HOME/$WORKTREE_NAME/exports \\"
echo "    ./scripts/mac_mini_launch_merchants.sh \"Costco Wholesale\" \"Target\" \"Vons\""

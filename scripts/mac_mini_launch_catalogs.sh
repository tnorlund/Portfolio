#!/usr/bin/env bash
# Launch one detached headless `claude -p` agent per merchant on the Mac Mini to
# research the merchant's ONLINE storefront and contribute an online_catalogs/
# <slug>.json (unlocking compose_online_catalog synthesis for that merchant).
#
# Each agent runs in its OWN git worktree on a branch synth-catalog/<slug> off
# the base branch, so parallel commits/pushes don't fight over one index. The
# worktrees are created up front (sequential, to avoid git-lock races), then the
# agents run concurrently and each commits + pushes only its own catalog file.
#
# Auth: SSH can't read the macOS Keychain, so put a SUBSCRIPTION token in
# ~/.claude_batch_env (CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`).
#
# Usage:
#   ./scripts/mac_mini_launch_catalogs.sh "Costco Wholesale" "Target" "Vons"
set -euo pipefail

REMOTE="${REMOTE:-tnorlund@192.168.0.147}"
BASE="${BASE:-fix/synthesis-geometry-loader}"
PROJECT="${PROJECT:-\$HOME/synth-batch}"     # repo on the box
EXPORTS_DIR="${EXPORTS_DIR:-\$HOME/synth-batch/exports}"

if [ "$#" -lt 1 ]; then echo "usage: $0 <merchant> [<merchant> ...]" >&2; exit 1; fi

slugify() { echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g'; }

# Build the per-merchant runner + prompt files, then an orchestrator that creates
# the worktrees and launches the agents. Everything is sent as files (heredocs
# through ssh + the box's zsh break on inline parens).
ORCH="$(mktemp)"
{
  echo '#!/bin/bash'
  echo "set -u"
  echo "BASE='$BASE'"
  echo "cd \"$PROJECT\""
  echo "git fetch origin \"\$BASE\" >/dev/null 2>&1 || true"
} > "$ORCH"

for MERCHANT in "$@"; do
  SLUG="$(slugify "$MERCHANT")"
  WT="\$HOME/cat-$SLUG"
  OUT="\$HOME/cat-$SLUG-out"
  EXPORT="$EXPORTS_DIR/$SLUG.json"
  PROMPT="You are building an ONLINE PRODUCT CATALOG for ONE merchant: \"$MERCHANT\".
Work only in this git worktree. Steps:
1. Web-research $MERCHANT's online storefront. Find 8-12 REAL products currently
   sold, each with a real current price (and a UPC when findable). Prefer items
   that plausibly appear on $MERCHANT receipts.
2. Write receipt_agent/receipt_agent/agents/label_evaluator/online_catalogs/$SLUG.json
   following online_catalogs/README.md: {\"merchant_name\":\"$MERCHANT\",
   \"source_url\":\"...\",\"entries\":[{\"name\",\"price\",\"upc\",\"taxable\"}]}.
   Mark food staples taxable:false where the jurisdiction exempts them; mark
   prepared/non-food taxable:true. Use UPPERCASE abbreviated names like a receipt.
3. Verify it unlocks compose: run
   RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1 python3.12 \\
     scripts/verify_synthetic_replay.py local-pipeline --receipt-dir <dir with $EXPORT> \\
     --artifact-output-dir ./.tmp/cat-$SLUG --bundle-output ./.tmp/cat-$SLUG.json \\
     --min-grounded-candidate-share 0.0 --max-per-merchant 20 --max-per-merchant-operation 6
   and confirm compose_online_catalog candidates appear in the bundle. If zero,
   check the contract's compose_online_catalog readiness (needs a stable tax rate
   + the catalog) and report why.
4. Commit ONLY online_catalogs/$SLUG.json on this branch and push it:
   git add receipt_agent/receipt_agent/agents/label_evaluator/online_catalogs/$SLUG.json
   git commit -m 'feat(catalog): $MERCHANT online catalog for compose_online_catalog'
   git push -u origin synth-catalog/$SLUG
5. Write $OUT/REPORT.md: catalog size, source URLs, accepted compose_online count,
   and any rejection reasons."

  PF="$(mktemp)"; printf '%s' "$PROMPT" > "$PF"
  RF="$(mktemp)"
  cat > "$RF" <<RUNNER
#!/bin/bash
export PATH="\$HOME/.local/bin:\$PATH" RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1
[ -f "\$HOME/.claude_batch_env" ] && . "\$HOME/.claude_batch_env"
mkdir -p "$OUT"
echo "STARTED \$(date +%H:%M:%S)" > "$OUT/job.log"
cd "$WT"
claude -p "\$(cat /tmp/cat-prompt-$SLUG.txt)" \\
  --permission-mode bypassPermissions --output-format text < /dev/null \\
  >> "$OUT/job.log" 2>&1
echo DONE >> "$OUT/job.log"
RUNNER
  scp -q "$PF" "$REMOTE:/tmp/cat-prompt-$SLUG.txt"
  scp -q "$RF" "$REMOTE:/tmp/cat-run-$SLUG.sh"
  rm -f "$PF" "$RF"

  # Orchestrator: create the worktree (sequential), then launch the agent.
  {
    echo "if ! git worktree list | grep -q \"$WT\"; then"
    echo "  git worktree add -B synth-catalog/$SLUG \"$WT\" \"origin/\$BASE\" >/dev/null 2>&1 || \\"
    echo "  git worktree add \"$WT\" \"origin/\$BASE\" >/dev/null 2>&1"
    echo "fi"
    echo "bash /tmp/cat-run-$SLUG.sh &"
    echo "echo \"  spawned $SLUG pid \$!\""
  } >> "$ORCH"
done

echo "wait" >> "$ORCH"
scp -q "$ORCH" "$REMOTE:/tmp/cat-orchestrator.sh"
rm -f "$ORCH"

echo ">> launching catalog agents on $REMOTE (each in its own worktree/branch) ..."
ssh "$REMOTE" "bash -lc 'nohup bash /tmp/cat-orchestrator.sh >/tmp/cat-orchestrator.log 2>&1 </dev/null & disown; echo \"  orchestrator pid \$!\"'"
echo
echo "Monitor:"
echo "  ssh $REMOTE 'cat /tmp/cat-orchestrator.log'"
echo "  ssh $REMOTE 'for d in \$HOME/cat-*-out; do echo == \$d; tail -2 \$d/job.log; done'"
echo "  git branch -r | grep synth-catalog   # branches as they are pushed"

#!/usr/bin/env bash
# Launch one detached headless `claude -p` job per merchant on the Mac Mini.
# Each job: audit footer labels -> apply safe flips (live MCP) + mirror to its
# local export -> review ambiguous payment labels -> run isolated synthesis ->
# write a REPORT.md. Jobs are fully isolated (own results dir, own export copy);
# label writes are naturally scoped to each merchant's receipts.
#
# PREREQUISITES (verify first with mac_mini_mcp_smoke_test.sh):
#   - Mac Mini has the repo on branch fix/synthesis-geometry-loader
#   - AWS creds + receipt-tools MCP reachable by headless `claude`
#   - Per-merchant grouped exports at $EXPORTS_DIR/<slug>.json
#
# Usage:
#   ./scripts/mac_mini_launch_merchants.sh "Costco Wholesale" "Target" "Vons"
set -euo pipefail

REMOTE="${REMOTE:-tnorlund@192.168.0.147}"
PROJECT="${PROJECT:-\$HOME/Portfolio}"          # repo path ON the Mac Mini
EXPORTS_DIR="${EXPORTS_DIR:-\$HOME/Portfolio/exports}"  # per-merchant grouped JSON

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <merchant> [<merchant> ...]" >&2; exit 1
fi

slugify() { echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g'; }

for MERCHANT in "$@"; do
  SLUG="$(slugify "$MERCHANT")"
  OUTDIR="$PROJECT/results/$SLUG"
  EXPORT="$OUTDIR/grouped/$SLUG.json"

  # Per-merchant job prompt (paths/merchant already substituted).
  PROMPT="You are processing synthetic-receipt augmentation for ONE merchant: \"$MERCHANT\".
Constraints: work ONLY on this merchant; put ALL outputs under $OUTDIR; no paid APIs
(RECEIPT_AGENT_DISABLE_PAID_LLM and DISABLE_PAID_LLM are set); do NOT start SageMaker,
Step Functions, or any cloud/paid job.

1. Audit footer labels:
   python3.12 scripts/audit_footer_labels.py --merchant-file '$EXPORT' --out '$OUTDIR/flips.json'

2. Apply the cleanup:
   - Apply EVERY entry in flips.json 'auto_safe' to the LIVE table via the receipt-tools MCP
     update_word_label (new_status INVALID; reasoning = the entry's 'pattern'). These are safe
     (store/warehouse numbers and arithmetically-confirmed footer items).
   - For each 'review_payment' entry: inspect with get_receipt_words for the line's text context and
     use the 'merchant_location' website to decide. Flip to INVALID ONLY if it is clearly promo/survey
     text, NOT a tender actually used to pay (a 'Shop Card' / 'Credit Card' used as payment STAYS VALID).
     Be conservative; when unsure, keep it VALID.
   - Mirror the applied flips into the local export so synthesis sees the cleaned data:
     python3.12 scripts/apply_label_flips.py --flips '$OUTDIR/flips.json' --export '$EXPORT'

3. Synthesize (isolated to this merchant):
   python3.12 scripts/verify_synthetic_replay.py local-pipeline \
     --receipt-dir '$OUTDIR/grouped' --artifact-output-dir '$OUTDIR/artifacts' \
     --bundle-output '$OUTDIR/bundle.json' --min-grounded-candidate-share 0.0

4. Write '$OUTDIR/REPORT.md' summarizing: auto_safe flips applied; review_payment kept-vs-flipped with
   reasons; whether this merchant used the compose or the edit path (read the contract
   supported_operations in bundle.json — compose_online_catalog needs a stable observed tax rate);
   bundle 'ready'; accepted synthetic row count; accepted_operation_counts; and any rejection_reasons."

  echo ">> Launching $MERCHANT (session synth-$SLUG)"
  ssh "$REMOTE" bash -lc "
    cd $PROJECT
    mkdir -p '$OUTDIR/grouped' '$OUTDIR/artifacts'
    cp '$EXPORTS_DIR/$SLUG.json' '$EXPORT'
    tmux new-session -d -s 'synth-$SLUG' \
      'export PATH=\"\$HOME/.local/bin:\$PATH\" RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1;
       [ -f \"\$HOME/.claude_batch_env\" ] && . \"\$HOME/.claude_batch_env\";
       claude -p \"\$(cat <<'PROMPT_EOF'
$PROMPT
PROMPT_EOF
)\" --permission-mode bypassPermissions --output-format text
         > \"$OUTDIR/job.log\" 2>&1;
       echo DONE >> \"$OUTDIR/job.log\"'
  "
  sleep 1
done

echo
echo "All jobs launched. Monitor from this machine:"
echo "  ssh $REMOTE 'tmux ls'"
echo "  ssh $REMOTE 'tail -n 40 $PROJECT/results/<slug>/job.log'"
echo "  ssh $REMOTE 'for d in $PROJECT/results/*/; do echo \"== \$d\"; tail -1 \"\$d/job.log\"; done'"
echo "Collect results:  scp -r $REMOTE:$PROJECT/results ./batch-results"

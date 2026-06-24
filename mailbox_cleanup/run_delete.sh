#!/usr/bin/env bash
# Drive delete_by_sender.applescript over a list of senders.
#
#   delete_list.txt   one sender (substring) per line; '#' starts a comment.
#
# DRY RUN BY DEFAULT -- only counts matches, deletes nothing.
# To actually move matches to Trash, pass --live:
#
#   ./run_delete.sh delete_list.txt            # dry run (count only)
#   ./run_delete.sh delete_list.txt --live     # move matches to Trash
#
# Nothing is permanently erased: matches go to Mail.app's Trash, which you can
# review and restore. Emptying Trash is a separate manual action.

set -euo pipefail

LIST="${1:-delete_list.txt}"
MODE="count"
[[ "${2:-}" == "--live" ]] && MODE="delete"

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/delete_by_sender.applescript"
mkdir -p "$HERE/logs"
LOG="$HERE/logs/delete-$(date +%Y%m%d-%H%M%S).log"

if [[ ! -f "$LIST" ]]; then
  echo "No such list file: $LIST" >&2
  exit 1
fi

if [[ "$MODE" == "count" ]]; then
  echo ">>> DRY RUN (counting only). Re-run with --live to trash matches."
else
  echo ">>> LIVE: matching messages will be moved to Trash."
fi
echo ">>> log: $LOG"
echo

total=0
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%%#*}"                      # strip comments
  line="$(echo "$line" | xargs || true)"  # trim whitespace
  [[ -z "$line" ]] && continue
  result="$(osascript "$SCRIPT" "$MODE" "$line")"
  echo "$result" | tee -a "$LOG"
  n="$(echo "$result" | sed -n 's/.*matched=\([0-9]*\).*/\1/p')"
  total=$(( total + ${n:-0} ))
done < "$LIST"

echo
echo ">>> total matched across list: $total"
[[ "$MODE" == "count" ]] && echo ">>> nothing deleted (dry run)."
